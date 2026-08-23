"""Explicit route registration for core, compatibility, and experiments."""
from __future__ import annotations

from importlib import import_module

from fastapi import APIRouter, FastAPI

from config import Settings, settings


def register_core_routes(application: FastAPI) -> None:
    """Register the PostgreSQL-backed research platform mainline."""
    for module_name in (
        "routes.research",
        "routes.knowledge",
        "routes.prompts",
        "routes.workflows",
        "app.api.evaluation",
        "app.api.health",
        "app.api.portfolios",
        "app.api.market_data",
    ):
        _include_router(application, module_name)


def register_compat_routes(
    application: FastAPI,
    *,
    configuration: Settings = settings,
) -> None:
    """Register stable legacy URLs while their implementations migrate.

    These HTTP adapters remain available independently of the optional SQLite
    storage bridge. PostgreSQL-backed adapters keep serving the existing
    dashboard contract; explicitly legacy handlers may use old state only when
    invoked.
    """
    for module_name in (
        "routes.portfolio",
        "routes.refresh",
        "routes.streaming",
        "routes.analysis",
        "routes.analytics",
        "routes.app_settings",
    ):
        _include_router(application, module_name)

    if configuration.ENABLE_LEGACY_SQLITE_COMPAT:
        # The old in-memory demo imports the legacy portfolio builder, which in
        # turn imports SQLite persistence. Keep that module outside core mode.
        _include_router(application, "routes.demo")


def register_experimental_routes(
    application: FastAPI,
    *,
    configuration: Settings = settings,
) -> None:
    """Lazily register opt-in extensions without loading disabled modules."""
    if configuration.ENABLE_TELEGRAM:
        _include_router(application, "routes.telegram")
        _include_router(application, "routes.refresh", "telegram_router")

    if configuration.ENABLE_PARQET:
        _include_router(application, "routes.parqet_oauth")
        _include_router(application, "routes.refresh", "parqet_router")

    if configuration.ENABLE_SHADOW_AGENT:
        _include_router(application, "routes.shadow_portfolio")

    if configuration.ENABLE_TECH_RADAR:
        _include_router(application, "routes.portfolio", "tech_radar_router")
        _include_router(application, "routes.analysis", "tech_radar_router")

    if configuration.ENABLE_TRADE_ADVISOR:
        _include_router(application, "routes.analysis", "trade_advisor_router")

    # Polymarket currently has no standalone Router. ENABLE_POLYMARKET controls
    # its optional import/display behavior and is intentionally not a reason to
    # load any other experimental integration.


def _include_router(
    application: FastAPI,
    module_name: str,
    attribute: str = "router",
) -> None:
    module = import_module(module_name)
    router = getattr(module, attribute, None)
    if not isinstance(router, APIRouter):
        raise TypeError(f"{module_name}.{attribute} must be an APIRouter")
    application.include_router(router)
