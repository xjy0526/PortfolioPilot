"""PostgreSQL integration coverage for daily pipeline idempotency."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import SyncRun
from app.workers import jobs as worker_jobs

pytestmark = pytest.mark.postgres


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


@pytest.mark.asyncio
async def test_same_day_daily_pipeline_is_idempotent(monkeypatch):
    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    market_run_id = uuid.uuid4()
    valuation_run_id = uuid.uuid4()
    executions = {"market": 0, "valuation": 0}

    async def market_sync(**kwargs):
        del kwargs
        executions["market"] += 1
        return SimpleNamespace(id=market_run_id)

    async def position_rebuild(**kwargs):
        del kwargs
        executions["valuation"] += 1
        return SimpleNamespace(id=valuation_run_id)

    monkeypatch.setattr(worker_jobs, "AsyncSessionFactory", factory)
    monkeypatch.setattr(worker_jobs, "run_market_sync_job", market_sync)
    monkeypatch.setattr(worker_jobs, "run_position_rebuild_job", position_rebuild)

    first_cutoff = datetime(2026, 8, 19, 1, tzinfo=UTC)
    second_cutoff = datetime(2026, 8, 19, 23, tzinfo=UTC)
    try:
        first = await worker_jobs.run_daily_pipeline_job(as_of=first_cutoff)
        second = await worker_jobs.run_daily_pipeline_job(as_of=second_cutoff)

        assert first["idempotent_replay"] is False
        assert second["idempotent_replay"] is True
        assert second["daily_pipeline_run_id"] == first["daily_pipeline_run_id"]
        assert executions == {"market": 1, "valuation": 1}

        run_key = worker_jobs._daily_pipeline_run_key(first_cutoff, None)
        async with factory.begin() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(SyncRun)
                .where(SyncRun.run_key == run_key)
            ) == 1
            await session.execute(delete(SyncRun).where(SyncRun.run_key == run_key))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_daily_pipeline_records_error_and_can_retry(monkeypatch):
    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cutoff = datetime(2026, 8, 20, 22, tzinfo=UTC)
    run_key = worker_jobs._daily_pipeline_run_key(cutoff, None)

    async def failed_market_sync(**kwargs):
        del kwargs
        raise RuntimeError("provider unavailable")

    async def successful_market_sync(**kwargs):
        del kwargs
        return SimpleNamespace(id=uuid.uuid4())

    async def successful_rebuild(**kwargs):
        del kwargs
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(worker_jobs, "AsyncSessionFactory", factory)
    monkeypatch.setattr(worker_jobs, "run_market_sync_job", failed_market_sync)
    monkeypatch.setattr(worker_jobs, "run_position_rebuild_job", successful_rebuild)
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await worker_jobs.run_daily_pipeline_job(as_of=cutoff)

        async with factory() as session:
            failed = await session.scalar(select(SyncRun).where(SyncRun.run_key == run_key))
            assert failed is not None
            assert failed.status == "failed"
            assert failed.completed_at is not None
            assert "provider unavailable" in failed.error_message

        monkeypatch.setattr(worker_jobs, "run_market_sync_job", successful_market_sync)
        result = await worker_jobs.run_daily_pipeline_job(as_of=cutoff)
        assert result["status"] == "completed"

        async with factory.begin() as session:
            completed = await session.scalar(
                select(SyncRun).where(SyncRun.run_key == run_key)
            )
            assert completed is not None
            assert completed.status == "completed"
            assert completed.retry_count == 1
            await session.execute(delete(SyncRun).where(SyncRun.run_key == run_key))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_daily_pipeline_uses_postgres_advisory_lock(monkeypatch):
    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cutoff = datetime(2026, 8, 21, 22, tzinfo=UTC)
    run_key = worker_jobs._daily_pipeline_run_key(cutoff, None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_market_sync(**kwargs):
        del kwargs
        started.set()
        await release.wait()
        return SimpleNamespace(id=uuid.uuid4())

    async def successful_rebuild(**kwargs):
        del kwargs
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(worker_jobs, "AsyncSessionFactory", factory)
    monkeypatch.setattr(worker_jobs, "run_market_sync_job", blocked_market_sync)
    monkeypatch.setattr(worker_jobs, "run_position_rebuild_job", successful_rebuild)
    first = asyncio.create_task(worker_jobs.run_daily_pipeline_job(as_of=cutoff))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(RuntimeError, match="daily pipeline is already running"):
            await worker_jobs.run_daily_pipeline_job(as_of=cutoff)
        release.set()
        assert (await first)["status"] == "completed"

        async with factory.begin() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(SyncRun)
                .where(SyncRun.run_key == run_key)
            ) == 1
            await session.execute(delete(SyncRun).where(SyncRun.run_key == run_key))
    finally:
        release.set()
        if not first.done():
            await first
        await engine.dispose()
