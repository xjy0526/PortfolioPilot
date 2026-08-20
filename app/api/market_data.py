"""Explicit market-data synchronization API."""
from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.market_data_sync import configured_provider_names
from app.workers.jobs import run_market_sync_job
from app.core.principal import Principal, get_principal, require_writable

router = APIRouter(prefix="/api/market-data", tags=["market-data"])
logger = logging.getLogger(__name__)


class MarketDataSyncRequest(BaseModel):
    providers: list[str] = Field(default_factory=lambda: list(configured_provider_names()))
    start: date | None = None
    end: date | None = None
    portfolio_id: uuid.UUID | None = None


@router.post("/sync")
async def sync_market_data(
    request: MarketDataSyncRequest,
    principal: Principal = Depends(get_principal),
) -> dict[str, object]:
    require_writable()
    principal.require_role("market_data_admin", "platform_admin")
    try:
        run = await run_market_sync_job(
            start=request.start,
            end=request.end,
            provider_names=tuple(request.providers),
            portfolio_id=request.portfolio_id,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning("Market-data sync failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Market-data sync failed; inspect the corresponding sync run",
        ) from exc
    return {
        "sync_run_id": str(run.id),
        "status": run.status,
        "data_as_of": run.data_as_of,
        "retry_count": run.retry_count,
        "result": run.result_snapshot,
    }
