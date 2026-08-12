"""Controlled research workflow, human review and published report APIs."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from routes.research import _build_portfolio_risk_summary
from workflows import ResearchReportWorkflow


router = APIRouter()


def get_workflow_service() -> ResearchReportWorkflow:
    return ResearchReportWorkflow()


@router.post("/api/workflows/research-report")
async def start_research_report(
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        raw_portfolio_id = payload.get("portfolio_id")
        portfolio_id = uuid.UUID(str(raw_portfolio_id)) if raw_portfolio_id else None
        context = await LegacyPortfolioAdapter(session).load(portfolio_id=portfolio_id)
        if context is None or not context.summary.stocks:
            return JSONResponse(
                {"error": "No PostgreSQL valuation snapshot available"}, status_code=503
            )
        risk_summary = await _build_portfolio_risk_summary(
            context.summary,
            session=session,
            as_of=context.valuation.as_of,
        )
        workflow_payload = dict(payload)
        workflow_payload["_db_portfolio"] = {
            "portfolio_id": str(context.portfolio.id),
            "valuation_snapshot_id": str(context.valuation.id),
            "valuation_input_hash": context.valuation.input_hash,
            "as_of": context.valuation.as_of.isoformat(),
            "tickers": [item.position.ticker for item in context.summary.stocks],
            "risk_summary": risk_summary,
        }
        result = await get_workflow_service().start(workflow_payload)
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
