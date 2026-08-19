"""Reusable deployment preflight for CLI and HTTP readiness probes."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.resources import ApplicationResources, close_resources, get_resources
from app.db.models import Base
from app.db.repositories.health import HealthRepository
from app.db.session import AsyncSessionFactory, dispose_async_engine
from config import BASE_DIR, Settings, settings

logger = logging.getLogger(__name__)
CORE_TABLES = frozenset(Base.metadata.tables)


@dataclass(frozen=True, slots=True)
class PreflightResult:
    ready: bool
    payload: dict[str, Any]


async def run_deployment_preflight(
    session: AsyncSession,
    *,
    configuration: Settings = settings,
    resources: ApplicationResources | None = None,
) -> PreflightResult:
    payload: dict[str, Any] = {"status": "not_ready"}
    checks_ready: list[bool] = []
    database_available = False

    configuration_ready, configuration_payload = _configuration_check(configuration)
    payload["configuration"] = configuration_payload
    checks_ready.append(configuration_ready)

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
        expected_revision = alembic_head_revision()
        missing_tables = sorted(CORE_TABLES - tables)
        schema_ready = bool(
            vector_version
            and current_revision
            and current_revision == expected_revision
            and not missing_tables
        )
        payload.update(
            {
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
            }
        )
        database_available = True
        checks_ready.append(schema_ready)
    except Exception as exc:
        logger.warning("PostgreSQL preflight failed: %s", type(exc).__name__)
        await session.rollback()
        payload.update(
            {
                "database": "unavailable",
                "pgvector": {"status": "unknown", "version": None},
                "schema": {"status": "unknown"},
            }
        )
        checks_ready.append(False)

    if not database_available:
        payload["embedding"] = {"status": "not_checked"}
        payload["object_storage"] = {
            "status": "not_checked",
            "backend": configuration.OBJECT_STORAGE_BACKEND,
        }
        payload["authentication"] = _authentication_check(configuration)
        return PreflightResult(ready=False, payload=payload)

    loaded_resources = resources
    if loaded_resources is None:
        try:
            loaded_resources = await get_resources()
        except Exception as exc:
            logger.warning("Application resource preflight failed: %s", type(exc).__name__)

    embedding_ready = bool(loaded_resources and loaded_resources.embedder is not None)
    embedder = loaded_resources.embedder if loaded_resources else None
    payload["embedding"] = {
        "status": "available" if embedding_ready else "unavailable",
        "provider": getattr(embedder, "provider_name", None),
        "model": getattr(embedder, "model_name", None),
        "model_version": getattr(embedder, "model_version", None),
        "dimensions": getattr(embedder, "dimensions", None),
        "semantic": bool(getattr(embedder, "semantic", False)),
    }
    checks_ready.append(embedding_ready)

    storage_required = not (
        configuration.ENVIRONMENT == "production" and configuration.read_only_demo
    )
    if not storage_required:
        payload["object_storage"] = {
            "status": "not_required",
            "backend": configuration.OBJECT_STORAGE_BACKEND,
        }
    elif loaded_resources is None:
        payload["object_storage"] = {
            "status": "unavailable",
            "backend": configuration.OBJECT_STORAGE_BACKEND,
        }
        checks_ready.append(False)
    else:
        try:
            await loaded_resources.object_storage.check_access()
            payload["object_storage"] = {
                "status": "available",
                "backend": configuration.OBJECT_STORAGE_BACKEND,
                "bucket": configuration.object_storage_bucket,
            }
            checks_ready.append(True)
        except Exception as exc:
            logger.warning("Object storage preflight failed: %s", type(exc).__name__)
            payload["object_storage"] = {
                "status": "unavailable",
                "backend": configuration.OBJECT_STORAGE_BACKEND,
                "bucket": configuration.object_storage_bucket,
            }
            checks_ready.append(False)

    payload["authentication"] = _authentication_check(configuration)
    authentication_ready = payload["authentication"]["status"] != "unavailable"
    checks_ready.append(authentication_ready)

    ready = all(checks_ready)
    payload["status"] = "ready" if ready else "not_ready"
    return PreflightResult(ready=ready, payload=payload)


def _configuration_check(configuration: Settings) -> tuple[bool, dict[str, object]]:
    try:
        configuration.validate_runtime_configuration()
    except (RuntimeError, ValueError) as exc:
        return False, {
            "status": "invalid",
            "reason": str(exc),
            "read_only_demo": configuration.read_only_demo,
        }
    return True, {
        "status": "valid",
        "environment": configuration.ENVIRONMENT,
        "read_only_demo": configuration.read_only_demo,
    }


def _authentication_check(configuration: Settings) -> dict[str, object]:
    if configuration.ENVIRONMENT != "production":
        return {"status": "development_only"}
    if configuration.read_only_demo:
        return {"status": "not_required", "mode": "read_only_demo"}
    return {
        "status": "available" if configuration.auth_configured else "unavailable",
        "mode": "basic_auth",
    }


@lru_cache(maxsize=1)
def alembic_head_revision() -> str:
    configuration = Config(str(BASE_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(configuration)
    heads = script.get_heads()
    if len(heads) != 1:
        return "multiple-heads"
    return str(heads[0])


async def _run_cli() -> int:
    try:
        async with AsyncSessionFactory() as session:
            result = await run_deployment_preflight(session)
        print(json.dumps(result.payload, ensure_ascii=False, sort_keys=True))
        return 0 if result.ready else 1
    finally:
        await close_resources()
        await dispose_async_engine()


def main() -> int:
    return asyncio.run(_run_cli())


if __name__ == "__main__":
    raise SystemExit(main())
