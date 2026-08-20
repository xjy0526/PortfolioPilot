"""Lease-based research workflow worker.

Run with ``python -m app.workers.run_research_workflow``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import uuid

from app.core.resources import close_resources
from app.db.repositories.governance import WorkflowRepository
from app.db.session import AsyncSessionFactory, dispose_async_engine
from config import settings
from time_utils import utc_now
from workflows.research_report import (
    ResearchReportWorkflow,
    WorkflowLimitError,
    WorkflowNodeExecutionError,
)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def claim_one_run(worker_id: str) -> uuid.UUID | None:
    async with AsyncSessionFactory.begin() as session:
        run = await WorkflowRepository(session).claim_next_run(
            lease_owner=worker_id,
            now=utc_now(),
            lease_seconds=settings.WORKFLOW_LEASE_SECONDS,
        )
        return run.id if run is not None else None


async def process_claimed_run(run_id: uuid.UUID, worker_id: str) -> dict[str, object]:
    last: dict[str, object] = {
        "run_id": str(run_id),
        "status": "RUNNING",
        "node": None,
    }
    while last["status"] == "RUNNING":
        try:
            async with AsyncSessionFactory() as session:
                service = ResearchReportWorkflow(session)
                last = await service.process_next_node(run_id, lease_owner=worker_id)
                await session.commit()
        except (WorkflowNodeExecutionError, WorkflowLimitError) as exc:
            terminal = isinstance(exc, WorkflowLimitError)
            async with AsyncSessionFactory.begin() as failure_session:
                await ResearchReportWorkflow(failure_session).mark_retry_or_failed(
                    run_id,
                    lease_owner=worker_id,
                    error_type=str(exc) or type(exc).__name__,
                    terminal=terminal,
                )
            return {
                "run_id": str(run_id),
                "status": "FAILED" if terminal else "RETRY",
                "error_type": str(exc) or type(exc).__name__,
            }
        except Exception as exc:
            async with AsyncSessionFactory.begin() as failure_session:
                await ResearchReportWorkflow(failure_session).mark_retry_or_failed(
                    run_id,
                    lease_owner=worker_id,
                    error_type=type(exc).__name__,
                )
            return {
                "run_id": str(run_id),
                "status": "RETRY",
                "error_type": type(exc).__name__,
            }
    return last


async def process_one_run(worker_id: str) -> dict[str, object]:
    run_id = await claim_one_run(worker_id)
    if run_id is None:
        return {"status": "idle"}
    return await process_claimed_run(run_id, worker_id)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Process PostgreSQL research workflows")
    parser.add_argument("--once", action="store_true", help="Process at most one run")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--worker-id", default=default_worker_id())
    args = parser.parse_args()
    try:
        while True:
            result = await process_one_run(args.worker_id)
            print(json.dumps(result, ensure_ascii=False))
            if args.once:
                return
            await asyncio.sleep(max(0.1, args.poll_seconds))
    finally:
        await close_resources()
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())
