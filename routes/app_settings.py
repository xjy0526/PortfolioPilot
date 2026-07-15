"""Runtime app settings endpoints for local setup."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from config import BASE_DIR, settings

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
    if not _can_update_settings(request):
        return JSONResponse(
            {
                "error": (
                    "API settings can only be changed from localhost, or behind "
                    "configured dashboard authentication."
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
        updates[env_key] = value

    if updates:
        _apply_runtime_settings(updates)
        _upsert_env_values(BASE_DIR / ".env", updates)
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
        "feature_flags": {
            "tech_picks": not settings.fund_research_mode,
            "shadow_agent": not settings.fund_research_mode,
            "trade_advisor": not settings.fund_research_mode,
        },
        "contact_email": settings.CONTACT_EMAIL,
        "qwen_configured": bool(settings.QWEN_API_KEY),
        "qwen_base_url": settings.QWEN_BASE_URL,
        "qwen_model": settings.QWEN_MODEL,
        "qwen_reasoning_model_configured": bool(settings.QWEN_REASONING_MODEL),
        "fmp_configured": bool(settings.FMP_API_KEY) and settings.FMP_API_KEY != "your_fmp_api_key_here",
        "env_file": ".env",
    }


def _can_update_settings(request: Request) -> bool:
    if settings.auth_configured:
        return True
    client_host = request.client.host if request.client else ""
    return client_host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _apply_runtime_settings(updates: dict[str, str]) -> None:
    for env_key, value in updates.items():
        if hasattr(settings, env_key):
            setattr(settings, env_key, value)


def _upsert_env_values(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    next_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            next_lines.append(f"{key}={_env_value(remaining.pop(key))}")
        else:
            next_lines.append(line)

    if remaining and next_lines and next_lines[-1].strip():
        next_lines.append("")
    for key, value in remaining.items():
        next_lines.append(f"{key}={_env_value(value)}")

    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _env_value(value: str) -> str:
    if not value or any(ch.isspace() for ch in value) or "#" in value or '"' in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
