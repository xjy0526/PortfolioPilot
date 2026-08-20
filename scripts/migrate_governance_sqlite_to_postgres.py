"""Migrate legacy SQLite research and prompt governance data to PostgreSQL.

This script never opens the application's implicit SQLite connection. Source
and destination are explicit, and every migrated document is re-embedded into
pgvector through the production ingestion service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.principal import Principal
from app.core.resources import close_resources
from app.db.models import (
    LLMCallTrace,
    PromptDeployment,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    WorkflowRun,
    WorkflowStep,
    DocumentVersion,
)
from app.db.repositories.governance import ResearchRepository
from app.services.research_knowledge import PostgresKnowledgeService
from config import settings
from prompts.registry import PromptRegistry
from time_utils import utc_now


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if table not in _tables(connection):
        return []
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()]


def _json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


async def migrate(
    *,
    sqlite_path: Path,
    database_url: str,
) -> dict[str, Any]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {sqlite_path}")
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    report: dict[str, Any] = {
        "source": str(sqlite_path),
        "documents": {"source": 0, "migrated": 0, "failed": []},
        "prompts": {"source": 0, "migrated": 0, "failed": []},
        "prompt_deployments": {"source": 0, "migrated": 0, "failed": []},
        "llm_traces": {"source": 0, "migrated": 0, "failed": []},
        "workflow_runs": {"source": 0, "migrated": 0, "failed": []},
        "workflow_steps": {"source": 0, "migrated": 0, "failed": []},
        "review_tasks": {"source": 0, "migrated": 0, "failed": []},
        "review_decisions": {"source": 0, "migrated": 0, "failed": []},
        "published_reports": {"source": 0, "migrated": 0, "failed": []},
    }
    principal = Principal(
        "sqlite-migration",
        frozenset({"public"}),
        roles=frozenset({"platform_admin"}),
    )
    try:
        documents = _rows(source, "knowledge_documents")
        versions = _rows(source, "knowledge_document_versions")
        report["documents"]["source"] = len(versions)
        document_by_key = {str(item["document_id"]): item for item in documents}
        for version in sorted(versions, key=lambda item: (str(item["document_id"]), int(item["version"]))):
            document = document_by_key.get(str(version["document_id"]), {})
            try:
                metadata = {
                    "document_id": str(version["document_id"]),
                    "title": str(document.get("title") or version["source_filename"]),
                    "source_type": str(document.get("source_type") or "uploaded"),
                    "department": str(document.get("department") or "Research"),
                    "tickers": _json_value(document.get("tickers"), []),
                    "fund_codes": _json_value(document.get("fund_codes"), []),
                    "publish_date": document.get("publish_date"),
                    "effective_from": document.get("effective_from"),
                    "effective_to": document.get("effective_to"),
                    "confidentiality": str(document.get("confidentiality") or "public"),
                    "permission_groups": _json_value(document.get("permission_groups"), ["public"]),
                    "author": str(document.get("author") or ""),
                    "source_checksum": str(version.get("checksum") or ""),
                }
                content = str(version.get("content_text") or "").encode("utf-8")
                async with factory() as session:
                    async with session.begin():
                        service = PostgresKnowledgeService(session)
                        job, _ = await service.queue_upload(
                            content=content,
                            filename=f"{Path(str(version['source_filename'])).stem}.md",
                            metadata=metadata,
                            principal=principal,
                            idempotency_key=f"sqlite-document:{version['version_id']}",
                        )
                        await service.process_job(job)
                        if job.version_id is not None:
                            migrated_version = await session.get(DocumentVersion, job.version_id)
                            if migrated_version is not None:
                                migrated_version.source_filename = str(version["source_filename"])
                                migrated_version.parser = (
                                    f"legacy_{version.get('parser') or 'text'}_content_reconstruction"
                                )
                                migrated_version.content_type = "text/plain"
                                migrated_version.created_at = (
                                    _datetime_value(version.get("created_at"))
                                    or migrated_version.created_at
                                )
                        if job.document_id is not None:
                            migrated_document = await session.get(
                                ResearchDocument, job.document_id
                            )
                            if migrated_document is not None:
                                migrated_document.created_at = (
                                    _datetime_value(document.get("created_at"))
                                    or migrated_document.created_at
                                )
                report["documents"]["migrated"] += 1
            except Exception as exc:
                report["documents"]["failed"].append(
                    {"version_id": str(version.get("version_id")), "error": f"{type(exc).__name__}: {exc}"[:500]}
                )

        for document in documents:
            published_version = int(document.get("published_version") or 0)
            if document.get("status") != "published" or published_version <= 0:
                continue
            try:
                async with factory() as session:
                    async with session.begin():
                        target = await session.scalar(
                            select(ResearchDocument).where(
                                ResearchDocument.document_key == str(document["document_id"])
                            )
                        )
                        published = (
                            await ResearchRepository(session).set_published_version(
                                target.id, published_version
                            )
                            if target
                            else None
                        )
                        if published is None:
                            raise ValueError(
                                f"Published version {published_version} was not migrated"
                            )
            except Exception as exc:
                report["documents"]["failed"].append(
                    {
                        "document_id": str(document.get("document_id")),
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )

        templates = _rows(source, "prompt_templates")
        prompt_versions = _rows(source, "prompt_versions")
        report["prompts"]["source"] = len(prompt_versions)
        versions_by_prompt: dict[str, list[dict[str, Any]]] = {}
        for item in prompt_versions:
            versions_by_prompt.setdefault(str(item["prompt_id"]), []).append(item)
        for template in templates:
            key = str(template["prompt_id"])
            try:
                ordered = sorted(versions_by_prompt.get(key, []), key=lambda item: int(item["version"]))
                if not ordered:
                    continue
                first = ordered[0]
                payload = _prompt_payload(template, first)
                async with factory() as session:
                    async with session.begin():
                        registry = PromptRegistry(session)
                        existing = await registry.get_prompt(key)
                        if existing is None:
                            _, current = await registry.create_prompt(payload)
                        else:
                            current = await registry.get_version(key, 1)
                        for item in ordered[1:]:
                            if await registry.get_version(key, int(item["version"])) is None:
                                current = await registry.create_version(key, _prompt_payload(template, item))
                        target = await registry.repository.get_template(key)
                        if target is None:
                            raise RuntimeError("Migrated prompt template is missing")
                        target.status = str(template.get("status") or "draft")
                        target.current_version = int(template.get("current_version") or len(ordered))
                        target.published_version = (
                            int(template["published_version"])
                            if template.get("published_version") is not None
                            else None
                        )
                        target.created_at = _datetime_value(template.get("created_at")) or target.created_at
                        target.published_at = _datetime_value(template.get("published_at"))
                        for item in ordered:
                            row = await registry.repository.get_version(target.id, int(item["version"]))
                            if row is not None:
                                row.status = str(item.get("status") or "draft")
                                row.created_at = _datetime_value(item.get("created_at")) or row.created_at
                                row.published_at = _datetime_value(item.get("published_at"))
                report["prompts"]["migrated"] += len(ordered)
            except Exception as exc:
                report["prompts"]["failed"].append(
                    {"prompt_id": key, "error": f"{type(exc).__name__}: {exc}"[:500]}
                )

        deployments = _rows(source, "prompt_deployments")
        report["prompt_deployments"]["source"] = len(deployments)
        async with factory() as session:
            async with session.begin():
                registry = PromptRegistry(session)
                for raw in deployments:
                    key = str(raw.get("prompt_id") or "")
                    try:
                        template = await registry.repository.get_template(key)
                        version = (
                            await registry.repository.get_version(
                                template.id, int(raw.get("version") or 0)
                            )
                            if template
                            else None
                        )
                        if template is None or version is None:
                            raise ValueError("Prompt deployment target was not migrated")
                        await _add_mapped(
                            session,
                            report,
                            "prompt_deployments",
                            str(raw.get("deployment_id")),
                            PromptDeployment(
                                id=_stable_uuid("prompt_deployment", raw.get("deployment_id")),
                                prompt_id=template.id,
                                prompt_version_id=version.id,
                                previous_version=(
                                    int(raw["previous_version"])
                                    if raw.get("previous_version") is not None
                                    else None
                                ),
                                action=str(raw.get("action") or "publish"),
                                environment=str(raw.get("environment") or "production"),
                                deployed_by="sqlite-migration",
                                created_at=_datetime_value(raw.get("created_at")) or utc_now(),
                            ),
                        )
                    except Exception as exc:
                        report["prompt_deployments"]["failed"].append(
                            {
                                "deployment_id": str(raw.get("deployment_id")),
                                "error": _error(exc),
                            }
                        )

        await _migrate_workflows_and_traces(source, factory, report)
        report["ok"] = all(
            not report[name]["failed"]
            for name in (
                "documents",
                "prompts",
                "prompt_deployments",
                "llm_traces",
                "workflow_runs",
                "workflow_steps",
                "review_tasks",
                "review_decisions",
                "published_reports",
            )
        )
        return report
    finally:
        source.close()
        await close_resources()
        await engine.dispose()


def _prompt_payload(template: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_id": str(template["prompt_id"]),
        "name": str(template["name"]),
        "business_scene": str(template["business_scene"]),
        "owner": str(version.get("owner") or template.get("owner") or "Research Platform"),
        "template": str(version["template"]),
        "variables": _json_value(version.get("variables"), []),
        "input_schema": _json_value(version.get("input_schema"), {}),
        "output_schema": _json_value(version.get("output_schema"), {}),
        "model": str(version.get("model") or settings.QWEN_MODEL),
        "temperature": float(version.get("temperature") or 0.2),
        "change_log": str(version.get("change_log") or "Migrated from SQLite"),
        "baseline_metrics": _json_value(version.get("baseline_metrics"), {}),
        "status": str(version.get("status") or "draft"),
    }


_MIGRATION_NAMESPACE = uuid.UUID("f09a2488-3722-52f3-97d2-7098db92ebf2")


def _stable_uuid(kind: str, value: Any) -> uuid.UUID:
    raw = str(value or "")
    try:
        return uuid.UUID(raw)
    except ValueError:
        return uuid.uuid5(_MIGRATION_NAMESPACE, f"{kind}:{raw}")


def _datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


async def _migrate_workflows_and_traces(
    source: sqlite3.Connection,
    factory: async_sessionmaker,
    report: dict[str, Any],
) -> None:
    runs = _rows(source, "workflow_runs")
    steps = _rows(source, "workflow_steps")
    reviews = _rows(source, "review_tasks")
    decisions = _rows(source, "review_decisions")
    reports = _rows(source, "published_reports")
    traces = _rows(source, "llm_call_traces")
    source_sets = {
        "workflow_runs": runs,
        "workflow_steps": steps,
        "review_tasks": reviews,
        "review_decisions": decisions,
        "published_reports": reports,
        "llm_traces": traces,
    }
    for name, values in source_sets.items():
        report[name]["source"] = len(values)

    async with factory() as session:
        async with session.begin():
            for raw in runs:
                try:
                    async with session.begin_nested():
                        run_id = _stable_uuid("workflow_run", raw.get("run_id"))
                        if await session.get(WorkflowRun, run_id) is None:
                            session.add(
                                WorkflowRun(
                                    id=run_id,
                                    user_id=str(raw.get("user_id") or "anonymous"),
                                    business_scene=str(raw.get("business_scene") or "legacy_research"),
                                    idempotency_key=str(
                                        raw.get("idempotency_key")
                                        or f"sqlite:{raw.get('run_id')}"
                                    ),
                                    status=str(raw.get("status") or "DRAFT"),
                                    max_steps=int(raw.get("max_steps") or 30),
                                    timeout_seconds=int(raw.get("timeout_seconds") or 120),
                                    node_timeout_seconds=min(
                                        45, int(raw.get("timeout_seconds") or 120)
                                    ),
                                    cost_budget=Decimal(str(raw.get("cost_budget") or "0.20")),
                                    cost_amount=Decimal(str(raw.get("estimated_cost") or "0")),
                                    cost_is_estimated=True,
                                    current_step=str(raw.get("current_step") or ""),
                                    iteration=int(raw.get("iteration") or 1),
                                    context_json=_json_value(raw.get("context_json"), {}),
                                    started_at=_datetime_value(raw.get("started_at")),
                                    completed_at=_datetime_value(raw.get("completed_at")),
                                    error_type=str(raw.get("error_type") or ""),
                                    code_version="sqlite-migration",
                                    created_at=_datetime_value(raw.get("created_at")) or utc_now(),
                                )
                            )
                            await session.flush()
                    report["workflow_runs"]["migrated"] += 1
                except Exception as exc:
                    report["workflow_runs"]["failed"].append(
                        {"run_id": str(raw.get("run_id")), "error": _error(exc)}
                    )
            for raw in steps:
                await _add_mapped(
                    session,
                    report,
                    "workflow_steps",
                    str(raw.get("step_id")),
                    WorkflowStep(
                        id=_stable_uuid("workflow_step", raw.get("step_id")),
                        workflow_run_id=_stable_uuid("workflow_run", raw.get("run_id")),
                        step_name=str(raw.get("step_name") or "unknown"),
                        iteration=int(raw.get("iteration") or 1),
                        status=str(raw.get("status") or "FAILED"),
                        input_summary=_json_value(raw.get("input_summary"), {}),
                        output_summary=_json_value(raw.get("output_summary"), {}),
                        started_at=_datetime_value(raw.get("started_at")) or utc_now(),
                        completed_at=_datetime_value(raw.get("completed_at")),
                        error_type=str(raw.get("error_type") or ""),
                    ),
                )
            for raw in reviews:
                await _add_mapped(
                    session,
                    report,
                    "review_tasks",
                    str(raw.get("review_id")),
                    ReviewTask(
                        id=_stable_uuid("review_task", raw.get("review_id")),
                        workflow_run_id=_stable_uuid("workflow_run", raw.get("run_id")),
                        status=str(raw.get("status") or "PENDING"),
                        assigned_group=str(raw.get("assigned_to") or "research_reviewer"),
                        completed_at=_datetime_value(raw.get("completed_at")),
                    ),
                )
            for raw in decisions:
                await _add_mapped(
                    session,
                    report,
                    "review_decisions",
                    str(raw.get("decision_id")),
                    ReviewDecision(
                        id=_stable_uuid("review_decision", raw.get("decision_id")),
                        review_task_id=_stable_uuid("review_task", raw.get("review_id")),
                        workflow_run_id=_stable_uuid("workflow_run", raw.get("run_id")),
                        decision=str(raw.get("decision") or "reject"),
                        reviewer_id=str(raw.get("reviewer_id") or "legacy-reviewer"),
                        feedback=str(raw.get("feedback") or ""),
                    ),
                )
            for raw in reports:
                await _add_mapped(
                    session,
                    report,
                    "published_reports",
                    str(raw.get("report_id")),
                    PublishedReport(
                        id=_stable_uuid("published_report", raw.get("report_id")),
                        workflow_run_id=_stable_uuid("workflow_run", raw.get("run_id")),
                        report_json=_json_value(raw.get("report_json"), {}),
                        published_by=str(raw.get("published_by") or "legacy-publisher"),
                        published_at=_datetime_value(raw.get("published_at"))
                        or utc_now(),
                    ),
                )

            for raw in traces:
                try:
                    async with session.begin_nested():
                        trace_id = _stable_uuid("llm_trace", raw.get("trace_id"))
                        if await session.get(LLMCallTrace, trace_id) is not None:
                            report["llm_traces"]["migrated"] += 1
                            continue
                        prompt_key = str(raw.get("prompt_id") or "")
                        registry = PromptRegistry(session)
                        prompt = await registry.repository.get_template(prompt_key)
                        prompt_version = (
                            await registry.repository.get_version(
                                prompt.id, int(raw.get("prompt_version") or 1)
                            )
                            if prompt
                            else None
                        )
                        run_value = str(raw.get("run_id") or "")
                        run_id = _stable_uuid("workflow_run", run_value) if run_value else None
                        if run_id is not None and await session.get(WorkflowRun, run_id) is None:
                            run_id = None
                        session.add(
                            LLMCallTrace(
                                id=trace_id,
                                trace_key=str(raw.get("trace_id") or trace_id),
                                workflow_run_id=run_id,
                                prompt_version_id=prompt_version.id if prompt_version else None,
                                user_id=str(raw.get("user_id") or "anonymous"),
                                business_scene=str(raw.get("business_scene") or "legacy"),
                                provider=str(raw.get("provider") or "unknown"),
                                model=str(raw.get("model") or "unknown"),
                                model_parameters=_json_value(raw.get("model_parameters"), {}),
                                request_hash=str(raw.get("input_hash") or "0" * 64),
                                response_hash="0" * 64,
                                status=str(raw.get("status") or "unknown"),
                                duration_ms=round(float(raw.get("latency_ms") or 0)),
                                data_as_of=None,
                                code_version="sqlite-migration",
                                provider_usage={},
                                usage_source="estimated",
                                input_tokens=int(raw.get("input_tokens") or 0),
                                output_tokens=int(raw.get("output_tokens") or 0),
                                cost_amount=Decimal(str(raw.get("estimated_cost") or "0")),
                                cost_currency="UNK",
                                cost_source="estimated_from_legacy",
                                evidence_ids=_json_value(raw.get("retrieved_chunk_ids"), []),
                                retrieved_document_ids=_json_value(
                                    raw.get("retrieved_document_ids"), []
                                ),
                                tool_calls=_json_value(raw.get("tool_calls"), []),
                                output_schema_valid=bool(raw.get("output_schema_valid")),
                                fallback_used=bool(raw.get("fallback_used")),
                                review_decision=str(raw.get("review_decision") or ""),
                                review_feedback=str(raw.get("review_feedback") or ""),
                                error_message=str(
                                    raw.get("validation_error") or raw.get("error_type") or ""
                                ),
                                created_at=_datetime_value(raw.get("created_at")) or utc_now(),
                            ),
                        )
                        await session.flush()
                    report["llm_traces"]["migrated"] += 1
                except Exception as exc:
                    report["llm_traces"]["failed"].append(
                        {"trace_id": str(raw.get("trace_id")), "error": _error(exc)}
                    )


async def _add_mapped(
    session: Any,
    report: dict[str, Any],
    category: str,
    source_id: str,
    entity: Any,
) -> None:
    try:
        async with session.begin_nested():
            if await session.get(type(entity), entity.id) is None:
                session.add(entity)
                await session.flush()
        report[category]["migrated"] += 1
    except Exception as exc:
        report[category]["failed"].append(
            {"source_id": source_id, "error": _error(exc)}
        )


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Migrate governed SQLite data to PostgreSQL")
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = await migrate(sqlite_path=args.sqlite, database_url=args.database_url)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    asyncio.run(_main())
