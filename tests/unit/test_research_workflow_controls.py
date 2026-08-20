"""Unit coverage for governed workflow state, review, and publication controls."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.principal import Principal
from app.db.models import ReviewDecision, ReviewTask, WorkflowRun
from workflows.research_report import (
    ResearchReportWorkflow,
    WorkflowLimitError,
    WorkflowNodeExecutionError,
    _contract_fields,
    _summarize_context,
    _workflow_cost_summary,
)


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *, scalar_values=(), execute_values=()) -> None:
        self.scalar_values = list(scalar_values)
        self.execute_values = list(execute_values)
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0

    def add(self, value) -> None:
        self.added.append(value)

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def execute(self, _statement):
        value = self.execute_values.pop(0) if self.execute_values else None
        return _ScalarResult(value)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


def _run(*, status: str = "RUNNING", context: dict | None = None) -> WorkflowRun:
    now = datetime.now(UTC)
    return WorkflowRun(
        id=uuid.uuid4(),
        user_id="analyst",
        tenant_id="tenant-a",
        business_scene="public_fund_research_report",
        idempotency_key=f"workflow-{uuid.uuid4()}",
        status=status,
        max_steps=30,
        timeout_seconds=120,
        node_timeout_seconds=30,
        cost_budget=Decimal("0.20"),
        cost_amount=Decimal("0"),
        cost_is_estimated=False,
        current_step="",
        iteration=1,
        context_json=context or {},
        started_at=now,
        error_type="",
        code_version="unit-test",
        lease_owner="worker-a",
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=30),
        attempt=1,
        next_retry_at=None,
    )


def _reviewer() -> Principal:
    return Principal(
        "reviewer",
        frozenset({"public"}),
        tenant_id="tenant-a",
        roles=frozenset({"research_reviewer"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "task_status"),
    [
        ("approve", "APPROVED"),
        ("reject", "REJECTED"),
        ("request_changes", "CHANGES_REQUESTED"),
    ],
)
async def test_human_review_decisions_use_authenticated_reviewer_identity(
    decision: str,
    task_status: str,
    monkeypatch,
):
    run = _run(status="PENDING_REVIEW", context={"request": {}})
    task = ReviewTask(
        id=uuid.uuid4(),
        workflow_run_id=run.id,
        iteration=1,
        status="PENDING",
        assigned_group="research_reviewer",
    )
    session = _Session(scalar_values=[None])
    service = ResearchReportWorkflow(session)

    class Repository:
        async def get_review(self, review_id, *, for_update=False):
            assert review_id == task.id and for_update is True
            return task

        async def get_run(self, run_id, *, for_update=False):
            assert run_id == run.id
            return run

    async def serialized(_run_id):
        return {"run_id": str(run.id), "status": run.status}

    service.repository = Repository()
    monkeypatch.setattr(service, "get_run", serialized)

    result = await service.decide(
        task.id,
        decision,
        principal=_reviewer(),
        feedback="reviewed evidence",
    )

    assert result == {"run_id": str(run.id), "status": "PENDING"}
    assert task.status == task_status
    assert run.context_json["review_decision"] == decision
    assert run.context_json["reviewer_id"] == "reviewer"
    saved = next(item for item in session.added if isinstance(item, ReviewDecision))
    assert saved.reviewer_id == "reviewer"
    assert run.lease_owner is None


@pytest.mark.asyncio
async def test_human_review_rejects_invalid_or_completed_decisions():
    service = ResearchReportWorkflow(_Session())
    with pytest.raises(ValueError, match="Unsupported review decision"):
        await service.decide(uuid.uuid4(), "skip", principal=_reviewer())

    class MissingRepository:
        async def get_review(self, _review_id, *, for_update=False):
            return None

    service.repository = MissingRepository()
    assert await service.decide(uuid.uuid4(), "approve", principal=_reviewer()) is None


@pytest.mark.asyncio
async def test_workflow_rejects_unknown_scene_and_missing_records():
    service = ResearchReportWorkflow(_Session())

    with pytest.raises(ValueError, match="Unsupported business_scene"):
        await service.start(
            {"business_scene": "automatic_trading"},
            principal=_reviewer(),
        )

    class MissingRepository:
        async def get_run(self, _run_id, *, for_update=False):
            return None

        async def get_report(self, _report_id):
            return None

    service.repository = MissingRepository()
    assert await service.get_run(uuid.uuid4()) is None
    assert await service.get_report(uuid.uuid4(), principal=_reviewer()) is None
    with pytest.raises(ValueError, match="Workflow run not found"):
        await service._require_run(uuid.uuid4())


@pytest.mark.asyncio
async def test_citation_validation_checks_reference_and_permission_before_publication():
    service = ResearchReportWorkflow(_Session())
    run = _run()
    allowed = {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "permission_level": "restricted",
        "permission_groups": ["deal_team"],
    }
    context = {
        "request": {},
        "permission_groups": ["deal_team"],
        "evidence": [allowed],
        "draft": {"evidence_used": [{"document_id": "doc-1", "chunk_id": "chunk-1"}]},
    }

    validated = await service._execute_node("validate_citations", run, context)
    assert validated["citations_valid"] is True
    assert validated["permissions_valid"] is True

    context["draft"]["evidence_used"] = [
        {"document_id": "doc-1", "chunk_id": "invented"}
    ]
    with pytest.raises(ValueError, match="ungrounded citations"):
        await service._execute_node("validate_citations", run, context)

    context["draft"]["evidence_used"] = [
        {"document_id": "doc-1", "chunk_id": "chunk-1"}
    ]
    context["permission_groups"] = ["public"]
    with pytest.raises(PermissionError, match="permission validation"):
        await service._execute_node("validate_citations", run, context)


@pytest.mark.asyncio
async def test_compliance_and_review_nodes_fail_closed():
    service = ResearchReportWorkflow(_Session())
    run = _run()
    context = {"request": {}, "draft": {"summary": "This is research only."}}

    assert (await service._execute_node("run_compliance_rules", run, context))["rules_valid"]
    context["draft"] = {"summary": "保证收益"}
    with pytest.raises(ValueError, match="Forbidden expressions"):
        await service._execute_node("run_compliance_rules", run, context)

    context["review_decision"] = "approve"
    await service._execute_node("approve_or_reject", run, context)
    context.pop("review_decision")
    with pytest.raises(ValueError, match="Human review decision"):
        await service._execute_node("approve_or_reject", run, context)


@pytest.mark.asyncio
async def test_review_task_and_report_publication_replay_existing_rows():
    review = SimpleNamespace(id=uuid.uuid4())
    report = SimpleNamespace(id=uuid.uuid4())
    session = _Session(
        scalar_values=[review],
        execute_values=[None, None],
    )
    service = ResearchReportWorkflow(session)

    class Repository:
        async def report_for_run(self, _run_id):
            return report

    service.repository = Repository()
    run = _run()
    context = {"request": {}}

    await service._execute_node("request_human_review", run, context)
    assert context["review_id"] == str(review.id)

    context.update(
        {
            "review_decision": "approve",
            "reviewer_id": "reviewer",
            "numbers_valid": True,
            "citations_valid": True,
            "permissions_valid": True,
            "rules_valid": True,
            "draft": {"portfolio_summary": "reviewed research"},
        }
    )
    await service._execute_node("publish_report", run, context)
    assert context["report_id"] == str(report.id)

    context["review_decision"] = "reject"
    with pytest.raises(PermissionError, match="Human approval"):
        await service._execute_node("publish_report", run, context)
    context["review_decision"] = "approve"
    context["numbers_valid"] = False
    with pytest.raises(ValueError, match="Pre-publication controls"):
        await service._execute_node("publish_report", run, context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node", "decision", "expected_status", "expected_iteration"),
    [
        ("request_human_review", "", "PENDING_REVIEW", 1),
        ("approve_or_reject", "reject", "REJECTED", 1),
        ("approve_or_reject", "request_changes", "PENDING", 2),
        ("publish_report", "approve", "PUBLISHED", 1),
        ("validate_input", "", "RUNNING", 1),
    ],
)
async def test_process_next_node_applies_durable_state_transition(
    node: str,
    decision: str,
    expected_status: str,
    expected_iteration: int,
    monkeypatch,
):
    context = {"request": {}, "review_decision": decision, "review_feedback": "revise"}
    run = _run(context=context)
    session = _Session()
    service = ResearchReportWorkflow(session)

    class Repository:
        async def get_run(self, run_id, *, for_update=False):
            assert run_id == run.id and for_update is True
            return run

    async def no_limits(_run):
        return None

    async def next_node(_run, _context):
        return node

    async def run_step(_run, _node, current):
        return current

    service.repository = Repository()
    monkeypatch.setattr(service, "_check_limits", no_limits)
    monkeypatch.setattr(service, "_next_node", next_node)
    monkeypatch.setattr(service, "_run_step", run_step)

    result = await service.process_next_node(run.id, lease_owner="worker-a")

    assert result["status"] == expected_status
    assert run.iteration == expected_iteration
    if expected_status == "RUNNING":
        assert run.lease_owner == "worker-a"
        assert run.heartbeat_at is not None
    else:
        assert run.lease_owner is None


@pytest.mark.asyncio
async def test_process_next_node_rejects_missing_or_foreign_lease(monkeypatch):
    run = _run()
    service = ResearchReportWorkflow(_Session())

    class Repository:
        def __init__(self):
            self.result = None

        async def get_run(self, _run_id, *, for_update=False):
            assert for_update is True
            return self.result

    repository = Repository()
    service.repository = repository

    with pytest.raises(ValueError, match="Workflow run not found"):
        await service.process_next_node(run.id, lease_owner="worker-a")

    repository.result = run
    with pytest.raises(ValueError, match="lease is not owned"):
        await service.process_next_node(run.id, lease_owner="worker-b")

    async def no_limits(_run):
        return None

    async def no_next_node(_run, _context):
        return None

    converged = False

    async def converge(current_run, _context):
        nonlocal converged
        converged = True
        current_run.status = "PENDING_REVIEW"

    monkeypatch.setattr(service, "_check_limits", no_limits)
    monkeypatch.setattr(service, "_next_node", no_next_node)
    monkeypatch.setattr(service, "_converge_completed_run", converge)
    result = await service.process_next_node(run.id, lease_owner="worker-a")

    assert converged is True
    assert result["node"] is None
    assert result["status"] == "PENDING_REVIEW"

    run.status = "RUNNING"
    run.lease_owner = "worker-a"

    async def next_node(_run, _context):
        return "validate_input"

    async def fail_step(current_run, _node, current_context):
        current_run.status = "FAILED"
        return current_context

    monkeypatch.setattr(service, "_next_node", next_node)
    monkeypatch.setattr(service, "_run_step", fail_step)
    result = await service.process_next_node(run.id, lease_owner="worker-a")

    assert result == {
        "run_id": str(run.id),
        "status": "FAILED",
        "node": "validate_input",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_iteration"),
    [
        ("approve", "PUBLISHED", 1),
        ("reject", "REJECTED", 1),
        ("request_changes", "PENDING", 2),
        ("", "PENDING_REVIEW", 1),
    ],
)
async def test_completed_workflow_converges_from_persisted_review_state(
    decision: str,
    expected_status: str,
    expected_iteration: int,
):
    context = {
        "request": {},
        "review_decision": decision,
        "review_feedback": "clarify evidence",
        "reviewer_id": "reviewer",
    }
    run = _run(context=context)
    service = ResearchReportWorkflow(_Session())

    await service._converge_completed_run(run, context)

    assert run.status == expected_status
    assert run.iteration == expected_iteration
    assert run.lease_owner is None
    if decision == "request_changes":
        assert context["revision_feedback"] == "clarify evidence"
        assert "review_decision" not in context


@pytest.mark.asyncio
async def test_workflow_retry_and_limit_failures_are_explicit():
    retry_run = _run()
    retry_run.attempt = 1
    session = _Session(scalar_values=[31])
    service = ResearchReportWorkflow(session)

    class Repository:
        async def get_run(self, _run_id, *, for_update=False):
            return retry_run

    service.repository = Repository()
    await service.mark_retry_or_failed(
        retry_run.id,
        lease_owner="worker-a",
        error_type="ProviderError",
    )
    assert retry_run.status == "RETRY"
    assert retry_run.next_retry_at is not None

    retry_run.status = "RUNNING"
    retry_run.lease_owner = "worker-a"
    await service.mark_retry_or_failed(
        retry_run.id,
        lease_owner="worker-a",
        error_type="ValidationError",
        terminal=True,
    )
    assert retry_run.status == "FAILED"
    assert retry_run.completed_at is not None

    with pytest.raises(WorkflowLimitError, match="max_steps"):
        await service._check_limits(_run())

    budget_session = _Session(scalar_values=[0])
    budget_service = ResearchReportWorkflow(budget_session)
    over_budget = _run()
    over_budget.cost_amount = Decimal("1")
    with pytest.raises(WorkflowLimitError, match="cost budget"):
        await budget_service._check_limits(over_budget)

    timeout_session = _Session(scalar_values=[0])
    timeout_service = ResearchReportWorkflow(timeout_session)
    timed_out = _run()
    timed_out.started_at = datetime.now(UTC) - timedelta(minutes=5)
    timed_out.timeout_seconds = 1
    with pytest.raises(WorkflowLimitError, match="workflow timeout"):
        await timeout_service._check_limits(timed_out)


@pytest.mark.asyncio
async def test_step_execution_is_allowlisted_idempotent_and_failure_audited(monkeypatch):
    run = _run(context={"request": {}})
    session = _Session()
    service = ResearchReportWorkflow(session)

    with pytest.raises(PermissionError, match="not allowlisted"):
        await service._run_step(run, "execute_trade", run.context_json)

    completed_step = SimpleNamespace(status="COMPLETED")

    class CompletedRepository:
        async def get_step(self, *_args, **_kwargs):
            return completed_step

    service.repository = CompletedRepository()
    context = {"request": {}, "checkpoint": "unchanged"}
    assert await service._run_step(run, "validate_input", context) is context

    class MissingStepRepository:
        async def get_step(self, *_args, **_kwargs):
            return None

    service.repository = MissingStepRepository()
    session.scalar_values.append(run.max_steps)
    with pytest.raises(WorkflowLimitError, match="max_steps exceeded"):
        await service._run_step(run, "validate_input", context)

    failed_step = SimpleNamespace(
        status="RUNNING",
        input_summary={},
        output_summary={},
        started_at=None,
        completed_at=None,
        error_type="",
    )

    class FailedRepository:
        async def get_step(self, *_args, **_kwargs):
            return failed_step

    async def fail_node(_node, _run, _context):
        raise RuntimeError("provider unavailable")

    service.repository = FailedRepository()
    monkeypatch.setattr(service, "_execute_node", fail_node)

    with pytest.raises(WorkflowNodeExecutionError, match="RuntimeError"):
        await service._run_step(run, "validate_input", context)
    assert failed_step.status == "FAILED"
    assert failed_step.error_type == "RuntimeError"
    assert failed_step.output_summary == {"error": "provider unavailable"}
    assert run.error_type == "RuntimeError"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_validate_input_and_retry_ignore_non_owned_run():
    run = _run(context={"request": {}})
    service = ResearchReportWorkflow(_Session())

    result = await service._execute_node("validate_input", run, run.context_json)
    assert result["input_valid"] is True

    class Repository:
        async def get_run(self, _run_id, *, for_update=False):
            assert for_update is True
            return run

    service.repository = Repository()
    await service.mark_retry_or_failed(
        run.id,
        lease_owner="other-worker",
        error_type="ignored",
    )
    assert run.status == "RUNNING"
    assert run.error_type == ""


def test_workflow_summary_and_contract_helpers_are_deterministic():
    context = {
        "portfolio_tickers": ["AAPL"],
        "evidence": [{"chunk_id": "chunk-1"}],
        "nested": {"value": Decimal("1.2")},
    }
    first = _summarize_context(context)
    second = _summarize_context(context)
    assert first == second
    assert first["ticker_count"] == 1
    assert first["evidence_count"] == 1

    assert _workflow_cost_summary(Decimal("0.1"), 1, 0) == (Decimal("0.1"), False)
    assert _workflow_cost_summary(0, 0, 0) == (Decimal("0"), True)

    normalized = _contract_fields(
        {
            "portfolio_summary": "summary",
            "risk_score": 5,
            "main_risks": [],
            "asset_level_comments": [
                {"ticker": "AAPL", "comment": "review", "risk_level": "medium"}
            ],
            "rebalance_suggestions": [
                {"ticker": "AAPL", "action": "watch", "reason": "risk", "confidence": 0.5}
            ],
            "evidence_used": [],
            "disclaimer": "research only",
        }
    )
    assert normalized["research_observations"][0]["ticker"] == "AAPL"
    assert normalized["review_priorities"][0]["priority"] == "high"
    assert normalized["deprecated_fields"] == ["rebalance_suggestions"]
