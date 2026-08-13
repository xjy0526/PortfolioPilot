"""Liveness and production dependency readiness probes."""
from __future__ import annotations

import logging
from functools import lru_cache

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.resources import get_resources
from app.db.models import Base
from app.db.repositories.health import HealthRepository
from config import BASE_DIR

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

CORE_TABLES = frozenset(Base.metadata.tables)


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Process liveness does not depend on external services."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    """Report ready only when schema and reusable model resources are usable."""
    repository = HealthRepository(session)
    try:
        await repository.ping()
        vector_version = await repository.pgvector_version()
        tables = await repository.table_names()
        current_revision = (
            await repository.alembic_revision()
            if "alembic_version" in tables
            else None
        )
    except Exception as exc:
        logger.warning("PostgreSQL readiness check failed: %s", type(exc).__name__)
        await session.rollback()
        return JSONResponse(
            {"status": "not_ready", "database": "unavailable"},
            status_code=503,
        )

    expected_revision = _alembic_head_revision()
    missing_tables = sorted(CORE_TABLES - tables)
    schema_ready = bool(
        vector_version
        and current_revision
        and current_revision == expected_revision
        and not missing_tables
    )
    try:
        resources = await get_resources()
        embedder = resources.embedder
        embedding = {
            "status": "available" if embedder is not None else "unavailable",
            "provider": getattr(embedder, "provider_name", None),
            "model": getattr(embedder, "model_name", None),
            "model_version": getattr(embedder, "model_version", None),
            "dimensions": getattr(embedder, "dimensions", None),
            "semantic": bool(getattr(embedder, "semantic", False)),
        }
    except Exception as exc:
        logger.warning("Application resource readiness check failed: %s", type(exc).__name__)
        embedding = {
            "status": "unavailable",
            "provider": None,
            "model": None,
            "model_version": None,
            "dimensions": None,
            "semantic": False,
        }

    payload = {
        "status": "ready" if schema_ready and embedding["status"] == "available" else "not_ready",
        "database": "available",
        "pgvector": {
            "status": "available" if vector_version else "unavailable",
            "version": vector_version,
        },
        "schema": {
            "status": "current" if schema_ready else "mismatch",
            "current_revision": current_revision,
            "head_revision": expected_revision,
            "missing_core_tables": missing_tables,
        },
        "embedding": embedding,
    }
    return JSONResponse(payload, status_code=200 if payload["status"] == "ready" else 503)


@lru_cache(maxsize=1)
def _alembic_head_revision() -> str:
    configuration = Config(str(BASE_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(configuration)
    heads = script.get_heads()
    if len(heads) != 1:
        return "multiple-heads"
    return str(heads[0])
