from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models import PortfolioPosition, PortfolioSummary, StockFullData
from routes import research
from services.market_data.base import PriceHistoryResult


class _StubPriceHistoryService:
    result: PriceHistoryResult

    def __init__(self, *args, **kwargs):
        pass

    async def get_history(self, tickers, *, lookback_days=365, as_of=None):
        return self.result


def _history_result() -> PriceHistoryResult:
    dates = pd.date_range("2026-06-01", periods=25, freq="B")
    prices = pd.DataFrame({
        "AAPL": [100 + index for index in range(len(dates))],
        "MSFT": [200 + index * 0.5 for index in range(len(dates))],
    }, index=dates)
    return PriceHistoryResult(
        adjusted_close=prices,
        source="test_adjusted_close",
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat(),
        start_date=dates.min().date().isoformat(),
        end_date=dates.max().date().isoformat(),
        missing_tickers=[],
        stale_tickers=[],
        coverage_ratio=1.0,
    )


def _summary() -> PortfolioSummary:
    stocks = [
        StockFullData(position=PortfolioPosition(ticker="AAPL", shares=10, avg_cost=90, current_price=124)),
        StockFullData(position=PortfolioPosition(ticker="MSFT", shares=5, avg_cost=180, current_price=212)),
    ]
    return PortfolioSummary(stocks=stocks, scores=[], num_positions=2, total_value=2300)


def _app(monkeypatch) -> FastAPI:
    async def load(self, **kwargs):
        return SimpleNamespace(
            summary=_summary(),
            valuation=SimpleNamespace(as_of=datetime(2026, 7, 15, tzinfo=timezone.utc)),
        )

    async def db_session():
        yield object()

    _StubPriceHistoryService.result = _history_result()
    monkeypatch.setattr(research.LegacyPortfolioAdapter, "load", load)
    monkeypatch.setattr(research, "PriceHistoryService", _StubPriceHistoryService)
    app = FastAPI()
    app.include_router(research.router)
    app.dependency_overrides[research.get_db_session] = db_session
    return app


def test_risk_summary_api_schema_and_real_price_metadata(monkeypatch):
    app = _app(monkeypatch)

    payload = TestClient(app).get("/api/portfolio/risk-summary").json()

    assert payload["as_of"]
    assert payload["portfolio_metrics"]["annual_volatility"] is not None
    assert payload["metric_status"]["annual_volatility"] == "valid"
    assert payload["data_quality"]["source"] == "test_adjusted_close"
    assert payload["data_quality"]["coverage_ratio"] == 1.0
    assert payload["data_quality"]["missing_tickers"] == []
    assert payload["data_quality"]["stale_tickers"] == []


def test_ai_analysis_api_embeds_same_risk_schema(monkeypatch):
    monkeypatch.setattr(research, "retrieve_evidence", lambda **kwargs: [])

    async def _analysis(risk_summary, evidence, language="zh"):
        return {
            "portfolio_summary": "ok",
            "risk_score": risk_summary["risk_score"],
            "main_risks": [],
            "asset_level_comments": [],
            "rebalance_suggestions": [],
            "evidence_used": [],
            "disclaimer": "research only",
        }

    monkeypatch.setattr(research, "analyze_portfolio_with_llm", _analysis)
    app = _app(monkeypatch)

    response = TestClient(app).post("/api/ai/analyze-portfolio", json={"lang": "en"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    risk = payload["portfolio_risk_summary"]
    assert risk["portfolio_metrics"]["sharpe_ratio"] is not None
    assert risk["metric_status"]["sharpe_ratio"] == "valid"
    assert risk["data_quality"]["source"] == "test_adjusted_close"
