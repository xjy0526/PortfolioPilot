"""Research, risk summary, RAG, AI analysis and strategy backtest APIs."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from analytics.risk_metrics import build_portfolio_risk_summary
from backtest.strategy_backtester import BacktestConfig, resolve_prices_csv, run_strategy_backtest
from config import BASE_DIR, settings
from portfolio_optimizer import llm_risk_adjusted_weighting
from rag import retrieve_evidence
from services.financial_analysis import (
    analyze_portfolio_with_llm,
    safe_financial_analysis_template,
)
from state import portfolio_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/portfolio/risk-summary")
async def get_portfolio_risk_summary():
    """Return structured portfolio risk metrics for frontend and LLM usage."""
    summary = portfolio_data.get("summary")
    if not summary or not getattr(summary, "stocks", None):
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    try:
        return build_portfolio_risk_summary(summary.stocks)
    except Exception as exc:
        logger.exception("Risk summary calculation failed")
        return JSONResponse({"error": "Risk summary calculation failed", "detail": str(exc)}, status_code=500)


@router.post("/api/ai/analyze-portfolio")
async def analyze_portfolio_endpoint(data: dict[str, Any] | None = Body(default=None)):
    """Run structured LLM portfolio analysis with optional RAG evidence."""
    summary = portfolio_data.get("summary")
    if not summary or not getattr(summary, "stocks", None):
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    data = data or {}
    language = data.get("lang") or data.get("language") or "zh"
    try:
        risk_summary = build_portfolio_risk_summary(summary.stocks)
        query = data.get("query") or _default_rag_query(summary, risk_summary)
        top_k = int(data.get("top_k", getattr(settings, "RAG_TOP_K", 5)) or 5)
        evidence = retrieve_evidence(query=query, top_k=top_k)
        analysis = await analyze_portfolio_with_llm(risk_summary, evidence, language=language)
        portfolio_data["last_structured_ai_analysis"] = analysis
        return {
            "status": "ok",
            "analysis": analysis,
            "portfolio_risk_summary": risk_summary,
            "evidence": evidence,
        }
    except Exception as exc:
        logger.exception("Structured portfolio AI analysis failed")
        return JSONResponse({"error": "AI analysis failed", "detail": str(exc)}, status_code=500)


@router.post("/api/rag/retrieve")
async def rag_retrieve_endpoint(data: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Retrieve local RAG evidence for a query."""
    data = data or {}
    query = str(data.get("query", "")).strip()
    top_k = int(data.get("top_k", getattr(settings, "RAG_TOP_K", 5)) or 5)
    if not query:
        return {"status": "ok", "query": query, "evidence": []}
    evidence = retrieve_evidence(query=query, top_k=top_k)
    return {"status": "ok", "query": query, "top_k": top_k, "evidence": evidence}


@router.get("/api/portfolio/rebalance")
async def get_portfolio_rebalance():
    """Return explainable LLM-risk-adjusted target weights without trading."""
    summary = portfolio_data.get("summary")
    if not summary or not getattr(summary, "stocks", None):
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    try:
        risk_summary = build_portfolio_risk_summary(summary.stocks)
        analysis = portfolio_data.get("last_structured_ai_analysis")
        if not analysis:
            analysis = safe_financial_analysis_template(risk_summary, evidence=[], language="zh")

        result = llm_risk_adjusted_weighting(
            current_weights=risk_summary.get("asset_weights", {}),
            asset_risk_metrics=risk_summary.get("asset_metrics", {}),
            llm_risk_score=float(analysis.get("risk_score", risk_summary.get("risk_score", 5.0)) or 5.0),
            asset_level_comments=analysis.get("asset_level_comments", []),
            sector_exposure=risk_summary.get("sector_concentration", {}),
        )
        return {
            "status": "ok",
            "rebalance": result,
            "risk_score": analysis.get("risk_score", risk_summary.get("risk_score")),
            "disclaimer": analysis.get(
                "disclaimer",
                "Research output only. Not investment advice or trading instruction.",
            ),
        }
    except Exception as exc:
        logger.exception("Portfolio rebalance endpoint failed")
        return JSONResponse({"error": "Rebalance calculation failed", "detail": str(exc)}, status_code=500)


@router.get("/api/backtest/report")
async def get_strategy_backtest_report(force: bool = False):
    """Return strategy comparison backtest report, generating mock prices if needed."""
    output_path = settings.CACHE_DIR / "backtest_report.json"
    try:
        if output_path.exists() and not force:
            return json.loads(output_path.read_text(encoding="utf-8"))

        report = run_strategy_backtest(
            BacktestConfig(
                portfolio_csv=BASE_DIR / "example_portfolio.csv",
                prices_csv=_optional_prices_path(),
                output_path=output_path,
            )
        )
        return report
    except Exception as exc:
        logger.exception("Backtest report endpoint failed")
        return JSONResponse({"error": "Backtest report failed", "detail": str(exc)}, status_code=500)


def _optional_prices_path() -> Path | None:
    raw = str(getattr(settings, "BACKTEST_PRICE_CSV", "") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        return resolve_prices_csv(path)
    return resolve_prices_csv()


def _default_rag_query(summary: Any, risk_summary: dict[str, Any]) -> str:
    tickers = [
        stock.position.ticker
        for stock in getattr(summary, "stocks", [])
        if getattr(stock.position, "ticker", "") != "CASH"
    ]
    sectors = ", ".join((risk_summary.get("sector_concentration") or {}).keys())
    asset_types = ", ".join((risk_summary.get("asset_type_exposure") or {}).keys())
    return f"portfolio risk evidence {' '.join(tickers)} sectors {sectors} asset types {asset_types}"
