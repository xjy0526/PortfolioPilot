"""Governed evaluation dashboard and LLM trace read APIs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from evaluation.full_eval import evaluation_dashboard
from prompts.registry import PromptRegistry


router = APIRouter()


@router.get("/api/evaluation/dashboard")
async def get_evaluation_dashboard(
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    principal.require_role("research_reviewer", "knowledge_admin", "platform_admin")
    return await evaluation_dashboard(session, limit)


@router.get("/api/evaluation/traces")
async def get_evaluation_traces(
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    principal.require_role("research_reviewer", "knowledge_admin", "platform_admin")
    traces = await PromptRegistry(session).list_traces(limit)
    return {"count": len(traces), "traces": traces}
