"""Traceable market-sync and valuation jobs with PostgreSQL mutual exclusion."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, text

from app.db.models import Portfolio, SyncRun
from app.db.session import AsyncSessionFactory
from app.services.market_data_sync import (
    MarketDataSyncService,
    configured_provider_names,
)
from app.services.portfolio_valuation import PortfolioValuationService
from config import settings
from time_utils import utc_now


async def run_market_sync_job(
    *,
    start: date | None = None,
    end: date | None = None,
    provider_names: tuple[str, ...] | None = None,
    portfolio_id: uuid.UUID | None = None,
    max_retries: int = 2,
) -> SyncRun:
    end_date = end or utc_now().date()
    start_date = start or end_date - timedelta(days=settings.MARKET_SYNC_LOOKBACK_DAYS)
    providers = provider_names or configured_provider_names()
    run_id = await _create_run(
        provider="market_data",
        portfolio_id=portfolio_id,
        config={
            "providers": list(providers),
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
    )
    for attempt in range(max_retries + 1):
        try:
            async with AsyncSessionFactory() as session:
                async with session.begin():
                    await _require_advisory_lock(session, "portfoliopilot:market-data")
                    result = await MarketDataSyncService(session).sync(
                        provider_names=providers,
                        start=start_date,
                        end=end_date,
                        sync_run_id=run_id,
                        portfolio_id=portfolio_id,
                    )
                    run = await _require_run(session, run_id)
                    run.status = "completed"
                    run.retry_count = attempt
                    run.completed_at = utc_now()
                    run.data_as_of = datetime.combine(end_date, time.max, tzinfo=UTC)
                    run.result_snapshot = result.as_dict()
                return run
        except Exception as exc:
            if attempt >= max_retries:
                await _fail_run(run_id, exc, attempt)
                raise
            await _record_retry(run_id, exc, attempt + 1)
    raise AssertionError("unreachable market sync retry state")


async def run_position_rebuild_job(
    *,
    portfolio_id: uuid.UUID | None = None,
    as_of: datetime | None = None,
    max_retries: int = 1,
) -> SyncRun:
    cutoff = _as_utc(as_of or utc_now())
    run_id = await _create_run(
        provider="position_rebuild",
        portfolio_id=portfolio_id,
        config={"as_of": cutoff.isoformat()},
    )
    for attempt in range(max_retries + 1):
        try:
            async with AsyncSessionFactory() as session:
                async with session.begin():
                    lock_suffix = str(portfolio_id) if portfolio_id else "all"
                    await _require_advisory_lock(
                        session, f"portfoliopilot:position-rebuild:{lock_suffix}"
                    )
                    portfolio_ids = await _portfolio_ids(session, portfolio_id)
                    hashes: dict[str, str] = {}
                    position_count = 0
                    for current_id in portfolio_ids:
                        result = await PortfolioValuationService(session).value(
                            portfolio_id=current_id,
                            as_of=cutoff,
                            sync_run_id=run_id,
                        )
                        hashes[str(current_id)] = result.valuation.input_hash
                        position_count += len(result.positions)
                    run = await _require_run(session, run_id)
                    run.status = "completed"
                    run.retry_count = attempt
                    run.completed_at = utc_now()
                    run.data_as_of = cutoff
                    run.result_snapshot = {
                        "portfolio_count": len(portfolio_ids),
                        "position_count": position_count,
                        "input_hashes": hashes,
                    }
                return run
        except Exception as exc:
            if attempt >= max_retries:
                await _fail_run(run_id, exc, attempt)
                raise
            await _record_retry(run_id, exc, attempt + 1)
    raise AssertionError("unreachable position rebuild retry state")


async def run_daily_pipeline_job(
    *,
    portfolio_id: uuid.UUID | None = None,
    as_of: datetime | None = None,
) -> dict[str, object]:
    cutoff = _as_utc(as_of or utc_now())
    market_run = await run_market_sync_job(
        end=cutoff.date(),
        portfolio_id=portfolio_id,
    )
    valuation_run = await run_position_rebuild_job(
        portfolio_id=portfolio_id,
        as_of=cutoff,
    )
    return {
        "status": "completed",
        "market_sync_run_id": str(market_run.id),
        "position_rebuild_run_id": str(valuation_run.id),
        "as_of": cutoff.isoformat(),
    }


async def _create_run(
    *, provider: str, portfolio_id: uuid.UUID | None, config: dict[str, object]
) -> uuid.UUID:
    async with AsyncSessionFactory() as session:
        run = SyncRun(
            portfolio_id=portfolio_id,
            provider=provider,
            status="started",
            started_at=utc_now(),
            code_version=settings.CODE_VERSION,
            retry_count=0,
            config_snapshot=config,
            result_snapshot={},
            error_message="",
        )
        session.add(run)
        await session.commit()
        return run.id


async def _record_retry(run_id: uuid.UUID, exc: Exception, retry_count: int) -> None:
    async with AsyncSessionFactory() as session:
        run = await _require_run(session, run_id)
        run.retry_count = retry_count
        run.error_message = _error_summary(exc)
        await session.commit()


async def _fail_run(run_id: uuid.UUID, exc: Exception, retry_count: int) -> None:
    async with AsyncSessionFactory() as session:
        run = await _require_run(session, run_id)
        run.status = "failed"
        run.retry_count = retry_count
        run.completed_at = utc_now()
        run.error_message = _error_summary(exc)
        await session.commit()


async def _require_run(session, run_id: uuid.UUID) -> SyncRun:
    run = await session.get(SyncRun, run_id)
    if run is None:
        raise RuntimeError(f"sync run disappeared: {run_id}")
    return run


async def _require_advisory_lock(session, key: str) -> None:
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": key},
    )
    if not acquired:
        raise RuntimeError(f"worker lock is already held: {key}")


async def _portfolio_ids(session, portfolio_id: uuid.UUID | None) -> list[uuid.UUID]:
    statement = select(Portfolio.id).where(Portfolio.is_active.is_(True))
    if portfolio_id is not None:
        statement = statement.where(Portfolio.id == portfolio_id)
    return list((await session.scalars(statement.order_by(Portfolio.id))).all())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _error_summary(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    message = re.sub(
        r"(postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^:\s]+:)[^@\s]+@",
        r"\1***@",
        message,
        flags=re.IGNORECASE,
    )
    for secret in (
        settings.TUSHARE_TOKEN,
        settings.QWEN_API_KEY,
        settings.OPENAI_COMPATIBLE_API_KEY,
        settings.FMP_API_KEY,
    ):
        if secret and secret not in {"your_qwen_api_key_here", "your_fmp_api_key_here"}:
            message = message.replace(secret, "***")
    return message[:2000]
