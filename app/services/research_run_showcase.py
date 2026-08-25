"""Permission-aware read model for the research workflow showcase."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import LLMCallTrace, WorkflowRun, WorkflowStep
from app.db.repositories.governance import (
    LLMTraceRepository,
    PromptRepository,
    ResearchRepository,
    WorkflowRepository,
)


class ResearchRunShowcaseService:
    """Assemble one traceable workflow without bypassing ACL or tenant rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflows = WorkflowRepository(session)
        self.traces = LLMTraceRepository(session)
        self.prompts = PromptRepository(session)
        self.research = ResearchRepository(session)

    async def load(
        self,
        *,
        portfolio_id: uuid.UUID,
        tenant_id: str,
        principal: Principal,
        run_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        run = (
            await self.workflows.get_run(run_id)
            if run_id is not None
            else await self.workflows.latest_for_portfolio(
                portfolio_id,
                tenant_id=tenant_id,
            )
        )
        if run is None or not _run_matches_portfolio(run, portfolio_id, tenant_id):
            return None
        if not _can_read_run(principal, run):
            return None

        steps = await self.workflows.run_steps(run.id)
        reviews = await self.workflows.run_reviews(run.id)
        decisions = await self.workflows.run_review_decisions(run.id)
        report = await self.workflows.report_for_run(run.id)
        trace = await self.traces.latest_for_run(run.id)
        prompt = None
        if trace is not None and trace.prompt_version_id is not None:
            prompt = await self.prompts.get_version_with_template(trace.prompt_version_id)

        evidence_ids = list(trace.evidence_ids) if trace is not None else []
        parsed_evidence_ids = _valid_uuids(evidence_ids)
        evidence_as_of = trace.data_as_of.date() if trace and trace.data_as_of else date.today()
        visible_evidence = await self.research.cited_chunks(
            parsed_evidence_ids,
            groups=principal.permission_groups,
            as_of=evidence_as_of,
        )
        truth_labels = _truth_labels(run, trace)
        evidence = _evidence_payload(evidence_ids, visible_evidence)

        return {
            "portfolio_id": str(portfolio_id),
            "run": _run_payload(run),
            "truth_labels": truth_labels,
            "evidence": evidence,
            "prompt": _prompt_payload(prompt),
            "trace": _trace_payload(trace),
            "validation": _validation_payload(steps, trace, evidence),
            "review_timeline": _review_payload(
                reviews,
                decisions,
                synthetic=truth_labels.get("synthetic_data_used") is True,
            ),
            "published_report": _report_payload(report),
            "lineage": {
                "workflow_run_id": str(run.id),
                "prompt_version_id": (
                    str(trace.prompt_version_id)
                    if trace is not None and trace.prompt_version_id is not None
                    else None
                ),
                "llm_trace_id": str(trace.id) if trace is not None else None,
                "trace_key": trace.trace_key if trace is not None else None,
                "report_id": str(report.id) if report is not None else None,
                "code_version": run.code_version,
                "data_as_of": (
                    trace.data_as_of.isoformat()
                    if trace is not None and trace.data_as_of is not None
                    else None
                ),
            },
        }


def _run_matches_portfolio(
    run: WorkflowRun,
    portfolio_id: uuid.UUID,
    tenant_id: str,
) -> bool:
    context = run.context_json if isinstance(run.context_json, dict) else {}
    raw_request = context.get("request")
    request = raw_request if isinstance(raw_request, dict) else {}
    recorded_id = context.get("portfolio_id") or request.get("portfolio_id")
    return run.tenant_id == tenant_id and str(recorded_id or "") == str(portfolio_id)


def _can_read_run(principal: Principal, run: WorkflowRun) -> bool:
    if principal.user_id == run.user_id or principal.is_platform_admin:
        return True
    return principal.has_role("research_reviewer") and principal.tenant_id == run.tenant_id


def _valid_uuids(values: list[str]) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    for value in values:
        try:
            parsed.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return parsed


def _run_payload(run: WorkflowRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "business_scene": run.business_scene,
        "status": run.status,
        "iteration": run.iteration,
        "current_step": run.current_step,
        "cost_amount": float(run.cost_amount),
        "cost_is_estimated": run.cost_is_estimated,
        "error_type": run.error_type or None,
        "code_version": run.code_version,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _truth_labels(run: WorkflowRun, trace: LLMCallTrace | None) -> dict[str, bool | None]:
    context = run.context_json if isinstance(run.context_json, dict) else {}
    response = trace.response_payload if trace and isinstance(trace.response_payload, dict) else {}
    parameters = trace.model_parameters if trace and isinstance(trace.model_parameters, dict) else {}
    keys = (
        "is_demo",
        "synthetic_data_used",
        "mock_response_used",
        "real_model_used",
        "human_label_used",
        "production_data_used",
    )
    labels: dict[str, bool | None] = {}
    for key in keys:
        recorded = next(
            (
                source[key]
                for source in (response, parameters, context)
                if isinstance(source.get(key), bool)
            ),
            None,
        )
        labels[key] = recorded if isinstance(recorded, bool) else None
    return labels


def _evidence_payload(
    requested_ids: list[str],
    visible: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_count = len(requested_ids)
    visible_count = len(visible)
    if requested_count == 0 or visible_count == 0:
        status = "evidence_insufficient"
    elif visible_count < requested_count:
        status = "partial"
    else:
        status = "available"
    return {
        "status": status,
        "requested_count": requested_count,
        "visible_count": visible_count,
        "withheld_or_invalid_count": max(0, requested_count - visible_count),
        "items": visible,
    }


def _prompt_payload(
    prompt: tuple[Any, Any] | None,
) -> dict[str, Any] | None:
    if prompt is None:
        return None
    template, version = prompt
    return {
        "prompt_key": template.prompt_key,
        "name": template.name,
        "business_scene": template.business_scene,
        "version_id": str(version.id),
        "version": version.version,
        "status": version.status,
        "model": version.model,
        "temperature": float(version.temperature),
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


def _trace_payload(trace: LLMCallTrace | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    return {
        "trace_id": str(trace.id),
        "trace_key": trace.trace_key,
        "status": trace.status,
        "provider": trace.provider,
        "model": trace.model,
        "model_parameters": trace.model_parameters,
        "duration_ms": trace.duration_ms,
        "data_as_of": trace.data_as_of.isoformat() if trace.data_as_of else None,
        "code_version": trace.code_version,
        "usage_source": trace.usage_source,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "cost_amount": float(trace.cost_amount) if trace.cost_amount is not None else None,
        "cost_currency": trace.cost_currency,
        "cost_source": trace.cost_source,
        "output_schema_valid": trace.output_schema_valid,
        "fallback_used": trace.fallback_used,
        "review_decision": trace.review_decision or None,
        "review_feedback": trace.review_feedback or None,
        "error_message": trace.error_message or None,
        "response_summary": trace.response_payload,
    }


def _validation_payload(
    steps: list[WorkflowStep],
    trace: LLMCallTrace | None,
    evidence: dict[str, Any],
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if trace is None:
        records.append(_validation("schema", "unavailable", "llm_trace_missing"))
    else:
        records.append(
            _validation(
                "schema",
                "passed" if trace.output_schema_valid else "failed",
                "trace_output_schema_valid" if trace.output_schema_valid else "schema_validation_failed",
            )
        )

    numeric_step = _find_step(steps, "validate_numbers", "validate_analysis")
    numeric_result = _step_boolean(
        numeric_step,
        "numeric_consistency",
        "numeric_consistency_valid",
        "numbers_valid",
    )
    records.append(
        _validation_from_optional(
            "numeric_consistency",
            numeric_result,
            "not_recorded_by_workflow",
        )
    )

    evidence_status = str(evidence.get("status") or "evidence_insufficient")
    if evidence_status == "available":
        records.append(
            _validation(
                "citation_reference_validity",
                "passed",
                "all_trace_chunk_references_are_visible_and_valid",
            )
        )
    else:
        records.append(
            _validation(
                "citation_reference_validity",
                "failed" if evidence_status == "evidence_insufficient" else "warning",
                evidence_status,
            )
        )

    compliance_step = _find_step(steps, "run_compliance_rules")
    compliance_result = _step_boolean(
        compliance_step,
        "passed",
        "compliance_valid",
        "rules_passed",
    )
    records.append(
        _validation_from_optional(
            "compliance_rules",
            compliance_result,
            "not_recorded_by_workflow",
        )
    )
    return records


def _validation(key: str, status: str, reason: str) -> dict[str, str]:
    return {"key": key, "status": status, "reason": reason}


def _validation_from_optional(
    key: str,
    value: bool | None,
    unavailable_reason: str,
) -> dict[str, str]:
    if value is None:
        return _validation(key, "unavailable", unavailable_reason)
    return _validation(key, "passed" if value else "failed", "recorded_by_workflow")


def _find_step(steps: list[WorkflowStep], *names: str) -> WorkflowStep | None:
    return next((step for step in reversed(steps) if step.step_name in names), None)


def _step_boolean(step: WorkflowStep | None, *keys: str) -> bool | None:
    if step is None or not isinstance(step.output_summary, dict):
        return None
    for key in keys:
        value = step.output_summary.get(key)
        if isinstance(value, bool):
            return value
    return None


def _review_payload(
    reviews: list[Any],
    decisions: list[Any],
    *,
    synthetic: bool,
) -> list[dict[str, Any]]:
    by_task = {decision.review_task_id: decision for decision in decisions}
    return [
        {
            "review_id": str(review.id),
            "iteration": review.iteration,
            "status": review.status,
            "assigned_group": review.assigned_group,
            "created_at": review.created_at.isoformat(),
            "completed_at": review.completed_at.isoformat() if review.completed_at else None,
            "decision": by_task[review.id].decision if review.id in by_task else None,
            "reviewer_id": by_task[review.id].reviewer_id if review.id in by_task else None,
            "feedback": by_task[review.id].feedback if review.id in by_task else None,
            "identity_source": "synthetic_fixture" if synthetic else "server_principal",
        }
        for review in reviews
    ]


def _report_payload(report: Any | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "report_id": str(report.id),
        "workflow_run_id": str(report.workflow_run_id),
        "published_by": report.published_by,
        "published_at": report.published_at.isoformat(),
        "report": report.report_json,
    }
