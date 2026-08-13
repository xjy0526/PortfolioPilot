"""CLI entry point: python -m app.workers.run_knowledge_ingestion."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid

from sqlalchemy import text

from app.db.models import IngestionJob
from app.db.repositories.governance import ResearchRepository
from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.core.resources import close_resources
from app.services.research_knowledge import PostgresKnowledgeService
from time_utils import utc_now


async def process_one_job() -> dict[str, object]:
    job_id: uuid.UUID | None = None
    try:
        async with AsyncSessionFactory() as session:
            async with session.begin():
                acquired = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": "portfoliopilot:knowledge-ingestion"},
                )
                if not acquired:
                    return {"status": "busy"}
                job = await ResearchRepository(session).next_pending_job()
                if job is None:
                    return {"status": "idle"}
                job_id = job.id
                await PostgresKnowledgeService(session).process_job(job)
                return {
                    "status": job.status,
                    "job_id": str(job.id),
                    "document_id": str(job.document_id) if job.document_id else None,
                    "version_id": str(job.version_id) if job.version_id else None,
                    "chunks_created": job.chunks_created,
                }
    except Exception as exc:
        if job_id is not None:
            async with AsyncSessionFactory() as failure_session:
                job = await failure_session.get(IngestionJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.retry_count += 1
                    job.completed_at = utc_now()
                    job.error_message = _safe_error(exc)
                    await failure_session.commit()
        return {
            "status": "failed",
            "job_id": str(job_id) if job_id else None,
            "error": _safe_error(exc),
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
