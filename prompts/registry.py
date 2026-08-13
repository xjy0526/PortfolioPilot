"""Async PostgreSQL Prompt Registry and LLM trace persistence."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.governance import (
    LLMCallTrace,
    PromptDeployment as PromptDeploymentRow,
    PromptTemplate as PromptTemplateRow,
    PromptVersion as PromptVersionRow,
)
from app.db.repositories.governance import PromptRepository
from config import settings
from prompts.registry_models import (
    PromptDeployment,
    PromptEvaluationResult,
    PromptTemplate,
    PromptVersion,
)
from prompts.registry_models import PromptStatus
from time_utils import utc_now


class PromptRegistry:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = PromptRepository(session)

    async def list_prompts(self) -> list[dict[str, Any]]:
        rows = await self.repository.list_templates()
        return [_template_model(item).model_dump(mode="json") for item in rows]

    async def create_prompt(
        self, payload: dict[str, Any]
    ) -> tuple[PromptTemplate, PromptVersion]:
        prompt_key = str(payload.get("prompt_id") or uuid.uuid4())
        if await self.repository.get_template(prompt_key):
            raise ValueError(f"Prompt already exists: {prompt_key}")
        now = utc_now()
        version_model = PromptVersion(
            prompt_id=prompt_key,
            version=1,
            template=payload["template"],
            variables=payload.get("variables", []),
            input_schema=payload["input_schema"],
            output_schema=payload["output_schema"],
            model=payload.get("model", "qwen-plus"),
            temperature=payload.get("temperature", 0.2),
            owner=payload.get("owner", "Research Platform"),
            status="draft",
            change_log=payload.get("change_log", "Initial version"),
            baseline_metrics=payload.get("baseline_metrics", {}),
            created_at=now,
        )
        template_row = PromptTemplateRow(
            prompt_key=prompt_key,
            name=payload["name"],
            business_scene=payload["business_scene"],
            owner=version_model.owner,
            status="draft",
            current_version=1,
        )
        self.session.add(template_row)
        await self.session.flush()
        self.session.add(_version_row(template_row.id, version_model))
        await self.session.flush()
        return _template_model(template_row), version_model

    async def create_version(
        self, prompt_key: str, payload: dict[str, Any]
    ) -> PromptVersion | None:
        template = await self.repository.get_template(prompt_key)
        if template is None:
            return None
        latest_row = await self.repository.get_version(template.id, template.current_version)
        if latest_row is None:
            raise RuntimeError("Prompt current version is missing")
        latest = _version_model(template.prompt_key, latest_row)
        model = PromptVersion(
            prompt_id=prompt_key,
            version=template.current_version + 1,
            template=payload.get("template", latest.template),
            variables=payload.get("variables", latest.variables),
            input_schema=payload.get("input_schema", latest.input_schema),
            output_schema=payload.get("output_schema", latest.output_schema),
            model=payload.get("model", latest.model),
            temperature=payload.get("temperature", latest.temperature),
            owner=payload.get("owner", latest.owner),
            status=payload.get("status", "draft"),
            change_log=payload.get("change_log", ""),
            baseline_metrics=payload.get("baseline_metrics", latest.baseline_metrics),
        )
        self.session.add(_version_row(template.id, model))
        template.current_version = model.version
        if template.published_version is None:
            template.status = model.status
        await self.session.flush()
        return model

    async def publish(
        self,
        prompt_key: str,
        version: int,
        *,
        deployed_by: str,
        action: str = "publish",
    ) -> PromptDeployment | None:
        template = await self.repository.get_template(prompt_key)
        if template is None:
            return None
        target = await self.repository.get_version(template.id, version)
        if target is None:
            return None
        previous = template.published_version
        now = utc_now()
        await self.session.execute(
            update(PromptVersionRow)
            .where(PromptVersionRow.prompt_id == template.id, PromptVersionRow.status == "published")
            .values(status="deprecated")
        )
        target.status = "published"
        target.published_at = now
        template.status = "published"
        template.published_version = target.version
        template.published_at = now
        deployment_row = PromptDeploymentRow(
            prompt_id=template.id,
            prompt_version_id=target.id,
            previous_version=previous,
            action=action,
            environment="production",
            deployed_by=deployed_by,
        )
        self.session.add(deployment_row)
        await self.session.flush()
        return PromptDeployment(
            deployment_id=str(deployment_row.id),
            prompt_id=prompt_key,
            version=target.version,
            action="rollback" if action == "rollback" else "publish",
            previous_version=previous,
            environment=deployment_row.environment,
            created_at=deployment_row.created_at,
        )

    async def rollback(
        self,
        prompt_key: str,
        target_version: int | None = None,
        *,
        deployed_by: str,
    ) -> PromptDeployment | None:
        template = await self.repository.get_template(prompt_key)
        if template is None or template.published_version is None:
            return None
        if target_version is None:
            target_version = template.published_version - 1
        if target_version < 1:
            return None
        return await self.publish(
            prompt_key, target_version, deployed_by=deployed_by, action="rollback"
        )

    async def compare_versions(
        self,
        prompt_key: str,
        version_a: int,
        version_b: int,
        test_cases: list[dict[str, Any]],
    ) -> PromptEvaluationResult:
        first = await self.get_version(prompt_key, version_a)
        second = await self.get_version(prompt_key, version_b)
        if first is None or second is None:
            raise ValueError("Prompt version not found")
        metrics_a = _evaluate_prompt(first, test_cases)
        metrics_b = _evaluate_prompt(second, test_cases)
        keys = sorted(set(metrics_a) | set(metrics_b))
        return PromptEvaluationResult(
            evaluation_id=str(uuid.uuid4()),
            prompt_id=prompt_key,
            version_a=version_a,
            version_b=version_b,
            test_case_count=len(test_cases),
            metrics_a=metrics_a,
            metrics_b=metrics_b,
            metric_delta={key: round(metrics_b.get(key, 0.0) - metrics_a.get(key, 0.0), 6) for key in keys},
        )

    async def get_prompt(self, prompt_key: str) -> PromptTemplate | None:
        row = await self.repository.get_template(prompt_key)
        return _template_model(row) if row else None

    async def get_version(self, prompt_key: str, version: int) -> PromptVersion | None:
        template = await self.repository.get_template(prompt_key)
        if template is None:
            return None
        row = await self.repository.get_version(template.id, version)
        return _version_model(prompt_key, row) if row else None

    async def get_published_by_scene(self, business_scene: str) -> PromptVersion | None:
        result = await self.repository.published_for_scene(business_scene)
        return _version_model(result[0].prompt_key, result[1]) if result else None

    async def get_published(self, prompt_key: str) -> PromptVersion | None:
        template = await self.repository.get_template(prompt_key)
        if template is None or template.status != "published" or template.published_version is None:
            return None
        row = await self.repository.get_version(template.id, template.published_version)
        return _version_model(prompt_key, row) if row else None

    async def record_llm_call(
        self,
        *,
        trace_id: str,
        prompt_id: str,
        prompt_version: int,
        provider: str,
        model: str,
        status: str,
        run_id: str = "",
        user_id: str = "",
        business_scene: str = "",
        input_hash: str = "",
        response_text: str = "",
        response_payload: dict[str, Any] | None = None,
        data_as_of: datetime | None = None,
        retrieved_chunk_ids: list[str] | None = None,
        retrieved_document_ids: list[str] | None = None,
        latency_ms: float = 0.0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_amount: float | None = None,
        cost_currency: str = "UNK",
        provider_usage: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        usage_source: str = "estimated",
        cost_source: str = "estimated_from_text",
        output_schema_valid: bool = False,
        fallback_used: bool = False,
        review_decision: str = "",
        review_feedback: str = "",
        validation_error: str = "",
        error_type: str = "",
        **_: Any,
    ) -> None:
        template = await self.repository.get_template(prompt_id)
        version_row = (
            await self.repository.get_version(template.id, prompt_version) if template else None
        )
        workflow_id = _optional_uuid(run_id)
        trace = await self.session.scalar(
            select(LLMCallTrace).where(LLMCallTrace.trace_key == trace_id)
        )
        values = {
            "workflow_run_id": workflow_id,
            "prompt_version_id": version_row.id if version_row else None,
            "user_id": user_id or "anonymous",
            "business_scene": business_scene or "unknown",
            "provider": provider,
            "model": model,
            "model_parameters": model_parameters or {},
            "request_hash": input_hash or hashlib.sha256(b"").hexdigest(),
            "response_hash": hashlib.sha256(
                (response_text or validation_error or status).encode()
            ).hexdigest(),
            "response_payload": response_payload or {},
            "status": status,
            "duration_ms": max(0, round(latency_ms)),
            "data_as_of": data_as_of,
            "code_version": settings.CODE_VERSION,
            "provider_usage": provider_usage or {},
            "usage_source": "provider" if usage_source == "provider" else "estimated",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_amount": Decimal(str(cost_amount)) if cost_amount is not None else None,
            "cost_currency": _currency_code(cost_currency),
            "cost_source": cost_source,
            "evidence_ids": retrieved_chunk_ids or [],
            "retrieved_document_ids": retrieved_document_ids or [],
            "tool_calls": tool_calls or [],
            "output_schema_valid": output_schema_valid,
            "fallback_used": fallback_used,
            "review_decision": review_decision,
            "review_feedback": review_feedback,
            "error_message": (validation_error or error_type)[:2000],
        }
        if trace is None:
            trace = LLMCallTrace(
                trace_key=trace_id,
                **values,
            )
            self.session.add(trace)
        elif trace.status != "success":
            for name, value in values.items():
                setattr(trace, name, value)
        await self.session.flush()

    async def mark_trace_fallback(self, trace_id: str) -> None:
        await self.session.execute(
            update(LLMCallTrace).where(LLMCallTrace.trace_key == trace_id).values(fallback_used=True)
        )

    async def list_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = list(
            (
                await self.session.scalars(
                    select(LLMCallTrace)
                    .order_by(LLMCallTrace.created_at.desc())
                    .limit(max(1, min(limit, 1000)))
                )
            ).all()
        )
        return [
            {
                "trace_id": row.trace_key,
                "run_id": str(row.workflow_run_id) if row.workflow_run_id else "",
                "user_id": row.user_id,
                "business_scene": row.business_scene,
                "provider": row.provider,
                "model": row.model,
                "model_parameters": row.model_parameters,
                "duration_ms": row.duration_ms,
                "data_as_of": row.data_as_of.isoformat() if row.data_as_of else None,
                "code_version": row.code_version,
                "provider_usage": row.provider_usage,
                "usage_source": row.usage_source,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_amount": float(row.cost_amount) if row.cost_amount is not None else None,
                "cost_currency": row.cost_currency,
                "cost_source": row.cost_source,
                "retrieved_document_ids": row.retrieved_document_ids,
                "evidence_ids": row.evidence_ids,
                "tool_calls": row.tool_calls,
                "output_schema_valid": row.output_schema_valid,
                "fallback_used": row.fallback_used,
                "review_decision": row.review_decision,
                "review_feedback": row.review_feedback,
                "status": row.status,
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


def _template_model(row: PromptTemplateRow) -> PromptTemplate:
    return PromptTemplate(
        prompt_id=row.prompt_key,
        name=row.name,
        business_scene=row.business_scene,
        owner=row.owner,
        status=cast(PromptStatus, row.status),
        current_version=row.current_version,
        published_version=row.published_version,
        created_at=row.created_at,
        published_at=row.published_at,
    )


def _version_model(prompt_key: str, row: PromptVersionRow) -> PromptVersion:
    return PromptVersion(
        prompt_id=prompt_key,
        version=row.version,
        template=row.template,
        variables=row.variables,
        input_schema=row.input_schema,
        output_schema=row.output_schema,
        model=row.model,
        temperature=float(row.temperature),
        owner=row.owner,
        status=cast(PromptStatus, row.status),
        change_log=row.change_log,
        created_at=row.created_at,
        published_at=row.published_at,
        baseline_metrics=row.baseline_metrics,
    )


def _version_row(prompt_id: uuid.UUID, model: PromptVersion) -> PromptVersionRow:
    return PromptVersionRow(
        prompt_id=prompt_id,
        version=model.version,
        template=model.template,
        variables=model.variables,
        input_schema=model.input_schema,
        output_schema=model.output_schema,
        model=model.model,
        temperature=Decimal(str(model.temperature)),
        owner=model.owner,
        status=model.status,
        change_log=model.change_log,
        baseline_metrics=model.baseline_metrics,
        published_at=model.published_at,
    )


def _evaluate_prompt(version: PromptVersion, cases: list[dict[str, Any]]) -> dict[str, float]:
    if not cases:
        return {"static_render_success_rate": 0.0, "static_expected_token_hit_rate": 0.0}
    rendered = token_hits = token_total = 0
    for case in cases:
        try:
            text = version.template.format_map(_StrictVariables(case.get("variables", {})))
            rendered += 1
            expected = [str(item) for item in case.get("expected_tokens", [])]
            token_total += len(expected)
            token_hits += sum(token in text for token in expected)
        except (KeyError, ValueError):
            continue
    return {
        "static_render_success_rate": round(rendered / len(cases), 6),
        "static_expected_token_hit_rate": round(token_hits / token_total, 6) if token_total else 1.0,
    }


class _StrictVariables(dict):
    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


def _optional_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def _currency_code(value: str) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if len(normalized) == 3 and normalized.isalpha() else "UNK"
