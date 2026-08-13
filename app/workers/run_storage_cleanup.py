"""Apply ingestion source retention and optionally remove orphaned objects."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.resources import close_resources, get_resources
from app.db.models import IngestionJob
from app.db.session import AsyncSessionFactory, dispose_async_engine
from config import settings


async def cleanup_storage(*, delete_unreferenced: bool = False) -> dict[str, int]:
    resources = await get_resources()
    storage = resources.object_storage
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        jobs = list((await session.scalars(select(IngestionJob))).all())
    referenced = {job.storage_uri for job in jobs if job.storage_uri}
    expired = {
        job.storage_uri
        for job in jobs
        if job.storage_uri and job.retention_until and job.retention_until <= now
    }
    deleted_expired = 0
    for uri in expired:
        if await storage.exists(uri):
            await storage.delete(uri)
            deleted_expired += 1

    deleted_orphans = 0
    if delete_unreferenced:
        async for uri in storage.iter_uris(settings.OBJECT_STORAGE_PREFIX.strip("/")):
            if uri not in referenced:
                await storage.delete(uri)
                deleted_orphans += 1
    return {"expired_deleted": deleted_expired, "orphans_deleted": deleted_orphans}


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Clean ingestion object storage")
    parser.add_argument(
        "--delete-unreferenced",
        action="store_true",
        help="Delete objects with no ingestion_jobs reference",
    )
    args = parser.parse_args()
    try:
        print(json.dumps(await cleanup_storage(delete_unreferenced=args.delete_unreferenced)))
    finally:
        await close_resources()
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())
