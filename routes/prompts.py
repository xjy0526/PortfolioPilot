"""Prompt Registry lifecycle and comparison APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from prompts.registry import PromptRegistry


router = APIRouter()


def get_prompt_registry() -> PromptRegistry:
    registry = PromptRegistry()
    from prompts.financial_analysis_prompt import ensure_financial_analysis_prompt
    ensure_financial_analysis_prompt(registry)
    return registry


@router.get("/api/prompts")
async def list_prompts():
    prompts = get_prompt_registry().list_prompts()
    return {"count": len(prompts), "prompts": prompts}


@router.post("/api/prompts")
async def create_prompt(payload: dict[str, Any] = Body(...)):
    try:
        template, version = get_prompt_registry().create_prompt(payload)
        return JSONResponse(
            {"prompt": template.model_dump(mode="json"), "version": version.model_dump(mode="json")},
            status_code=201,
        )
    except KeyError as exc:
        return JSONResponse({"error": f"Missing field: {exc.args[0]}"}, status_code=422)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@router.post("/api/prompts/{prompt_id}/versions")
async def create_prompt_version(prompt_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    try:
        version = get_prompt_registry().create_version(prompt_id, payload)
        if not version:
            return JSONResponse({"error": "Prompt not found"}, status_code=404)
        return JSONResponse(version.model_dump(mode="json"), status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@router.post("/api/prompts/{prompt_id}/versions/{version}/publish")
async def publish_prompt_version(prompt_id: str, version: int):
    deployment = get_prompt_registry().publish(prompt_id, version)
    if not deployment:
        return JSONResponse({"error": "Prompt version not found"}, status_code=404)
    return deployment.model_dump(mode="json")


@router.post("/api/prompts/{prompt_id}/rollback")
async def rollback_prompt(prompt_id: str, payload: dict[str, Any] | None = Body(default=None)):
    target_version = (payload or {}).get("target_version")
    deployment = get_prompt_registry().rollback(
        prompt_id,
        int(target_version) if target_version is not None else None,
    )
    if not deployment:
        return JSONResponse({"error": "Rollback target not found"}, status_code=404)
    return deployment.model_dump(mode="json")


@router.post("/api/prompts/compare")
async def compare_prompt_versions(payload: dict[str, Any] = Body(...)):
    try:
        result = get_prompt_registry().compare_versions(
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
