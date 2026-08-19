"""PostgreSQL/pgvector integration coverage for governed research workflows."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.principal import Principal
from app.db.models import (
    ChunkEmbedding,
    DocumentChunk,
    LLMCallTrace,
    PromptDeployment,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    WorkflowStep,
    WorkflowRun,
)
from app.db.repositories.governance import WorkflowRepository
from app.services.research_knowledge import PostgresKnowledgeService
from app.storage.local import LocalObjectStorage
from config import settings
from prompts.registry import PromptRegistry
from services.financial_analysis import analyze_portfolio_with_llm
from services.llm import LLMRequest, LLMResponse
from workflows.research_report import (
    ResearchReportWorkflow,
    WorkflowNodeExecutionError,
)

pytestmark = pytest.mark.postgres


class RecordingEmbedder:
    provider_name = "test"
    model_name = "integration-recording-384"
    model_version = "1"
    dimensions = 384
    semantic = False

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        vectors = np.zeros((len(texts), settings.RAG_EMBEDDING_DIMENSION), dtype=np.float32)
        for index, text in enumerate(texts):
            bucket = sum(text.encode("utf-8")) % settings.RAG_EMBEDDING_DIMENSION
            vectors[index, bucket] = 1.0
        return vectors


class UsageProvider:
    provider_name = "qwen"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(self.payload),
            provider="qwen",
            model=request.model,
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "cost": 0.0123,
                "cost_currency": "USD",
            },
            usage_source="provider",
        )


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        _url(), connect_args={"server_settings": {"timezone": "UTC"}}
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.mark.asyncio
async def test_ingestion_persists_embeddings_and_hybrid_retrieval_enforces_acl(
    tmp_path: Path, monkeypatch
):
    engine, factory = await _factory()
    embedder = RecordingEmbedder()
    suffix = uuid.uuid4().hex
    monkeypatch.setattr(settings, "RAG_INGESTION_DIR", str(tmp_path))
    admin = Principal(
        "integration-admin",
        frozenset({"public", "deal_team"}),
        roles=frozenset({"knowledge_admin"}),
    )
    public = Principal("integration-public", frozenset({"public"}))
    storage = LocalObjectStorage(tmp_path, bucket="integration-research")
    document_ids: list[uuid.UUID] = []
    try:
        async with factory() as session:
            async with session.begin():
                service = PostgresKnowledgeService(
                    session,
                    embedder=embedder,
                    storage=storage,
                )
                public_job, _ = await service.queue_upload(
                    content=b"# AAPL Risk\n\nAAPL concentration and supply chain risk evidence.",
                    filename="aapl_risk.md",
                    metadata={
                        "document_id": f"public-{suffix}",
                        "title": "AAPL Public Risk",
                        "source_type": "research_report",
                        "tickers": ["AAPL"],
                        "confidentiality": "public",
                        "permission_groups": ["public"],
                    },
                    principal=admin,
                    idempotency_key=f"public-{suffix}",
                )
                assert public_job.object_key
                assert public_job.object_owner == admin.user_id
                assert public_job.object_permission_groups == ["public"]
                assert await service.read_source_object(
                    public_job,
                    principal=public,
                ) == b"# AAPL Risk\n\nAAPL concentration and supply chain risk evidence."
                await service.process_job(public_job)
                assert public_job.document_id is not None
                document_ids.append(public_job.document_id)
                await service.repository.set_published(public_job.document_id)

                draft_job, _ = await service.queue_upload(
                    content=b"# Draft Metadata\n\nUnpublished AAPL draft evidence.",
                    filename="aapl_draft.md",
                    metadata={
                        "document_id": f"public-{suffix}",
                        "title": "AAPL Unpublished Draft",
                        "source_type": "research_report",
                        "tickers": ["AAPL"],
                        "confidentiality": "public",
                        "permission_groups": ["public"],
                    },
                    principal=admin,
                    idempotency_key=f"public-draft-{suffix}",
                )
                await service.process_job(draft_job)
                public_document = await service.repository.get_document(
                    public_job.document_id,
                    public.permission_groups,
                )
                assert public_document is not None
                assert public_document.title == "AAPL Public Risk"
                assert public_document.current_version == 2
                assert public_document.published_version == 1

                secret_job, _ = await service.queue_upload(
                    content=b"# AAPL Deal\n\nAAPL restricted acquisition evidence.",
                    filename="aapl_deal.md",
                    metadata={
                        "document_id": f"secret-{suffix}",
                        "title": "AAPL Restricted Deal",
                        "source_type": "research_report",
                        "tickers": ["AAPL"],
                        "confidentiality": "restricted",
                        "permission_groups": ["deal_team"],
                    },
                    principal=admin,
                    idempotency_key=f"secret-{suffix}",
                )
                await service.process_job(secret_job)
                assert secret_job.document_id is not None
                document_ids.append(secret_job.document_id)
                await service.repository.set_published(secret_job.document_id)

                expired_job, _ = await service.queue_upload(
                    content=b"# Expired AAPL Note\n\nSuperseded concentration evidence.",
                    filename="aapl_expired.md",
                    metadata={
                        "document_id": f"expired-{suffix}",
                        "title": "Expired AAPL Note",
                        "source_type": "research_report",
                        "tickers": ["AAPL"],
                        "confidentiality": "public",
                        "permission_groups": ["public"],
                        "effective_to": (
                            datetime.now(UTC).date() - timedelta(days=1)
                        ).isoformat(),
                    },
                    principal=admin,
                    idempotency_key=f"expired-{suffix}",
                )
                await service.process_job(expired_job)
                assert expired_job.document_id is not None
                document_ids.append(expired_job.document_id)
                await service.repository.set_published(expired_job.document_id)

            document_vector_calls = len(embedder.calls)
            result = await PostgresKnowledgeService(
                session, embedder=embedder, storage=storage
            ).retrieve_with_status(
                "ticker:AAPL concentration risk",
                principal=public,
                score_threshold=0.0,
            )
            assert len(embedder.calls) == document_vector_calls + 1
            assert embedder.calls[-1] == ["AAPL concentration risk"]
            assert {item["document_key"] for item in result["citations"]} == {
                f"public-{suffix}"
            }
            assert f"expired-{suffix}" not in {
                item["document_key"] for item in result["citations"]
            }
            assert {item["version"] for item in result["citations"]} == {1}
            assert result["retrieval_backend"] == "postgresql_fts+pgvector_rrf"
            embedding_count = await session.scalar(
                select(func.count(ChunkEmbedding.id))
                .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
                .where(DocumentChunk.document_id.in_(document_ids))
            )
            assert embedding_count == 4

            await session.rollback()
            async with session.begin():
                await session.execute(
                    delete(ResearchDocument).where(ResearchDocument.id.in_(document_ids))
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prompt_trace_uses_provider_usage_and_cost_fields():
    engine, factory = await _factory()
    summary = {
        "risk_score": 5.0,
        "asset_metrics": {"AAPL": {"weight": 0.2, "risk_level": "medium"}},
        "concentration_flags": [],
    }
    payload = {
        "portfolio_summary": "Portfolio requires human risk review.",
        "risk_score": 5.0,
        "main_risks": [],
        "asset_level_comments": [
            {"ticker": "AAPL", "risk_level": "medium", "comment": "AAPL requires review."}
        ],
        "research_observations": [
            {"ticker": "AAPL", "observation": "Review AAPL risk.", "evidence_ids": []}
        ],
        "review_priorities": [
            {"priority": "medium", "ticker": "AAPL", "reason": "Review AAPL risk drivers."}
        ],
        "rebalance_suggestions": [],
        "deprecated_fields": ["rebalance_suggestions"],
        "evidence_used": [],
        "disclaimer": "Research only, not investment advice.",
    }
    trace_id = ""
    try:
        async with factory() as session:
            async with session.begin():
                result = await analyze_portfolio_with_llm(
                    summary,
                    provider=UsageProvider(payload),
                    registry=PromptRegistry(session),
                    user_id="usage-integration",
                    business_scene="usage-integration",
                )
                trace_id = result["llm_trace_id"]
                trace = await session.scalar(
                    select(LLMCallTrace).where(LLMCallTrace.trace_key == trace_id)
                )
                assert trace is not None
                assert trace.usage_source == "provider"
                assert trace.cost_source == "provider"
                assert trace.input_tokens == 100
                assert trace.output_tokens == 25
                assert float(trace.cost_amount or 0) == pytest.approx(0.0123)
                assert trace.cost_currency == "USD"

            async with session.begin():
                await session.execute(
                    delete(LLMCallTrace).where(LLMCallTrace.trace_key == trace_id)
                )
                template = await session.scalar(
                    select(PromptTemplate).where(PromptTemplate.prompt_key == "financial-analysis")
                )
                if template is not None:
                    await session.execute(
                        delete(PromptDeployment).where(PromptDeployment.prompt_id == template.id)
                    )
                    await session.execute(
                        delete(PromptVersion).where(PromptVersion.prompt_id == template.id)
                    )
                    await session.delete(template)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def _claim_workflow(
    factory: async_sessionmaker[AsyncSession],
    owner: str,
    *,
    now: datetime | None = None,
) -> uuid.UUID | None:
    async with factory.begin() as session:
        run = await WorkflowRepository(session).claim_next_run(
            lease_owner=owner,
            now=now or datetime.now(UTC),
            lease_seconds=30,
        )
        return run.id if run is not None else None


async def _process_workflow_node(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, owner: str
) -> dict[str, object]:
    async with factory() as session:
        result = await ResearchReportWorkflow(session).process_next_node(
            run_id,
            lease_owner=owner,
        )
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_workflow_crash_recovery_idempotent_trace_publish_and_review(monkeypatch):
    engine, factory = await _factory()
    suffix = uuid.uuid4().hex
    analyst = Principal(f"analyst-{suffix}", frozenset({"public"}), tenant_id="tenant-a")
    second_analyst = Principal(
        f"analyst-two-{suffix}", frozenset({"public"}), tenant_id="tenant-a"
    )
    reviewer = Principal(
        f"reviewer-{suffix}",
        frozenset({"public"}),
        tenant_id="tenant-a",
        roles=frozenset({"research_reviewer"}),
    )
    request = {
        "idempotency_key": f"same-{suffix}",
        "portfolio_id": str(uuid.uuid4()),
    }
    original_execute = ResearchReportWorkflow._execute_node
    executions: dict[str, int] = {}

    async def deterministic_node(self, node, run, context):
        executions[node] = executions.get(node, 0) + 1
        if node in {"request_human_review", "approve_or_reject", "publish_report"}:
            return await original_execute(self, node, run, context)
        if node == "validate_input":
            context["input_valid"] = True
        elif node == "load_portfolio":
            context["portfolio_summary"] = {"stocks": [], "scores": [], "num_positions": 0}
            context["portfolio_tickers"] = ["AAPL"]
            context["portfolio_lineage"] = {
                "portfolio_id": request["portfolio_id"],
                "valuation_snapshot_id": str(uuid.uuid4()),
                "valuation_input_hash": "a" * 64,
                "as_of": "2026-08-12T00:00:00+00:00",
                "valuation_status": "complete",
                "coverage_ratio": 1.0,
            }
        elif node == "calculate_risk":
            context["risk_summary"] = {
                "risk_score": 5.0,
                "asset_metrics": {
                    "AAPL": {"weight": 1.0, "risk_level": "medium"}
                },
                "concentration_flags": ["single_asset:AAPL"],
            }
        elif node == "retrieve_evidence":
            context["evidence"] = []
            context["evidence_insufficient"] = True
        elif node == "generate_draft":
            trace_key = f"workflow:{run.id}:generate_draft:{run.iteration}"
            trace = await self.session.scalar(
                select(LLMCallTrace).where(LLMCallTrace.trace_key == trace_key)
            )
            if trace is None:
                self.session.add(
                    LLMCallTrace(
                        trace_key=trace_key,
                        workflow_run_id=run.id,
                        user_id=run.user_id,
                        business_scene=run.business_scene,
                        provider="integration",
                        model="deterministic-test",
                        request_hash="b" * 64,
                        response_hash="c" * 64,
                        response_payload={"portfolio_summary": "Research draft"},
                        status="success",
                        duration_ms=1,
                        code_version="integration-test",
                        usage_source="estimated",
                        cost_source="estimated_from_text",
                    )
                )
            context["draft"] = {
                "portfolio_summary": "Research draft",
                "risk_score": 5.0,
                "main_risks": ["single_asset:AAPL"],
                "asset_level_comments": [],
                "research_observations": [],
                "review_priorities": [],
                "rebalance_suggestions": [],
                "deprecated_fields": ["rebalance_suggestions"],
                "evidence_used": [],
                "disclaimer": "Research only; not investment advice.",
            }
        elif node == "validate_numbers":
            context["numbers_valid"] = True
        elif node == "validate_citations":
            context["citations_valid"] = True
            context["permissions_valid"] = True
        elif node == "run_compliance_rules":
            context["rules_valid"] = True
        return context

    monkeypatch.setattr(ResearchReportWorkflow, "_execute_node", deterministic_node)
    run_ids: list[uuid.UUID] = []
    try:
        async with factory() as session:
            async with session.begin():
                service = ResearchReportWorkflow(session)
                first = await service.start(request, principal=analyst)
                replay = await service.start(request, principal=analyst)
                scoped = await service.start(request, principal=second_analyst)
                assert first["run_id"] == replay["run_id"]
                assert scoped["run_id"] != first["run_id"]
                assert first["status"] == "PENDING"
                assert first["steps"] == []
                assert first["review_tasks"] == []
                run_ids = [uuid.UUID(first["run_id"])]
                scoped_run = await session.get(WorkflowRun, uuid.UUID(scoped["run_id"]))
                assert scoped_run is not None
                await session.delete(scoped_run)

        owner_one = f"worker-one-{suffix}"
        owner_two = f"worker-two-{suffix}"
        assert await _claim_workflow(factory, owner_one) == run_ids[0]
        assert (await _process_workflow_node(factory, run_ids[0], owner_one))["node"] == (
            "validate_input"
        )

        # Simulate a process crash after the first committed node. The lease is
        # made stale, then another worker claims the same durable run.
        async with factory.begin() as session:
            run = await session.get(WorkflowRun, run_ids[0], with_for_update=True)
            assert run is not None
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await _claim_workflow(factory, owner_two) == run_ids[0]

        result: dict[str, object] = {"status": "RUNNING"}
        while result["status"] == "RUNNING":
            result = await _process_workflow_node(factory, run_ids[0], owner_two)
        assert result["status"] == "PENDING_REVIEW"
        assert executions["validate_input"] == 1
        assert executions["generate_draft"] == 1

        async with factory() as session:
            service = ResearchReportWorkflow(session)
            pending = await service.get_run(run_ids[0])
            assert pending is not None
            persisted_run = await session.get(WorkflowRun, run_ids[0])
            assert persisted_run is not None
            assert all(
                persisted_run.context_json.get(flag)
                for flag in (
                    "numbers_valid",
                    "citations_valid",
                    "permissions_valid",
                    "rules_valid",
                )
            )
            review_id = uuid.UUID(pending["review_tasks"][0]["review_id"])
            trace_count = await session.scalar(
                select(func.count(LLMCallTrace.id)).where(
                    LLMCallTrace.workflow_run_id == run_ids[0]
                )
            )
            assert trace_count == 1

        async def approve_once() -> str:
            try:
                async with factory() as session:
                    async with session.begin():
                        await ResearchReportWorkflow(session).decide(
                            review_id,
                            "approve",
                            principal=reviewer,
                            feedback="Authenticated reviewer approval",
                        )
                return "approved"
            except (ValueError, IntegrityError):
                return "conflict"

        approvals = await asyncio.gather(approve_once(), approve_once())
        assert sorted(approvals) == ["approved", "conflict"]

        owner_three = f"worker-three-{suffix}"
        assert await _claim_workflow(factory, owner_three) == run_ids[0]
        result = {"status": "RUNNING"}
        while result["status"] == "RUNNING":
            result = await _process_workflow_node(factory, run_ids[0], owner_three)
        assert result["status"] == "PUBLISHED"

        # Recreate the narrow checkpoint ambiguity where publication exists but
        # the publish step was not marked complete. The unique report key makes
        # replay converge on the original report instead of duplicating it.
        async with factory.begin() as session:
            run = await session.get(WorkflowRun, run_ids[0], with_for_update=True)
            publish_step = await session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_run_id == run_ids[0],
                    WorkflowStep.step_name == "publish_report",
                )
            )
            assert run is not None and publish_step is not None
            run.status = "RUNNING"
            run.lease_owner = "crashed-publisher"
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            publish_step.status = "RUNNING"
            publish_step.completed_at = None
        owner_four = f"worker-four-{suffix}"
        assert await _claim_workflow(factory, owner_four) == run_ids[0]
        replayed_publish = await _process_workflow_node(factory, run_ids[0], owner_four)
        assert replayed_publish["status"] == "PUBLISHED"

        async with factory() as session:
            decision = await session.scalar(
                select(ReviewDecision).where(
                    ReviewDecision.workflow_run_id == run_ids[0]
                )
            )
            assert decision is not None and decision.reviewer_id == reviewer.user_id
            assert await session.scalar(
                select(func.count(PublishedReport.id)).where(
                    PublishedReport.workflow_run_id == run_ids[0]
                )
            ) == 1
            assert await session.scalar(
                select(func.count(LLMCallTrace.id)).where(
                    LLMCallTrace.workflow_run_id == run_ids[0]
                )
            ) == 1
            report = await session.scalar(
                select(PublishedReport).where(PublishedReport.workflow_run_id == run_ids[0])
            )
            assert report is not None
            service = ResearchReportWorkflow(session)
            assert await service.get_report(report.id, principal=analyst) is not None
            assert await service.get_report(report.id, principal=second_analyst) is None
            assert await service.get_report(report.id, principal=reviewer) is not None

        async with factory.begin() as session:
            await session.execute(
                delete(LLMCallTrace).where(
                    LLMCallTrace.user_id.in_([analyst.user_id, second_analyst.user_id])
                )
            )
            await session.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workflow_node_timeout_cancels_running_node(monkeypatch):
    engine, factory = await _factory()
    suffix = uuid.uuid4().hex
    principal = Principal(f"timeout-{suffix}", frozenset({"public"}))

    async def slow_node(self, node, run, context):
        await asyncio.sleep(1.2)
        return context

    monkeypatch.setattr(ResearchReportWorkflow, "_execute_node", slow_node)
    run_id: uuid.UUID | None = None
    try:
        async with factory() as session:
            async with session.begin():
                queued = await ResearchReportWorkflow(session).start(
                    {
                        "idempotency_key": f"timeout-{suffix}",
                        "node_timeout_seconds": 1,
                        "timeout_seconds": 5,
                    },
                    principal=principal,
                )
                run_id = uuid.UUID(queued["run_id"])
                assert queued["status"] == "PENDING"

        owner = f"timeout-worker-{suffix}"
        assert await _claim_workflow(factory, owner) == run_id
        with pytest.raises(WorkflowNodeExecutionError, match="NodeTimeoutError"):
            await _process_workflow_node(factory, run_id, owner)
        async with factory.begin() as session:
            await ResearchReportWorkflow(session).mark_retry_or_failed(
                run_id,
                lease_owner=owner,
                error_type="NodeTimeoutError",
            )
        async with factory() as session:
            result = await ResearchReportWorkflow(session).get_run(run_id)
            assert result is not None
            assert result["status"] == "RETRY"
            assert result["error_type"] == "NodeTimeoutError"
            assert result["steps"][0]["status"] == "FAILED"
        async with factory.begin() as session:
            await session.execute(delete(WorkflowRun).where(WorkflowRun.id == run_id))
    finally:
        await engine.dispose()
