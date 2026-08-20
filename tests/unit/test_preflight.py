"""Deployment preflight dependency and mode behavior."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import preflight
from config import Settings


class _Session:
    async def rollback(self) -> None:
        return None


class _HealthyRepository:
    def __init__(self, session) -> None:
        del session

    async def ping(self) -> None:
        return None

    async def pgvector_version(self) -> str:
        return "0.8.0"

    async def table_names(self) -> frozenset[str]:
        return preflight.CORE_TABLES | {"alembic_version"}

    async def alembic_revision(self) -> str:
        return "test-head"


class _Embedder:
    provider_name = "test"
    model_name = "test-embedding"
    model_version = "1"
    dimensions = 384
    semantic = True


class _AvailableStorage:
    def __init__(self) -> None:
        self.checks = 0

    async def check_access(self) -> None:
        self.checks += 1


class _UnavailableStorage:
    async def check_access(self) -> None:
        raise RuntimeError("bucket unavailable")


@pytest.fixture(autouse=True)
def _healthy_database(monkeypatch):
    monkeypatch.setattr(preflight, "HealthRepository", _HealthyRepository)
    monkeypatch.setattr(preflight, "alembic_head_revision", lambda: "test-head")


@pytest.mark.asyncio
async def test_read_only_production_does_not_require_writable_object_storage():
    storage = _UnavailableStorage()
    resources = SimpleNamespace(embedder=_Embedder(), object_storage=storage)
    configuration = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        READ_ONLY_DEMO=True,
        OBJECT_STORAGE_BACKEND="local",
        RAG_ALLOW_HASHING_FALLBACK=False,
    )

    result = await preflight.run_deployment_preflight(
        _Session(),
        configuration=configuration,
        resources=resources,
    )

    assert result.ready is True
    assert result.payload["object_storage"]["status"] == "not_required"
    assert result.payload["authentication"]["status"] == "not_required"


@pytest.mark.asyncio
async def test_required_object_storage_failure_makes_readiness_fail():
    resources = SimpleNamespace(
        embedder=_Embedder(),
        object_storage=_UnavailableStorage(),
    )
    configuration = Settings(
        _env_file=None,
        ENVIRONMENT="development",
        OBJECT_STORAGE_BACKEND="local",
    )

    result = await preflight.run_deployment_preflight(
        _Session(),
        configuration=configuration,
        resources=resources,
    )

    assert result.ready is False
    assert result.payload["object_storage"] == {
        "status": "unavailable",
        "backend": "local",
        "bucket": "portfoliopilot-ingestion",
    }


@pytest.mark.asyncio
async def test_local_development_preflight_checks_storage_access():
    storage = _AvailableStorage()
    resources = SimpleNamespace(embedder=_Embedder(), object_storage=storage)
    configuration = Settings(_env_file=None, ENVIRONMENT="development")

    result = await preflight.run_deployment_preflight(
        _Session(),
        configuration=configuration,
        resources=resources,
    )

    assert result.ready is True
    assert storage.checks == 1
    assert result.payload["object_storage"]["status"] == "available"
