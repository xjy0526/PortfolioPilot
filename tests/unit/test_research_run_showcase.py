"""Unit guards for Showcase truth labels and explicit unavailable states."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.principal import Principal
from app.db.models import LLMCallTrace, WorkflowRun
from app.services.research_run_showcase import (
    ResearchRunShowcaseService,
    _can_read_run,
    _evidence_payload,
    _truth_labels,
    _validation_payload,
)


def _principal(*, user_id: str = "showcase-owner", roles: set[str] | None = None) -> Principal:
    return Principal(
        user_id=user_id,
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id="tenant-a",
        roles=frozenset(roles or set()),
    )


def test_evidence_insufficient_is_explicit_without_fabricated_scores() -> None:
    payload = _evidence_payload(["missing-chunk"], [])

    assert payload == {
        "status": "evidence_insufficient",
        "requested_count": 1,
        "visible_count": 0,
        "withheld_or_invalid_count": 1,
        "items": [],
    }


def test_truth_labels_only_use_persisted_boolean_values() -> None:
    run = WorkflowRun(
        context_json={
            "synthetic_data_used": True,
            "production_data_used": "false",
        }
    )
    trace = LLMCallTrace(
        response_payload={
            "mock_response_used": True,
            "real_model_used": False,
        },
        model_parameters={},
    )

    labels = _truth_labels(run, trace)

    assert labels["synthetic_data_used"] is True
    assert labels["mock_response_used"] is True
    assert labels["real_model_used"] is False
    assert labels["production_data_used"] is None
    assert labels["human_label_used"] is None


def test_missing_validations_remain_unavailable_and_schema_failure_is_visible() -> None:
    trace = LLMCallTrace(output_schema_valid=False, evidence_ids=[])
    evidence = _evidence_payload([], [])

    validation = {
        item["key"]: item
        for item in _validation_payload([], trace, evidence)
    }

    assert validation["schema"]["status"] == "failed"
    assert validation["numeric_consistency"]["status"] == "unavailable"
    assert validation["numeric_consistency"]["reason"] == "not_recorded_by_workflow"
    assert validation["citation_reference_validity"]["status"] == "failed"
    assert validation["citation_reference_validity"]["reason"] == "evidence_insufficient"
    assert validation["compliance_rules"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_load_assembles_persisted_lineage_and_permission_filtered_evidence() -> None:
    portfolio_id = uuid.uuid4()
    run_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    report_id = uuid.uuid4()
    review_id = uuid.uuid4()
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    run = SimpleNamespace(
        id=run_id,
        tenant_id="tenant-a",
        user_id="showcase-owner",
        context_json={"portfolio_id": str(portfolio_id), "synthetic_data_used": True},
        business_scene="portfolio_research",
        status="PUBLISHED",
        iteration=2,
        current_step="publish_report",
        cost_amount=Decimal("0"),
        cost_is_estimated=True,
        error_type="",
        code_version="test-sha",
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    chunk_id = uuid.uuid4()
    trace = SimpleNamespace(
        id=trace_id,
        prompt_version_id=prompt_id,
        evidence_ids=[str(chunk_id), "not-a-uuid"],
        response_payload={"mock_response_used": True, "real_model_used": False},
        model_parameters={"human_label_used": False},
        data_as_of=now,
        trace_key="trace-key",
        status="success",
        provider="demo_fixture_model",
        model="synthetic-no-network-v1",
        duration_ms=2,
        code_version="test-sha",
        usage_source="estimated",
        input_tokens=None,
        output_tokens=None,
        cost_amount=Decimal("0"),
        cost_currency="USD",
        cost_source="synthetic_fixture_zero_cost",
        output_schema_valid=True,
        fallback_used=False,
        review_decision="approved_demo_fixture",
        review_feedback="Synthetic fixture",
        error_message="",
    )
    template = SimpleNamespace(
        prompt_key="portfolio-research",
        name="Portfolio research",
        business_scene="portfolio_research",
    )
    prompt = SimpleNamespace(
        id=prompt_id,
        version=1,
        status="published",
        model="synthetic-no-network-v1",
        temperature=Decimal("0"),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        published_at=now,
    )
    review = SimpleNamespace(
        id=review_id,
        iteration=2,
        status="COMPLETED",
        assigned_group="research_reviewer",
        created_at=now,
        completed_at=now,
    )
    decision = SimpleNamespace(
        review_task_id=review_id,
        decision="approved",
        reviewer_id="synthetic-reviewer",
        feedback="Synthetic fixture",
    )
    report = SimpleNamespace(
        id=report_id,
        workflow_run_id=run_id,
        published_by="synthetic-reviewer",
        published_at=now,
        report_json={"summary": "Persisted report"},
    )
    service = ResearchRunShowcaseService.__new__(ResearchRunShowcaseService)
    workflows = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        latest_for_portfolio=AsyncMock(return_value=run),
        run_steps=AsyncMock(return_value=[]),
        run_reviews=AsyncMock(return_value=[review]),
        run_review_decisions=AsyncMock(return_value=[decision]),
        report_for_run=AsyncMock(return_value=report),
    )
    prompts = SimpleNamespace(
        get_version_with_template=AsyncMock(return_value=(template, prompt))
    )
    visible = [{"chunk_id": str(chunk_id), "quote": "Persisted evidence"}]
    cited_chunks = AsyncMock(return_value=visible)
    service_any = cast(Any, service)
    service_any.workflows = workflows
    service_any.traces = SimpleNamespace(latest_for_run=AsyncMock(return_value=trace))
    service_any.prompts = prompts
    service_any.research = SimpleNamespace(cited_chunks=cited_chunks)

    payload = await service.load(
        portfolio_id=portfolio_id,
        tenant_id="tenant-a",
        principal=_principal(),
        run_id=run_id,
    )

    assert payload is not None
    assert payload["truth_labels"]["mock_response_used"] is True
    assert payload["evidence"]["status"] == "partial"
    assert payload["lineage"] == {
        "workflow_run_id": str(run_id),
        "prompt_version_id": str(prompt_id),
        "llm_trace_id": str(trace_id),
        "trace_key": "trace-key",
        "report_id": str(report_id),
        "code_version": "test-sha",
        "data_as_of": now.isoformat(),
    }
    assert payload["review_timeline"][0]["identity_source"] == "synthetic_fixture"
    cited_chunks.assert_awaited_once_with(
        [chunk_id],
        groups=frozenset({"public"}),
        as_of=now.date(),
    )


@pytest.mark.asyncio
async def test_load_fails_closed_for_missing_or_inaccessible_run() -> None:
    portfolio_id = uuid.uuid4()
    service = ResearchRunShowcaseService.__new__(ResearchRunShowcaseService)
    workflows = SimpleNamespace(
        get_run=AsyncMock(return_value=None),
        latest_for_portfolio=AsyncMock(return_value=None),
    )
    cast(Any, service).workflows = workflows

    assert await service.load(
        portfolio_id=portfolio_id,
        tenant_id="tenant-a",
        principal=_principal(),
    ) is None

    run = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        user_id="another-user",
        context_json={"request": {"portfolio_id": str(portfolio_id)}},
    )
    workflows.latest_for_portfolio.return_value = run
    assert await service.load(
        portfolio_id=portfolio_id,
        tenant_id="tenant-a",
        principal=_principal(),
    ) is None
    assert _can_read_run(_principal(roles={"research_reviewer"}), cast(Any, run)) is True
    assert _can_read_run(_principal(user_id="viewer"), cast(Any, run)) is False
