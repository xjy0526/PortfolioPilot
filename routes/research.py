"""Research, risk summary, RAG, AI analysis and strategy backtest APIs."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.services.research_knowledge import PostgresKnowledgeService
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from analytics.risk_metrics import build_portfolio_risk_summary
from backtest.strategy_backtester import (
    EXECUTION_CONVENTION,
    STRATEGY_NAMES,
    BacktestConfig,
    resolve_prices_csv,
    run_strategy_backtest,
)
from config import BASE_DIR, settings
from portfolio_optimizer import risk_parity_simple
from services.financial_analysis import (
    analyze_portfolio_with_llm,
)
from services.market_data.postgres_provider import PostgresPriceHistoryProvider
from services.market_data.price_history_service import PriceHistoryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/portfolio/risk-summary")
async def get_portfolio_risk_summary(
    portfolio_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    """Return structured portfolio risk metrics for frontend and LLM usage."""
    context = await LegacyPortfolioAdapter(session).load(portfolio_id=portfolio_id)
    if context is None or not context.summary.stocks:
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    try:
        return await _build_portfolio_risk_summary(
            context.summary,
            session=session,
            as_of=context.valuation.as_of,
        )
    except Exception as exc:
        logger.exception("Risk summary calculation failed")
        return JSONResponse({"error": "Risk summary calculation failed", "detail": str(exc)}, status_code=500)


@router.post("/api/ai/analyze-portfolio")
async def analyze_portfolio_endpoint(
    data: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Run structured LLM portfolio analysis with optional RAG evidence."""
    data = data or {}
    raw_portfolio_id = data.get("portfolio_id")
    try:
        portfolio_id = uuid.UUID(str(raw_portfolio_id)) if raw_portfolio_id else None
    except ValueError:
        return JSONResponse({"error": "Invalid portfolio_id"}, status_code=422)
    context = await LegacyPortfolioAdapter(session).load(portfolio_id=portfolio_id)
    if context is None or not context.summary.stocks:
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    language = data.get("lang") or data.get("language") or "zh"
    try:
        risk_summary = await _build_portfolio_risk_summary(
            context.summary,
            session=session,
            as_of=context.valuation.as_of,
        )
        query = data.get("query") or _default_rag_query(context.summary, risk_summary)
        top_k = int(data.get("top_k", getattr(settings, "RAG_TOP_K", 5)) or 5)
        retrieval = await PostgresKnowledgeService(session).retrieve_with_status(
            query,
            top_k=top_k,
            principal=principal,
        )
        evidence = retrieval["citations"]
        analysis = await analyze_portfolio_with_llm(
            risk_summary,
            evidence,
            language=language,
            session=session,
            user_id=principal.user_id,
        )
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
async def rag_retrieve_endpoint(
    data: dict[str, Any] | None = Body(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Retrieve local RAG evidence for a query."""
    data = data or {}
    query = str(data.get("query", "")).strip()
    top_k = int(data.get("top_k", getattr(settings, "RAG_TOP_K", 5)) or 5)
    if not query:
        return {
            "status": "ok",
            "query": query,
            "normalized_query": "",
            "top_k": top_k,
            "intent": {},
            "evidence": [],
            "citations": [],
            "evidence_insufficient": True,
        }
    result = await PostgresKnowledgeService(session).retrieve_with_status(
        query,
        top_k=top_k,
        principal=principal,
        score_threshold=data.get("score_threshold"),
    )
    return {
        "status": "ok",
        "query": query,
        "top_k": top_k,
        "evidence": result.get("citations", []),
        **result,
    }


@router.get("/api/portfolio/rebalance")
async def get_portfolio_rebalance(
    portfolio_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    """Return deterministic allocation research without placing trades."""
    context = await LegacyPortfolioAdapter(session).load(portfolio_id=portfolio_id)
    if context is None or not context.summary.stocks:
        return JSONResponse({"error": "No portfolio data available"}, status_code=503)

    try:
        risk_summary = await _build_portfolio_risk_summary(
            context.summary,
            session=session,
            as_of=context.valuation.as_of,
        )
        allocations = risk_parity_simple(
            current_weights=risk_summary.get("asset_weights", {}),
            asset_risk_metrics=risk_summary.get("asset_metrics", {}),
        )
        sector_warnings = [
            f"行业 {sector} 权重为 {float(data.get('weight', 0.0)):.1%}，超过 40% 研究阈值。"
            for sector, data in risk_summary.get("sector_concentration", {}).items()
            if float(data.get("weight", 0.0) or 0.0) > 0.40
        ]
        result = {
            "method": "deterministic_inverse_volatility",
            "target_weight_owner": "deterministic_optimizer",
            "suggestions": allocations,
            "sector_warnings": sector_warnings,
        }
        return {
            "status": "ok",
            "analysis_source": "deterministic_optimizer",
            "llm_used": False,
            "rebalance": result,
            "allocation_research": result,
            "research_observations": sector_warnings,
            "review_priorities": [item["ticker"] for item in allocations if item["weight_change"] < 0],
            "deprecated_fields": ["rebalance"],
            "risk_score": risk_summary.get("risk_score"),
            "disclaimer": "仅用于配置研究与风险提示，不构成投资建议或交易指令。",
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
            cached = json.loads(output_path.read_text(encoding="utf-8"))
            cached_strategies = {
                item.get("strategy") for item in cached.get("strategies", [])
            }
            if (
                cached.get("out_of_sample") is True
                and cached.get("data_leakage_checks", {}).get("status") == "passed"
                and cached.get("execution_convention") == EXECUTION_CONVENTION
                and cached_strategies == set(STRATEGY_NAMES)
            ):
                return cached

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


async def _build_portfolio_risk_summary(
    summary: Any,
    *,
    session: AsyncSession,
    as_of,
) -> dict[str, Any]:
    tickers = [
        stock.position.ticker
        for stock in getattr(summary, "stocks", [])
        if getattr(stock.position, "ticker", "") and getattr(stock.position, "ticker", "") != "CASH"
    ]
    history_service = PriceHistoryService(
        PostgresPriceHistoryProvider(session, as_of=as_of),
        stale_after_days=settings.PRICE_HISTORY_STALE_AFTER_DAYS,
    )
    history = await history_service.get_history(
        tickers,
        lookback_days=settings.PRICE_HISTORY_LOOKBACK_DAYS,
        as_of=as_of,
    )
    return build_portfolio_risk_summary(
        summary.stocks,
        price_data=history.adjusted_close,
        min_observations=settings.RISK_MIN_OBSERVATIONS,
        market_data_quality=history.data_quality(),
        as_of=history.as_of,
    )


def _default_rag_query(summary: Any, risk_summary: dict[str, Any]) -> str:
    tickers = [
        stock.position.ticker
        for stock in getattr(summary, "stocks", [])
        if getattr(stock.position, "ticker", "") != "CASH"
    ]
    sectors = ", ".join((risk_summary.get("sector_concentration") or {}).keys())
    asset_types = ", ".join((risk_summary.get("asset_type_exposure") or {}).keys())
    return f"portfolio risk evidence {' '.join(tickers)} sectors {sectors} asset types {asset_types}"
