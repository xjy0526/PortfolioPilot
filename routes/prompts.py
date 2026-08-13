"""PostgreSQL Prompt Registry lifecycle and comparison APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal, require_writable
from prompts.financial_analysis_prompt import ensure_financial_analysis_prompt
from prompts.registry import PromptRegistry

router = APIRouter()


async def _registry(session: AsyncSession) -> PromptRegistry:
    registry = PromptRegistry(session)
    await ensure_financial_analysis_prompt(registry)
    return registry


@router.get("/api/prompts")
async def list_prompts(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    principal.require_role("knowledge_admin", "platform_admin")
    prompts = await (await _registry(session)).list_prompts()
    return {"count": len(prompts), "prompts": prompts}


@router.post("/api/prompts")
async def create_prompt(
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    try:
        template, version = await (await _registry(session)).create_prompt(payload)
        return JSONResponse(
            {"prompt": template.model_dump(mode="json"), "version": version.model_dump(mode="json")},
            status_code=201,
        )
    except KeyError as exc:
        return JSONResponse({"error": f"Missing field: {exc.args[0]}"}, status_code=422)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@router.post("/api/prompts/{prompt_id}/versions")
async def create_prompt_version(
    prompt_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    try:
        version = await (await _registry(session)).create_version(prompt_id, payload)
        if version is None:
            return JSONResponse({"error": "Prompt not found"}, status_code=404)
        return JSONResponse(version.model_dump(mode="json"), status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@router.post("/api/prompts/{prompt_id}/versions/{version}/publish")
async def publish_prompt_version(
    prompt_id: str,
    version: int,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    deployment = await (await _registry(session)).publish(
        prompt_id, version, deployed_by=principal.user_id
    )
    if deployment is None:
        return JSONResponse({"error": "Prompt version not found"}, status_code=404)
    return deployment.model_dump(mode="json")


@router.post("/api/prompts/{prompt_id}/rollback")
async def rollback_prompt(
    prompt_id: str,
    payload: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    target = (payload or {}).get("target_version")
    deployment = await (await _registry(session)).rollback(
        prompt_id,
        int(target) if target is not None else None,
        deployed_by=principal.user_id,
    )
    if deployment is None:
        return JSONResponse({"error": "Rollback target not found"}, status_code=404)
    return deployment.model_dump(mode="json")


@router.post("/api/prompts/compare")
async def compare_prompt_versions(
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    principal.require_role("knowledge_admin", "platform_admin")
    try:
        result = await (await _registry(session)).compare_versions(
            str(payload["prompt_id"]),
            int(payload["version_a"]),
            int(payload["version_b"]),
            list(payload.get("test_cases") or []),
        )
        return result.model_dump(mode="json")
    except KeyError as exc:
        return JSONResponse({"error": f"Missing field: {exc.args[0]}"}, status_code=422)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
