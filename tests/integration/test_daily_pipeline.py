"""PostgreSQL integration coverage for daily pipeline idempotency."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    PortfolioValuationSnapshot,
    PositionSnapshot,
    PriceBar,
    Security,
    SyncRun,
    Transaction,
    User,
)
from app.db.repositories import PortfolioRepository, SecurityRepository, UserRepository
from app.services.market_data_sync import (
    MarketDataSyncFailure,
    MarketDataSyncResult,
    MarketDataSyncService,
    ProviderSyncStatus,
)
from app.services.transaction_ledger import TransactionLedgerService
from app.workers import jobs as worker_jobs

pytestmark = pytest.mark.postgres


def _completed_market_run(run_id: uuid.UUID, data_as_of: datetime):
    return SimpleNamespace(
        id=run_id,
        status="completed",
        data_as_of=data_as_of,
        result_snapshot={
            "sync_status": "completed",
            "successful_providers": ["test_provider"],
            "failed_providers": [],
            "degraded": False,
            "provider_statuses": [
                {
                    "provider": "test_provider",
                    "status": "completed",
                    "research_only": True,
                }
            ],
            "data_as_of_earliest": data_as_of.isoformat(),
            "data_as_of_latest": data_as_of.isoformat(),
        },
    )


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
    valuation_kwargs: dict[str, object] = {}
    market_data_as_of = datetime(2026, 8, 19, 2, tzinfo=UTC)

    async def market_sync(**kwargs):
        del kwargs
        executions["market"] += 1
        return _completed_market_run(market_run_id, market_data_as_of)

    async def position_rebuild(**kwargs):
        valuation_kwargs.update(kwargs)
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
        assert valuation_kwargs["as_of"] == first_cutoff
        assert valuation_kwargs["knowledge_as_of"] == market_data_as_of
        assert first["valuation_as_of"] == first_cutoff.isoformat()
        assert first["data_as_of"] == market_data_as_of.isoformat()

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
        return _completed_market_run(uuid.uuid4(), cutoff.replace(hour=23))

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
            completed = await session.scalar(select(SyncRun).where(SyncRun.run_key == run_key))
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
        return _completed_market_run(uuid.uuid4(), cutoff.replace(hour=23))

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


@pytest.mark.asyncio
async def test_daily_pipeline_values_prices_observed_after_start_before_one_cutoff(
    monkeypatch,
) -> None:
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
    suffix = uuid.uuid4().hex
    valuation_as_of = datetime(2026, 8, 20, 10, tzinfo=UTC)
    included_data_as_of = valuation_as_of.replace(minute=1)
    knowledge_cutoff = valuation_as_of.replace(minute=2)
    excluded_data_as_of = valuation_as_of.replace(minute=3)
    market_run_id = uuid.uuid4()
    user_id = None
    portfolio_id = None
    security_id = None

    async with factory.begin() as session:
        user = await UserRepository(session).get_or_create(
            email=f"pipeline-cutoff-{suffix}@example.invalid"
        )
        portfolio = await PortfolioRepository(session).get_or_create(
            user_id=user.id,
            name=f"Pipeline Cutoff {suffix}",
            base_currency="USD",
        )
        security = await SecurityRepository(session).get_or_create(
            canonical_symbol=f"CUT{suffix[:8].upper()}",
            exchange="NASDAQ",
            market="US",
            currency="USD",
        )
        await TransactionLedgerService(session).add_transaction(
            Transaction(
                portfolio_id=portfolio.id,
                security_id=security.id,
                transaction_type="opening_balance",
                occurred_at=valuation_as_of.replace(day=19),
                quantity=Decimal("10"),
                price=Decimal("100"),
                gross_amount=Decimal("1000"),
                fees=Decimal("0"),
                taxes=Decimal("0"),
                currency="USD",
                source="pipeline_cutoff_test",
                external_id=f"opening-{suffix}",
                raw_payload={},
            )
        )
        user_id = user.id
        portfolio_id = portfolio.id
        security_id = security.id

    async def market_sync(**kwargs):
        assert kwargs["end"] == valuation_as_of.date()
        result_snapshot = {
            "sync_status": "completed",
            "successful_providers": ["cutoff_fixture"],
            "failed_providers": [],
            "degraded": False,
            "provider_statuses": [
                {
                    "provider": "cutoff_fixture",
                    "status": "completed",
                    "research_only": True,
                }
            ],
            "data_as_of_earliest": included_data_as_of.isoformat(),
            "data_as_of_latest": included_data_as_of.isoformat(),
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
        }
        async with factory.begin() as session:
            session.add(
                SyncRun(
                    id=market_run_id,
                    portfolio_id=portfolio_id,
                    provider="market_data",
                    status="completed",
                    started_at=valuation_as_of,
                    completed_at=knowledge_cutoff,
                    data_as_of=knowledge_cutoff,
                    code_version="cutoff-test",
                    config_snapshot={"providers": ["cutoff_fixture"]},
                    result_snapshot=result_snapshot,
                )
            )
            await session.flush()
            session.add_all(
                [
                    PriceBar(
                        security_id=security_id,
                        trade_date=valuation_as_of.date(),
                        source="cutoff_fixture",
                        currency="USD",
                        close=Decimal("125"),
                        adjusted_close=Decimal("125"),
                        data_as_of=included_data_as_of,
                        is_final=True,
                        sync_run_id=market_run_id,
                        quality_status="valid",
                        raw_payload={"research_only": True},
                    ),
                    PriceBar(
                        security_id=security_id,
                        trade_date=valuation_as_of.date(),
                        source="yfinance_research",
                        currency="USD",
                        close=Decimal("999"),
                        adjusted_close=Decimal("999"),
                        data_as_of=excluded_data_as_of,
                        is_final=True,
                        sync_run_id=market_run_id,
                        quality_status="valid",
                        raw_payload={"research_only": True, "after_cutoff": True},
                    ),
                ]
            )
        return SimpleNamespace(
            id=market_run_id,
            status="completed",
            data_as_of=knowledge_cutoff,
            result_snapshot=result_snapshot,
        )

    monkeypatch.setattr(worker_jobs, "AsyncSessionFactory", factory)
    monkeypatch.setattr(worker_jobs, "run_market_sync_job", market_sync)
    try:
        result = await worker_jobs.run_daily_pipeline_job(
            portfolio_id=portfolio_id,
            as_of=valuation_as_of,
        )

        assert result["valuation_as_of"] == valuation_as_of.isoformat()
        assert result["data_as_of"] == knowledge_cutoff.isoformat()
        assert result["market_data"]["successful_sources"] == ["cutoff_fixture"]
        async with factory() as session:
            valuation = await session.scalar(
                select(PortfolioValuationSnapshot).where(
                    PortfolioValuationSnapshot.portfolio_id == portfolio_id,
                    PortfolioValuationSnapshot.as_of == valuation_as_of,
                )
            )
            assert valuation is not None
            assert valuation.data_as_of == included_data_as_of
            assert valuation.config_snapshot["valuation_as_of"] == valuation_as_of.isoformat()
            assert valuation.config_snapshot["knowledge_as_of"] == knowledge_cutoff.isoformat()
            assert valuation.config_snapshot["data_source_context"]["successful_sources"] == [
                "cutoff_fixture"
            ]
            position = await session.scalar(
                select(PositionSnapshot).where(
                    PositionSnapshot.valuation_snapshot_id == valuation.id
                )
            )
            assert position is not None
            assert position.native_price == Decimal("125")
            assert position.snapshot_data["price_source"] == "cutoff_fixture"
            assert position.snapshot_data["knowledge_as_of"] == knowledge_cutoff.isoformat()
    finally:
        async with factory.begin() as session:
            await session.execute(
                delete(SyncRun).where(
                    (SyncRun.portfolio_id == portfolio_id)
                    | (SyncRun.id == market_run_id)
                    | (
                        SyncRun.run_key
                        == worker_jobs._daily_pipeline_run_key(valuation_as_of, portfolio_id)
                    )
                )
            )
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(Security).where(Security.id == security_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_market_sync_records_partial_and_total_provider_failures(monkeypatch) -> None:
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
    suffix = uuid.uuid4().hex
    good_name = f"good_{suffix}"
    bad_name = f"bad_{suffix}"
    valuation_date = datetime(2026, 8, 20, tzinfo=UTC).date()

    class GoodProvider:
        name = good_name
        research_only = True

        async def fetch_security_master(self, securities=()):
            del securities
            return []

        async def fetch_price_bars(self, securities, *, start, end):
            del securities, start, end
            return []

        async def fetch_fx_rates(self, pairs, *, start, end):
            del pairs, start, end
            return []

        async def fetch_corporate_actions(self, securities, *, start, end):
            del securities, start, end
            return []

    class BadProvider(GoodProvider):
        name = bad_name

        async def fetch_price_bars(self, securities, *, start, end):
            del securities, start, end
            raise RuntimeError("fixture provider unavailable")

    async with factory.begin() as session:
        result = await MarketDataSyncService(
            session,
            providers={good_name: GoodProvider(), bad_name: BadProvider()},
        ).sync(
            provider_names=(good_name, bad_name),
            start=valuation_date,
            end=valuation_date,
            sync_run_id=uuid.uuid4(),
        )
        assert result.successful_providers == (good_name,)
        assert result.failed_providers == (bad_name,)
        assert result.degraded is True
        assert result.as_dict()["provider_statuses"][1]["error_type"] == "RuntimeError"

    failure_result = MarketDataSyncResult(
        providers=(bad_name,),
        successful_providers=(),
        failed_providers=(bad_name,),
        provider_statuses=(
            ProviderSyncStatus(
                provider=bad_name,
                status="failed",
                research_only=True,
                error_type="RuntimeError",
            ),
        ),
        securities_upserted=0,
        price_bars_upserted=0,
        fx_rates_upserted=0,
        corporate_actions_observed=0,
        data_as_of_earliest=None,
        data_as_of_latest=None,
    )

    class FailedService:
        def __init__(self, session):
            del session

        async def sync(self, **kwargs):
            del kwargs
            raise MarketDataSyncFailure(failure_result)

    monkeypatch.setattr(worker_jobs, "AsyncSessionFactory", factory)
    monkeypatch.setattr(worker_jobs, "MarketDataSyncService", FailedService)
    try:
        with pytest.raises(MarketDataSyncFailure):
            await worker_jobs.run_market_sync_job(
                start=valuation_date,
                end=valuation_date,
                provider_names=(bad_name,),
                max_retries=0,
            )
        async with factory.begin() as session:
            runs = list(
                (
                    await session.scalars(
                        select(SyncRun)
                        .where(SyncRun.provider == "market_data")
                        .order_by(SyncRun.created_at.desc())
                    )
                ).all()
            )
            failed = next(row for row in runs if row.config_snapshot.get("providers") == [bad_name])
            assert failed.status == "failed"
            assert failed.data_as_of is None
            assert failed.result_snapshot["sync_status"] == "failed"
            assert failed.result_snapshot["successful_providers"] == []
            assert failed.result_snapshot["failed_providers"] == [bad_name]
            await session.delete(failed)
    finally:
        await engine.dispose()
