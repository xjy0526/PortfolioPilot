"""Liveness and production dependency readiness probes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.preflight import (
    alembic_head_revision as _alembic_head_revision,
    run_deployment_preflight,
)

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Process liveness does not depend on external services."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    """Report ready only when every mode-required dependency is usable."""
    result = await run_deployment_preflight(session)
    payload = result.payload
    if payload.get("database") == "unavailable":
        # Preserve the original failure contract used by existing probes.
        payload = {"status": "not_ready", "database": "unavailable"}
    return JSONResponse(payload, status_code=200 if result.ready else 503)
