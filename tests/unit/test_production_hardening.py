"""Fail-closed production identity, configuration, and embedding contracts."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api.market_data import router as market_data_router
from app.core.principal import Principal, get_principal
from app.providers.embeddings import EmbeddingConfigurationError, build_embedding_provider
from config import Settings, settings
from middleware.auth import BasicAuthMiddleware, ReadOnlyDemoMiddleware


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "OBJECT_STORAGE_BACKEND": "s3",
        "READ_ONLY_DEMO": True,
        "ALLOW_DEV_IDENTITY_HEADERS": False,
        "RAG_ALLOW_HASHING_FALLBACK": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_defaults_to_read_only_and_rejects_dev_identity_headers():
    default = Settings(_env_file=None, ENVIRONMENT="production")
    assert default.read_only_demo is True

    unsafe = _production_settings(ALLOW_DEV_IDENTITY_HEADERS=True)
    with pytest.raises(RuntimeError, match="forbidden in production"):
        unsafe.validate_runtime_configuration()


def test_writable_production_requires_authentication_and_shared_storage():
    unauthenticated = _production_settings(READ_ONLY_DEMO=False)
    with pytest.raises(RuntimeError, match="configure authentication"):
        unauthenticated.validate_runtime_configuration()

    local_storage = _production_settings(
        READ_ONLY_DEMO=False,
        DASHBOARD_USER="researcher",
        DASHBOARD_PASSWORD="test-password",
        OBJECT_STORAGE_BACKEND="local",
    )
    with pytest.raises(RuntimeError, match="S3-compatible object storage"):
        local_storage.validate_runtime_configuration()


def test_production_hashing_embedding_is_rejected_even_if_fallback_requested():
    configuration = _production_settings(
        EMBEDDING_PROVIDER="hashing",
        RAG_ALLOW_HASHING_FALLBACK=True,
    )
    with pytest.raises(EmbeddingConfigurationError, match="restricted"):
        build_embedding_provider(configuration)


def test_provider_base_url_must_be_https_and_allowlisted():
    configuration = _production_settings()
    with pytest.raises(ValueError, match="HTTPS"):
        configuration.validate_provider_base_url(
            "http://dashscope.aliyuncs.com/compatible-mode/v1",
            setting_name="QWEN_BASE_URL",
        )
    with pytest.raises(ValueError, match="allowlisted"):
        configuration.validate_provider_base_url(
            "https://attacker.example/v1",
            setting_name="QWEN_BASE_URL",
        )


@pytest.mark.asyncio
async def test_read_only_gate_precedes_basic_auth(monkeypatch):
    monkeypatch.setattr(settings, "READ_ONLY_DEMO", True)
    monkeypatch.setattr(settings, "DASHBOARD_USER", "configured-user")
    monkeypatch.setattr(settings, "DASHBOARD_PASSWORD", "configured-password")
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)
    app.add_middleware(ReadOnlyDemoMiddleware)

    @app.post("/write")
    async def write() -> dict[str, bool]:
        return {"written": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/write")

    assert response.status_code == 403
    assert response.json() == {"detail": "This deployment is a read-only demo"}


@pytest.mark.asyncio
async def test_anonymous_principal_cannot_start_market_sync(monkeypatch):
    monkeypatch.setattr(settings, "READ_ONLY_DEMO", False)
    app = FastAPI()
    app.include_router(market_data_router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        "anonymous",
        frozenset({"public"}),
        authenticated=False,
        tenant_id="public",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/market-data/sync", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
