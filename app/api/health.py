"""Liveness and PostgreSQL readiness probes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.db.repositories.health import HealthRepository

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Process liveness does not depend on external services."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    """Report ready only when PostgreSQL accepts a query."""
    repository = HealthRepository(session)
    try:
        await repository.ping()
    except Exception as exc:
        logger.warning("PostgreSQL readiness check failed: %s", type(exc).__name__)
        await session.rollback()
        return JSONResponse(
            {"status": "not_ready", "database": "unavailable"},
            status_code=503,
        )
    return JSONResponse({"status": "ready", "database": "available"})
