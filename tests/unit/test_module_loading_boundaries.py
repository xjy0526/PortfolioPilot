"""Runtime loading guards for core, compatibility, and experimental modules."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

import main as main_module
from app.api.router_registry import register_compat_routes, register_experimental_routes
from config import Settings


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTAL_PATHS = {
    "/api/telegram/webhook/{secret}",
    "/api/parqet/authorize",
    "/api/parqet/callback",
    "/api/refresh/portfolio",
    "/api/refresh/parqet",
    "/api/shadow-portfolio",
    "/api/tech-picks",
    "/api/sectors/rotation",
    "/api/advisor/evaluate",
    "/api/advisor/chat",
    "/api/advisor/holding-recommendations",
    "/api/trigger-report",
    "/api/trigger-weekly-digest",
}


def _test_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": "test",
        "EMBEDDING_PROVIDER": "hashing",
        "RAG_ALLOW_HASHING_FALLBACK": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _route_paths(application: FastAPI) -> set[str]:
    return set(application.openapi()["paths"])


def test_core_import_does_not_load_sqlite_or_experimental_modules() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "test",
            "EMBEDDING_PROVIDER": "hashing",
            "RAG_ALLOW_HASHING_FALLBACK": "true",
            "ENABLE_LEGACY_SQLITE_COMPAT": "false",
            "ENABLE_TELEGRAM": "false",
            "ENABLE_PARQET": "false",
            "ENABLE_SHADOW_AGENT": "false",
            "ENABLE_TECH_RADAR": "false",
            "ENABLE_TRADE_ADVISOR": "false",
        }
    )
    script = """
import asyncio
import json
import sys
import main
from app.api.health import live

blocked = {
    "database",
    "routes.telegram",
    "routes.parqet_oauth",
    "routes.shadow_portfolio",
    "services.telegram_bot",
    "services.shadow_agent",
    "services.trade_advisor",
    "services.tech_radar_ai",
}
print(json.dumps({
    "loaded": sorted(blocked.intersection(sys.modules)),
    "health": asyncio.run(live()),
    "paths": sorted(main.app.openapi()["paths"]),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["loaded"] == []
    assert payload["health"] == {"status": "ok"}
    assert "/api/portfolio" in payload["paths"]
    assert not EXPERIMENTAL_PATHS.intersection(payload["paths"])


def test_compat_routes_keep_legacy_urls_and_explicit_sqlite_demo() -> None:
    application = FastAPI()
    configuration = _test_settings(ENABLE_LEGACY_SQLITE_COMPAT=True)

    register_compat_routes(application, configuration=configuration)

    paths = _route_paths(application)
    assert "/api/portfolio" in paths
    assert "/api/analysis/run" in paths
    assert "/api/refresh" in paths
    assert "/api/demo/status" in paths


def test_disabled_experimental_flags_do_not_register_routes() -> None:
    application = FastAPI()

    register_experimental_routes(application, configuration=_test_settings())

    assert not EXPERIMENTAL_PATHS.intersection(_route_paths(application))


def test_enabled_experimental_flags_register_existing_urls() -> None:
    application = FastAPI()
    configuration = _test_settings(
        ENABLE_TELEGRAM=True,
        ENABLE_PARQET=True,
        ENABLE_SHADOW_AGENT=True,
        ENABLE_TECH_RADAR=True,
        ENABLE_TRADE_ADVISOR=True,
    )

    register_experimental_routes(application, configuration=configuration)

    assert EXPERIMENTAL_PATHS.issubset(_route_paths(application))


@pytest.mark.asyncio
async def test_enabled_sqlite_initialization_failure_is_not_swallowed(monkeypatch):
    import database as legacy_database

    configuration = _test_settings(ENABLE_LEGACY_SQLITE_COMPAT=True)

    async def no_op_async() -> None:
        return None

    monkeypatch.setattr(main_module, "settings", configuration)
    monkeypatch.setattr(main_module, "initialize_resources", no_op_async)
    monkeypatch.setattr(main_module, "close_resources", no_op_async)
    monkeypatch.setattr(main_module, "dispose_async_engine", no_op_async)
    monkeypatch.setattr(main_module.CacheManager, "clear_volatile_caches", lambda: None)
    monkeypatch.setattr(main_module.CacheManager, "cleanup_stale_files", lambda: None)
    monkeypatch.setattr(
        legacy_database,
        "init_db",
        lambda: (_ for _ in ()).throw(RuntimeError("legacy sqlite unavailable")),
    )

    with pytest.raises(RuntimeError, match="legacy sqlite unavailable"):
        async with main_module.lifespan(main_module.app):
            pass
