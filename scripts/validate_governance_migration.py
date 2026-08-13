"""Validate legacy SQLite governance records against PostgreSQL targets."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    LLMCallTrace,
    PromptDeployment,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    WorkflowRun,
    WorkflowStep,
)
from config import settings
from scripts.migrate_governance_sqlite_to_postgres import _stable_uuid


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if table not in _tables(connection):
        return []
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()]


async def validate(*, sqlite_path: Path, database_url: str) -> dict[str, Any]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {sqlite_path}")
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            checks = {
                "document_versions": await _validate_document_versions(source, session),
                "prompt_versions": await _validate_prompt_versions(source, session),
                "prompt_deployments": await _validate_stable_ids(
                    source,
                    session,
                    "prompt_deployments",
                    "deployment_id",
                    "prompt_deployment",
                    PromptDeployment,
                ),
                "workflow_runs": await _validate_stable_ids(
                    source, session, "workflow_runs", "run_id", "workflow_run", WorkflowRun
                ),
                "workflow_steps": await _validate_stable_ids(
                    source, session, "workflow_steps", "step_id", "workflow_step", WorkflowStep
                ),
                "review_tasks": await _validate_stable_ids(
                    source, session, "review_tasks", "review_id", "review_task", ReviewTask
                ),
                "review_decisions": await _validate_stable_ids(
                    source,
                    session,
                    "review_decisions",
                    "decision_id",
                    "review_decision",
                    ReviewDecision,
                ),
                "published_reports": await _validate_stable_ids(
                    source,
                    session,
                    "published_reports",
                    "report_id",
                    "published_report",
                    PublishedReport,
                ),
                "llm_call_traces": await _validate_trace_keys(source, session),
            }
            return {
                "sqlite_path": str(sqlite_path),
                "all_consistent": all(item["consistent"] for item in checks.values()),
                "checks": checks,
            }
    finally:
        source.close()
        await engine.dispose()


async def _validate_document_versions(source, session) -> dict[str, Any]:
    expected = {
        (
            str(item["document_id"]),
            int(item["version"]),
            str(item["checksum"]),
        )
        for item in _rows(source, "knowledge_document_versions")
    }
    rows = (
        await session.execute(
            select(
                ResearchDocument.document_key,
                DocumentVersion.version,
                DocumentVersion.checksum,
            ).join(DocumentVersion, DocumentVersion.document_id == ResearchDocument.id)
        )
    ).all()
    actual = {(str(key), int(version), str(checksum)) for key, version, checksum in rows}
    result = _result(expected, actual)
    source_document_keys = sorted({item[0] for item in expected})

    count_rows = (
        await session.execute(
            select(
                DocumentVersion.id,
                DocumentVersion.chunk_count,
                func.count(func.distinct(DocumentChunk.id)),
                func.count(func.distinct(ChunkEmbedding.chunk_id)),
            )
            .outerjoin(DocumentChunk, DocumentChunk.version_id == DocumentVersion.id)
            .outerjoin(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
            .join(ResearchDocument, ResearchDocument.id == DocumentVersion.document_id)
            .where(ResearchDocument.document_key.in_(source_document_keys))
            .group_by(DocumentVersion.id, DocumentVersion.chunk_count)
        )
    ).all()
    count_mismatches = [
        {
            "version_id": str(version_id),
            "declared_chunk_count": int(declared),
            "actual_chunk_count": int(chunks),
            "embedded_chunk_count": int(embeddings),
        }
        for version_id, declared, chunks, embeddings in count_rows
        if int(declared) != int(chunks) or int(embeddings) != int(chunks)
    ]
    result["chunk_embedding_mismatch_count"] = len(count_mismatches)
    result["chunk_embedding_mismatches"] = count_mismatches[:100]
    result["consistent"] = bool(result["consistent"] and not count_mismatches)
    return result


async def _validate_prompt_versions(source, session) -> dict[str, Any]:
    expected = {
        (str(item["prompt_id"]), int(item["version"]))
        for item in _rows(source, "prompt_versions")
    }
    rows = (
        await session.execute(
            select(PromptTemplate.prompt_key, PromptVersion.version).join(
                PromptVersion, PromptVersion.prompt_id == PromptTemplate.id
            )
        )
    ).all()
    actual = {(str(key), int(version)) for key, version in rows}
    return _result(expected, actual)


async def _validate_stable_ids(
    source,
    session,
    table: str,
    source_field: str,
    namespace: str,
    model: type,
) -> dict[str, Any]:
    expected = {
        str(_stable_uuid(namespace, item.get(source_field))) for item in _rows(source, table)
    }
    actual = {str(value) for value in (await session.scalars(select(model.id))).all()}
    return _result(expected, actual)


async def _validate_trace_keys(source, session) -> dict[str, Any]:
    expected = {str(item["trace_id"]) for item in _rows(source, "llm_call_traces")}
    actual = {str(value) for value in (await session.scalars(select(LLMCallTrace.trace_key))).all()}
    return _result(expected, actual)


def _result(expected: set[Any], actual: set[Any]) -> dict[str, Any]:
    missing = sorted(expected - actual, key=str)
    return {
        "source_count": len(expected),
        "matched_count": len(expected & actual),
        "missing_count": len(missing),
        "missing": [list(item) if isinstance(item, tuple) else item for item in missing[:100]],
        "consistent": not missing,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Validate SQLite to PostgreSQL governance migration")
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = await validate(sqlite_path=args.sqlite, database_url=args.database_url)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report["all_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(_main())
