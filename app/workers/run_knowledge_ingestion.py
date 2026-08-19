"""CLI entry point: python -m app.workers.run_knowledge_ingestion."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.resources import close_resources, get_resources
from app.db.models import IngestionJob
from app.db.repositories.governance import ResearchRepository
from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.services.research_knowledge import PostgresKnowledgeService
from config import settings
from time_utils import utc_now


async def process_one_job() -> dict[str, object]:
    """Claim, prepare, and persist one job without holding a long DB transaction."""
    job_id = await _claim_job()
    if job_id is None:
        return {"status": "idle"}

    try:
        async with AsyncSessionFactory() as loading_session:
            job = await loading_session.get(IngestionJob, job_id)
            if job is None:
                return {"status": "missing", "job_id": str(job_id)}
            loading_session.expunge(job)

        # Parsing and model inference use a detached job snapshot. This
        # session performs no SQL, so no database transaction remains open
        # while object storage, PDF parsing, or embedding work runs.
        async with AsyncSessionFactory() as preparation_session:
            prepared = await PostgresKnowledgeService(preparation_session).prepare_job(job)

        async with AsyncSessionFactory.begin() as persistence_session:
            job = await persistence_session.scalar(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .with_for_update()
            )
            if job is None:
                return {"status": "missing", "job_id": str(job_id)}
            if job.status in {"completed", "duplicate"}:
                return _job_payload(job)
            if job.status != "processing":
                return {"status": job.status, "job_id": str(job.id)}
            await PostgresKnowledgeService(persistence_session).persist_prepared(job, prepared)
            payload = _job_payload(job)

        await _apply_success_retention(job_id)
        return payload
    except Exception as exc:
        await _mark_failed(job_id, exc)
        return {
            "status": "failed",
            "job_id": str(job_id),
            "error": _safe_error(exc),
        }


async def _claim_job() -> uuid.UUID | None:
    async with AsyncSessionFactory.begin() as session:
        job = await ResearchRepository(session).next_pending_job()
        if job is None:
            return None
        job.status = "processing"
        job.started_at = utc_now()
        job.completed_at = None
        job.error_message = ""
        return job.id


async def _mark_failed(job_id: uuid.UUID, exc: Exception) -> None:
    async with AsyncSessionFactory.begin() as session:
        job = await session.get(IngestionJob, job_id)
        if job is None or job.status in {"completed", "duplicate"}:
            return
        job.status = "failed"
        job.retry_count += 1
        job.completed_at = utc_now()
        job.retention_until = datetime.now(UTC) + timedelta(
            days=max(0, settings.RAG_FAILURE_SOURCE_RETENTION_DAYS)
        )
        job.error_message = _safe_error(exc)


async def _apply_success_retention(job_id: uuid.UUID) -> None:
    if settings.RAG_SUCCESS_SOURCE_RETENTION_DAYS > 0:
        return
    resources = await get_resources()
    async with AsyncSessionFactory() as session:
        job = await session.get(IngestionJob, job_id)
        object_key = job.object_key if job is not None else None
        object_version = job.object_version if job is not None else ""
    if object_key:
        await resources.object_storage.delete_object(
            object_key,
            version=object_version,
        )


def _job_payload(job: IngestionJob) -> dict[str, object]:
    return {
        "status": job.status,
        "job_id": str(job.id),
        "document_id": str(job.document_id) if job.document_id else None,
        "version_id": str(job.version_id) if job.version_id else None,
        "chunks_created": job.chunks_created,
    }


def _safe_error(exc: Exception) -> str:
    value = f"{type(exc).__name__}: {exc}"
    value = re.sub(r"(://[^:\s]+:)[^@\s]+@", r"\1***@", value)
    return value[:2000]


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Process PostgreSQL research ingestion jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    try:
        while True:
            result = await process_one_job()
            print(json.dumps(result, ensure_ascii=False))
            if args.once:
                return
            await asyncio.sleep(max(0.1, args.poll_seconds))
    finally:
        await close_resources()
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())
