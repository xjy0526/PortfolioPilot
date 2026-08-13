"""Controlled PostgreSQL research workflow and human-review APIs."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from routes.research import _build_portfolio_risk_summary
from workflows import ResearchReportWorkflow

router = APIRouter()


@router.post("/api/workflows/research-report")
async def start_research_report(
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(get_principal),
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
        workflow_payload = {
            **payload,
            "_db_portfolio": {
                "portfolio_id": str(context.portfolio.id),
                "valuation_snapshot_id": str(context.valuation.id),
                "valuation_input_hash": context.valuation.input_hash,
                "as_of": context.valuation.as_of.isoformat(),
                "tickers": [item.position.ticker for item in context.summary.stocks],
                "risk_summary": risk_summary,
            },
        }
        result = await ResearchReportWorkflow(session).start(
            workflow_payload, principal=principal
        )
        return JSONResponse(result, status_code=200 if result.get("idempotent_replay") else 201)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "error_type": type(exc).__name__}, status_code=422)


@router.get("/api/workflows/{run_id}")
async def get_workflow(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    result = await ResearchReportWorkflow(session).get_run(run_id)
    if result is None or (
        result["user_id"] != principal.user_id and not principal.has_group("research_reviewer")
    ):
        return JSONResponse({"error": "Workflow run not found"}, status_code=404)
    return result


async def _review(
    review_id: uuid.UUID,
    decision: str,
    payload: dict[str, Any] | None,
    *,
    principal: Principal,
    session: AsyncSession,
):
    try:
        result = await ResearchReportWorkflow(session).decide(
            review_id,
            decision,
            principal=principal,
            feedback=str((payload or {}).get("feedback") or ""),
        )
        if result is None:
            return JSONResponse({"error": "Review task not found"}, status_code=404)
        return result
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@router.post("/api/reviews/{review_id}/approve")
async def approve_review(
    review_id: uuid.UUID,
    payload: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    return await _review(review_id, "approve", payload, principal=principal, session=session)


@router.post("/api/reviews/{review_id}/reject")
async def reject_review(
    review_id: uuid.UUID,
    payload: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    return await _review(review_id, "reject", payload, principal=principal, session=session)


@router.post("/api/reviews/{review_id}/request-changes")
async def request_review_changes(
    review_id: uuid.UUID,
    payload: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    return await _review(
        review_id, "request_changes", payload, principal=principal, session=session
    )


@router.get("/api/reports/{report_id}")
async def get_published_report(
    report_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    result = await ResearchReportWorkflow(session).get_report(
        report_id,
        principal=principal,
    )
    if result is None:
        return JSONResponse({"error": "Published report not found"}, status_code=404)
    return result
