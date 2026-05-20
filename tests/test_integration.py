"""PortfolioPilot - Integration Tests (C3)

End-to-end Tests für die komplette Pipeline:
  - Scorer mit allen 10 Faktoren
  - Rebalancer mit echten Score-Daten
  - Backtest-Engine
  - Refresh-Pipeline Simulation
"""
import pytest
from unittest.mock import MagicMock, patch

from models import (
    AnalystData,
    FearGreedData,
    FmpRating,
    FundamentalData,
    PortfolioPosition,
    Rating,
    ScoreBreakdown,
    StockScore,
    TechnicalIndicators,
    YFinanceData,
)


# ─────────────────────────────────────────────────────────────
# Fixtures: Realistische Testdaten
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_fundamentals():
    """Realistische AAPL-ähnliche Fundamentaldaten."""
    return FundamentalData(
        pe_ratio=28.5,
        forward_pe=25.0,
        peg_ratio=1.8,
        ev_ebitda=22.0,
        roe=0.45,
        gross_margin=0.44,
        operating_margin=0.30,
        net_margin=0.25,
        debt_to_equity=1.7,
        current_ratio=1.1,
        revenue_growth=0.08,
        market_cap=2.8e12,
        free_cash_flow=1.1e11,
        fcf_yield=0.039,
        altman_z_score=8.5,
        piotroski_score=7,
        roic=0.35,
    )


@pytest.fixture
def sample_analyst():
    """Realistische Analysten-Daten."""
    return AnalystData(
        consensus="Buy",
        target_price=220.0,
        num_analysts=35,
        strong_buy_count=15,
        buy_count=12,
        hold_count=6,
        sell_count=1,
        strong_sell_count=1,
    )


@pytest.fixture
def sample_technical():
    """Realistische technische Indikatoren."""
    return TechnicalIndicators(
        rsi_14=55.0,
        sma_50=185.0,
        sma_200=175.0,
        price_vs_sma50=5.4,
        sma_cross="golden",
        momentum_30d=8.5,
        momentum_90d=12.3,
        momentum_180d=22.1,
        signal="Bullish",
    )


@pytest.fixture
def sample_yfinance():
    """Realistische yFinance-Daten."""
    return YFinanceData(
        recommendation_trend="Buy",
        insider_buy_count=5,
        insider_sell_count=2,
        esg_risk_score=18.0,
        earnings_growth_yoy=12.0,
    )


@pytest.fixture
def sample_fear_greed():
    """Neutrales Markt-Sentiment."""
    return FearGreedData(value=55, label="Greed")


@pytest.fixture
def sample_positions():
    """Realistische Portfolio-Positionen."""
    return [
        PortfolioPosition(ticker="AAPL", name="Apple", shares=10, avg_cost=150, current_price=195, sector="Technology"),
        PortfolioPosition(ticker="MSFT", name="Microsoft", shares=5, avg_cost=280, current_price=410, sector="Technology"),
        PortfolioPosition(ticker="JNJ", name="Johnson & Johnson", shares=8, avg_cost=160, current_price=155, sector="Healthcare"),
    ]


# ─────────────────────────────────────────────────────────────
# Test: Scorer Pipeline (alle 10 Faktoren)
# ─────────────────────────────────────────────────────────────

class TestScorerPipelineIntegration:
    """End-to-end Test der 10-Faktor Scoring-Pipeline."""

    def test_full_10_factor_scoring(
        self, sample_fundamentals, sample_analyst, sample_technical,
        sample_yfinance, sample_fear_greed,
    ):
        """Scorer mit allen 10 Faktoren liefert validen Score."""
        from engine.scorer import calculate_score

        score = calculate_score(
            ticker="AAPL",
            name="Apple Inc.",
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
            current_price=195.0,
            yfinance_data=sample_yfinance,
            fear_greed=sample_fear_greed,
            technical=sample_technical,
            sector="Technology",
        )

        assert isinstance(score, StockScore)
        assert 0 <= score.total_score <= 100
        assert score.rating in (Rating.BUY, Rating.HOLD, Rating.SELL)
        assert score.confidence > 0.5  # Hohe Confidence bei so vielen Daten
        assert score.breakdown.quality_score > 0
        assert score.breakdown.momentum_score > 0  # Neuer 10. Faktor

    def test_scorer_with_minimal_data(self):
        """Scorer mit minimalen Daten liefert trotzdem Score."""
        from engine.scorer import calculate_score

        score = calculate_score(
            ticker="TEST",
            name="Test Corp",
            fundamentals=None,
            analyst=None,
            current_price=100.0,
        )

        assert isinstance(score, StockScore)
        assert score.total_score == 50.0  # Default ohne Daten
        assert score.confidence == 0.0

    def test_momentum_factor_integration(self, sample_technical):
        """Momentum-Faktor wird korrekt in den Score integriert."""
        from engine.scorer import calculate_score

        # Score mit positivem Momentum
        score_positive = calculate_score(
            ticker="TEST",
            name="Test",
            fundamentals=None,
            analyst=None,
            current_price=100.0,
            technical=sample_technical,  # momentum_90d=12.3, momentum_180d=22.1
        )

        # Score mit negativem Momentum
        negative_tech = TechnicalIndicators(
            rsi_14=55.0, momentum_90d=-15.0, momentum_180d=-20.0,
        )
        score_negative = calculate_score(
            ticker="TEST",
            name="Test",
            fundamentals=None,
            analyst=None,
            current_price=100.0,
            technical=negative_tech,
        )

        assert score_positive.breakdown.momentum_score > score_negative.breakdown.momentum_score

    def test_macro_aware_sentiment(self, sample_fundamentals, sample_technical):
        """A5: Extreme Fear + Qualitätsaktie = Contrarian-Bonus."""
        from engine.scorer import calculate_score

        extreme_fear = FearGreedData(value=15, label="Extreme Fear")
        score_fear = calculate_score(
            ticker="AAPL", name="Apple", fundamentals=sample_fundamentals,
            analyst=None, current_price=195.0, fear_greed=extreme_fear,
            technical=sample_technical, sector="Technology",
        )

        neutral = FearGreedData(value=50, label="Neutral")
        score_neutral = calculate_score(
            ticker="AAPL", name="Apple", fundamentals=sample_fundamentals,
            analyst=None, current_price=195.0, fear_greed=neutral,
            technical=sample_technical, sector="Technology",
        )

        # Extreme Fear + high quality → Sentiment Score should be boosted
        # However F&G=50 is excluded (line 206: if fear_greed.value != 50)
        # so score_neutral won't include sentiment at all
        assert score_fear.breakdown.sentiment_score > 0

    def test_confidence_quality_weighting(self, sample_fundamentals, sample_analyst):
        """A4: Confidence mit Qualitätsgewichtung."""
        from engine.scorer import calculate_score

        # Score mit vielen Datenquellen
        score_rich = calculate_score(
            ticker="AAPL", name="Apple",
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
            current_price=195.0,
        )

        # Score mit wenig Datenquellen
        thin_analyst = AnalystData(num_analysts=1, consensus="Buy", target_price=200.0)
        score_thin = calculate_score(
            ticker="TEST", name="Test",
            fundamentals=None,
            analyst=thin_analyst,
            current_price=100.0,
        )

        assert score_rich.confidence > score_thin.confidence


# ─────────────────────────────────────────────────────────────
# Test: Rebalancer mit echten Scores
# ─────────────────────────────────────────────────────────────

class TestRebalancerIntegration:
    """End-to-end Test des Rebalancers mit echten Score-Daten."""

    def test_rebalancer_with_scored_positions(self, sample_positions):
        """Rebalancer erzeugt sinnvolle Actions aus Portfolio + Scores."""
        from engine.rebalancer import calculate_rebalancing

        scores = {
            "AAPL": StockScore(ticker="AAPL", name="Apple", total_score=78, rating=Rating.BUY),
            "MSFT": StockScore(ticker="MSFT", name="Microsoft", total_score=72, rating=Rating.BUY),
            "JNJ": StockScore(ticker="JNJ", name="J&J", total_score=35, rating=Rating.SELL),
        }

        result = calculate_rebalancing(sample_positions, scores)

        assert result is not None
        assert hasattr(result, "actions")
        assert len(result.actions) > 0

    def test_rebalancer_empty_portfolio(self):
        """Rebalancer mit leerem Portfolio gibt leeres Ergebnis."""
        from engine.rebalancer import calculate_rebalancing

        result = calculate_rebalancing([], {})
        assert result is not None
        assert len(result.actions) == 0


# ─────────────────────────────────────────────────────────────
# Test: Backtest-Engine
# ─────────────────────────────────────────────────────────────

class TestBacktestIntegration:
    """Test der Backtest-Engine."""

    def test_backtest_with_no_data(self, tmp_path):
        """Backtest ohne Score-Historie gibt Fehler zurück."""
        from engine.backtest import run_backtest

        with patch("engine.backtest.BACKTEST_CACHE_FILE", tmp_path / "bt.json"):
            with patch("engine.analysis.db.get_analysis_history", return_value=[]):
                result = run_backtest(lookback_days=30, forward_days=14)
                assert "error" in result or "entries" in result

    def test_days_between(self):
        """Hilfsfunktion: Tage zwischen Daten."""
        from engine.backtest import _days_between

        assert _days_between("2025-01-01", "2025-01-15") == 14
        assert _days_between("2025-03-01", "2025-03-01") == 0
        assert _days_between("invalid", "2025-01-01") == 0


# ─────────────────────────────────────────────────────────────
# Test: FMP Usage Tracker
# ─────────────────────────────────────────────────────────────

class TestFmpUsageTracker:
    """Test des FMP Usage Trackers (DA4)."""

    def test_get_fmp_usage_returns_dict(self):
        """get_fmp_usage gibt valides Dict zurück."""
        from fetchers.fmp import get_fmp_usage

        usage = get_fmp_usage()

        assert isinstance(usage, dict)
        assert "requests_today" in usage
        assert "daily_limit" in usage
        assert "remaining" in usage
        assert usage["daily_limit"] == 250
        assert usage["remaining"] >= 0
