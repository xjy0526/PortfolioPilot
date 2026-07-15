"""Evaluation dashboard and trace read APIs."""
from fastapi import APIRouter, Query

from evaluation.full_eval import evaluation_dashboard
from prompts.registry import PromptRegistry


router = APIRouter()


@router.get("/api/evaluation/dashboard")
async def get_evaluation_dashboard(limit: int = Query(default=100, ge=1, le=1000)):
    return evaluation_dashboard(limit)


@router.get("/api/evaluation/traces")
async def get_evaluation_traces(limit: int = Query(default=100, ge=1, le=1000)):
    traces = PromptRegistry().list_traces(limit)
    return {"count": len(traces), "traces": traces}
