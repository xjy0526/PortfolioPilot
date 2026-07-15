"""Controlled research workflow, human review and published report APIs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from workflows import ResearchReportWorkflow


router = APIRouter()


def get_workflow_service() -> ResearchReportWorkflow:
    return ResearchReportWorkflow()


@router.post("/api/workflows/research-report")
async def start_research_report(payload: dict[str, Any] = Body(...)):
    try:
        result = await get_workflow_service().start(payload)
        status_code = 200 if result.get("idempotent_replay") else 201
        return JSONResponse(result, status_code=status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "error_type": type(exc).__name__}, status_code=422)


@router.get("/api/workflows/{run_id}")
async def get_workflow(run_id: str):
    result = get_workflow_service().get_run(run_id)
    if not result:
        return JSONResponse({"error": "Workflow run not found"}, status_code=404)
    return result


async def _review(review_id: str, decision: str, payload: dict[str, Any] | None):
    payload = payload or {}
    try:
        result = await get_workflow_service().decide(
            review_id,
            decision,
            reviewer_id=str(payload.get("reviewer_id") or "human_reviewer"),
            feedback=str(payload.get("feedback") or ""),
        )
        if not result:
            return JSONResponse({"error": "Review task not found"}, status_code=404)
        return result
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@router.post("/api/reviews/{review_id}/approve")
async def approve_review(review_id: str, payload: dict[str, Any] | None = Body(default=None)):
    return await _review(review_id, "approve", payload)


@router.post("/api/reviews/{review_id}/reject")
async def reject_review(review_id: str, payload: dict[str, Any] | None = Body(default=None)):
    return await _review(review_id, "reject", payload)


@router.post("/api/reviews/{review_id}/request-changes")
async def request_review_changes(review_id: str, payload: dict[str, Any] | None = Body(default=None)):
    return await _review(review_id, "request_changes", payload)


@router.get("/api/reports/{report_id}")
async def get_published_report(report_id: str):
    result = get_workflow_service().get_report(report_id)
    if not result:
        return JSONResponse({"error": "Published report not found"}, status_code=404)
    return result
