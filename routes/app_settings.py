"""Runtime app settings endpoints for local setup."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app.core.principal import Principal, get_principal, require_writable
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

_FIELD_TO_ENV = {
    "qwen_api_key": "QWEN_API_KEY",
    "qwen_base_url": "QWEN_BASE_URL",
    "qwen_model": "QWEN_MODEL",
    "qwen_reasoning_model": "QWEN_REASONING_MODEL",
    "fmp_api_key": "FMP_API_KEY",
    "contact_email": "CONTACT_EMAIL",
}


@router.get("/api/app-settings")
async def get_app_settings() -> dict[str, Any]:
    """Return non-secret public settings for the dashboard."""
    return _public_settings()


@router.post("/api/app-settings")
async def update_app_settings(request: Request, data: dict[str, Any] = Body(default=None)):
    """Update API configuration from the dashboard without echoing secrets."""
    principal = await get_principal(request)
    require_writable()
    if not _can_update_settings(request, principal):
        return JSONResponse(
            {
                "error": (
                    "Runtime settings require an enabled local development admin session."
                )
            },
            status_code=403,
        )

    data = data or {}
    updates: dict[str, str] = {}
    for field, env_key in _FIELD_TO_ENV.items():
        value = str(data.get(field, "") or "").strip()
        if not value:
            continue
        if "\n" in value or "\r" in value:
            return JSONResponse({"error": f"Invalid newline in {field}"}, status_code=400)
        if env_key in {"QWEN_BASE_URL", "OPENAI_COMPATIBLE_BASE_URL"}:
            try:
                value = settings.validate_provider_base_url(value, setting_name=env_key)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=422)
        updates[env_key] = value

    if updates:
        _apply_runtime_settings(updates)
        logger.info("Runtime app settings updated: %s", sorted(updates))

    return {
        "status": "ok",
        "saved": sorted(updates),
        "settings": _public_settings(),
    }


def _public_settings() -> dict[str, Any]:
    return {
        "app_name": settings.APP_NAME,
        "app_mode": settings.APP_MODE,
        "read_only_demo": settings.read_only_demo,
        "feature_flags": {
            "tech_picks": not settings.fund_research_mode,
            "shadow_agent": settings.shadow_agent_enabled,
            "trade_advisor": not settings.fund_research_mode,
            "polymarket": settings.ENABLE_POLYMARKET,
            "telegram": settings.ENABLE_TELEGRAM,
            "parqet": settings.ENABLE_PARQET,
        },
        "contact_email": settings.CONTACT_EMAIL,
        "qwen_configured": bool(settings.QWEN_API_KEY),
        "qwen_base_url": settings.QWEN_BASE_URL,
        "qwen_model": settings.QWEN_MODEL,
        "qwen_reasoning_model_configured": bool(settings.QWEN_REASONING_MODEL),
        "fmp_configured": bool(settings.FMP_API_KEY) and settings.FMP_API_KEY != "your_fmp_api_key_here",
        "runtime_configuration_persisted": False,
    }


def _can_update_settings(request: Request, principal: Principal) -> bool:
    if settings.ENVIRONMENT != "development":
        return False
    if not settings.ALLOW_RUNTIME_SECRET_CONFIGURATION:
        return False
    if not principal.is_platform_admin:
        return False
    client_host = request.client.host if request.client else ""
    return client_host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _apply_runtime_settings(updates: dict[str, str]) -> None:
    for env_key, value in updates.items():
        if hasattr(settings, env_key):
            setattr(settings, env_key, value)
