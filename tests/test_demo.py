"""Tests für den Demo-Modus (routes/demo.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from routes.demo import build_demo_portfolio
from fetchers.demo_data import (
    get_demo_positions, get_demo_technical_indicators,
    get_demo_portfolio_history,
    get_demo_risk,
)
from routes.portfolio import get_portfolio_history
from state import portfolio_data


class TestBuildDemoPortfolio:
    """Tests für build_demo_portfolio()."""

    def test_returns_portfolio_summary(self):
        summary = build_demo_portfolio()
        assert summary is not None
        assert summary.is_demo is True

    def test_has_12_positions(self):
        summary = build_demo_portfolio()
        assert summary.num_positions == 12

    def test_all_positions_have_scores(self):
        summary = build_demo_portfolio()
        scored = [s for s in summary.stocks if s.score]
        assert len(scored) == 12

    def test_total_value_positive(self):
        summary = build_demo_portfolio()
        assert summary.total_value > 0

    def test_has_rebalancing(self):
        summary = build_demo_portfolio()
        assert summary.rebalancing is not None
        assert len(summary.rebalancing.actions) > 0

    def test_has_tech_picks(self):
        summary = build_demo_portfolio()
        assert len(summary.tech_picks) > 0

    def test_has_fear_greed(self):
        summary = build_demo_portfolio()
        assert summary.fear_greed is not None
        assert summary.fear_greed.source == "Demo"

    def test_all_data_sources_marked(self):
        """Alle Demo-Positionen sollten alle Datenquellen als verfügbar markiert haben."""
        summary = build_demo_portfolio()
        for stock in summary.stocks:
            ds = stock.data_sources
            assert ds.parqet is True
            assert ds.fmp is True
            assert ds.technical is True
            assert ds.yfinance is True

    def test_has_technical_indicators(self):
        """Demo-Portfolio sollte technische Indikatoren enthalten."""
        summary = build_demo_portfolio()
        with_tech = [s for s in summary.stocks if s.technical is not None]
        assert len(with_tech) == 12

    def test_scores_have_ratings(self):
        """Scores sollten buy/hold/sell Ratings haben."""
        summary = build_demo_portfolio()
        ratings = {s.score.rating.value for s in summary.stocks if s.score}
        # Mindestens 2 verschiedene Ratings (buy, hold, sell)
        assert len(ratings) >= 2


class TestDemoData:
    """Tests für fetchers/demo_data.py Erweiterungen."""

    def test_demo_positions_count(self):
        positions = get_demo_positions()
        assert len(positions) == 12

    def test_demo_technical_indicators(self):
        tech = get_demo_technical_indicators()
        assert len(tech) == 12
        assert "AAPL" in tech
        assert tech["AAPL"].rsi_14 is not None
        assert tech["TSLA"].signal == "Bearish"

    def test_demo_portfolio_history(self):
        history = get_demo_portfolio_history(days=90)
        assert len(history) == 90
        # Invested should increase over time
        assert history[-1]["invested_capital"] >= history[0]["invested_capital"]
        # Value should generally go up (seeded random)
        assert history[-1]["total_value"] > 0

    def test_demo_portfolio_history_structure(self):
        history = get_demo_portfolio_history(days=10)
        for entry in history:
            assert "date" in entry
            assert "total_value" in entry
            assert "invested_capital" in entry

    def test_demo_risk_has_frontend_compatible_fields(self):
        risk = get_demo_risk()

        assert risk["risk_score"] == 5
        assert risk["volatility_annual"] == risk["volatility_annualized"]
        assert risk["var_95_daily"] == abs(risk["var_95_pct"])
        assert risk["max_drawdown"] == abs(risk["max_drawdown_pct"])

    @pytest.mark.asyncio
    async def test_portfolio_history_route_uses_demo_series(self):
        old_summary = portfolio_data.get("summary")
        portfolio_data["summary"] = build_demo_portfolio()
        try:
            history = await get_portfolio_history(days=30)
        finally:
            if old_summary is None:
                portfolio_data.pop("summary", None)
            else:
                portfolio_data["summary"] = old_summary

        assert len(history) == 30
        assert history[-1]["total_value"] > 0

    @pytest.mark.asyncio
    async def test_portfolio_history_route_treats_zero_as_max_for_demo(self):
        old_summary = portfolio_data.get("summary")
        portfolio_data["summary"] = build_demo_portfolio()
        try:
            history = await get_portfolio_history(days=0)
        finally:
            if old_summary is None:
                portfolio_data.pop("summary", None)
            else:
                portfolio_data["summary"] = old_summary

        assert len(history) >= 180
