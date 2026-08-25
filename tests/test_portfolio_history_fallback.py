import pytest

from models import PortfolioPosition, PortfolioSummary, StockFullData
from services.portfolio_history_fallback import (
    build_estimated_history_detail,
    build_estimated_portfolio_history,
)
from state import portfolio_data


def _summary() -> PortfolioSummary:
    stocks = [
        StockFullData(position=PortfolioPosition(
            ticker="NVDA",
            name="NVIDIA",
            shares=2,
            avg_cost=100,
            current_price=125,
            sector="Technology",
        )),
        StockFullData(position=PortfolioPosition(
            ticker="AMD",
            name="Advanced Micro Devices",
            shares=1,
            avg_cost=80,
            current_price=70,
            sector="Technology",
        )),
    ]
    return PortfolioSummary(
        total_value=320,
        total_cost=280,
        total_pnl=40,
        total_pnl_percent=14.29,
        num_positions=2,
        stocks=stocks,
    )


def test_estimated_portfolio_history_has_enough_points():
    history = build_estimated_portfolio_history(_summary(), days=30, source="csv")

    assert len(history) == 30
    assert history[0]["estimated"] is True
    assert history[-1]["total_value"] == 320.0
    assert history[-1]["invested_capital"] == 280.0


def test_estimated_history_detail_matches_frontend_shape():
    detail = build_estimated_history_detail(_summary(), days=30, source="csv")

    assert len(detail["dates"]) == 30
    assert set(detail["stocks"]) == {"NVDA", "AMD"}
    assert len(detail["stocks"]["NVDA"]["values"]) == 30
    assert detail["total"][-1] == 320.0
    assert detail["total_cost"][-1] == 280.0
    assert detail["pnl"][-1] == 40.0


@pytest.mark.asyncio
async def test_portfolio_history_route_does_not_use_csv_state_as_fact_source(monkeypatch):
    from routes.portfolio import get_portfolio_history

    portfolio_data.clear()
    portfolio_data["summary"] = _summary()
    portfolio_data["source"] = "csv"

    async def no_database_snapshot(self, **kwargs):
        return None

    monkeypatch.setattr(
        "routes.portfolio.LegacyPortfolioAdapter.load", no_database_snapshot
    )
    history = await get_portfolio_history(days=30, portfolio_id=None, session=object())

    assert history == []


def test_portfolio_return_series_uses_csv_estimate_when_snapshots_missing(monkeypatch):
    from config import settings
    from routes.analytics import _portfolio_return_series

    portfolio_data.clear()
    portfolio_data["summary"] = _summary()
    portfolio_data["source"] = "csv"

    monkeypatch.setattr(settings, "ENABLE_LEGACY_SQLITE_COMPAT", True)
    monkeypatch.setattr("database.load_snapshots", lambda days=30: [])

    series = _portfolio_return_series(30, _summary())

    assert len(series) == 30
    assert series[0]["estimated"] is True
    assert series[-1]["value"] == 320.0


def test_benchmark_fallback_keeps_portfolio_series_visible(monkeypatch):
    from routes.analytics import _benchmark_fallback_result, _portfolio_return_series

    portfolio_data.clear()
    portfolio_data["summary"] = _summary()
    portfolio_data["source"] = "csv"

    monkeypatch.setattr("database.load_snapshots", lambda days=30: [])
    series = _portfolio_return_series(30, _summary())

    result = _benchmark_fallback_result("SPY", "1month", series)

    assert result is not None
    assert result["benchmark_estimated"] is True
    assert len(result["portfolio"]) == 30
    assert len(result["benchmark"]) == 30
    assert result["benchmark"][0]["return_pct"] == 0.0
