"""Tests für den AI Finance Agent."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from models import (
    PortfolioSummary,
    PortfolioPosition,
    StockFullData,
    StockScore,
    ScoreBreakdown,
    Rating,
    FearGreedData,
    AnalysisReport,
    PositionAnalysis,
    DataSourceStatus,
)


@pytest.fixture
def sample_summary():
    """Erstellt eine PortfolioSummary mit Testdaten."""
    stocks = [
        StockFullData(
            position=PortfolioPosition(
                ticker="AAPL", name="Apple Inc.", shares=10,
                avg_cost=150.0, current_price=175.0, sector="Technology",
                daily_change_pct=2.1,
            ),
            score=StockScore(
                ticker="AAPL", name="Apple Inc.", total_score=78.0,
                rating=Rating.BUY, breakdown=ScoreBreakdown(quality_score=80),
            ),
            data_sources=DataSourceStatus(fmp=True, technical=True),
        ),
        StockFullData(
            position=PortfolioPosition(
                ticker="MSFT", name="Microsoft", shares=5,
                avg_cost=300.0, current_price=350.0, sector="Technology",
                daily_change_pct=0.5,
            ),
            score=StockScore(
                ticker="MSFT", name="Microsoft", total_score=55.0,
                rating=Rating.HOLD, breakdown=ScoreBreakdown(quality_score=60),
            ),
            data_sources=DataSourceStatus(fmp=True),
        ),
        StockFullData(
            position=PortfolioPosition(
                ticker="INTC", name="Intel", shares=20,
                avg_cost=40.0, current_price=30.0, sector="Technology",
                daily_change_pct=-3.4,
            ),
            score=StockScore(
                ticker="INTC", name="Intel", total_score=32.0,
                rating=Rating.SELL, breakdown=ScoreBreakdown(quality_score=25),
            ),
            data_sources=DataSourceStatus(fmp=True, technical=True),
        ),
    ]

    return PortfolioSummary(
        total_value=4100.0,
        total_cost=3800.0,
        total_pnl=300.0,
        total_pnl_percent=7.9,
        num_positions=3,
        stocks=stocks,
        fear_greed=FearGreedData(value=65, label="Greed", source="CNN"),
        daily_total_change=50.0,
        daily_total_change_pct=1.2,
    )


@pytest.fixture
def sample_report():
    """Erstellt einen AnalysisReport mit Testdaten."""
    return AnalysisReport(
        analysis_level="full",
        portfolio_score=55.0,
        portfolio_rating=Rating.HOLD,
        num_positions=3,
        positions=[
            PositionAnalysis(ticker="AAPL", name="Apple", score=78.0, rating=Rating.BUY,
                             score_change=3.0, weight_in_portfolio=42.7),
            PositionAnalysis(ticker="MSFT", name="Microsoft", score=55.0, rating=Rating.HOLD,
                             score_change=-1.0, weight_in_portfolio=42.7),
            PositionAnalysis(ticker="INTC", name="Intel", score=32.0, rating=Rating.SELL,
                             score_change=-5.0, weight_in_portfolio=14.6),
        ],
        top_buys=[
            PositionAnalysis(ticker="AAPL", name="Apple", score=78.0, rating=Rating.BUY,
                             weight_in_portfolio=42.7),
        ],
        top_sells=[
            PositionAnalysis(ticker="INTC", name="Intel", score=32.0, rating=Rating.SELL,
                             weight_in_portfolio=14.6),
        ],
        biggest_changes=[
            PositionAnalysis(ticker="INTC", name="Intel", score=32.0, score_change=-5.0, rating=Rating.SELL),
            PositionAnalysis(ticker="AAPL", name="Apple", score=78.0, score_change=3.0, rating=Rating.BUY),
        ],
        avg_confidence=0.7,
        data_quality={"total": 3, "fmp": 3, "technical": 2, "yfinance": 1, "fear_greed": 1},
    )


class TestBuildTelegramReport:
    """Tests für die Report-Formatierung."""

    def test_report_contains_portfolio_overview(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "PortfolioPilot Daily Report" in text
        assert "Portfolio Übersicht" in text
        assert "$4,100.00 USD" in text

    def test_report_contains_all_positions(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "AAPL" in text
        assert "MSFT" in text
        assert "INTC" in text

    def test_report_contains_positions_with_daily_change(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "Positionen" in text

    def test_report_contains_watchlist(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "Watchlist" in text or "SELL" in text

    def test_report_contains_score_changes(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "Veränderungen" in text

    def test_report_contains_fear_greed(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "Fear & Greed" in text
        assert "65" in text

    def test_report_contains_ai_insights(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report, ai_insights="Markt sieht bullish aus")

        assert "AI Marktkommentar" in text
        assert "bullish" in text

    def test_report_without_ai_insights(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report, ai_insights="")

        assert "AI Marktkommentar" not in text

    def test_report_without_analysis_report(self, sample_summary):
        from services.ai_agent import _build_telegram_report
        # Sollte nicht crashen ohne Report
        text = _build_telegram_report(sample_summary, None)

        assert "PortfolioPilot Daily Report" in text
        assert "Positionen" in text


class TestHelpers:
    """Tests für Hilfsfunktionen."""

    def test_rating_icon(self):
        from services.ai_agent import _rating_icon
        assert _rating_icon(Rating.BUY) == "🟢"
        assert _rating_icon(Rating.HOLD) == "🟡"
        assert _rating_icon(Rating.SELL) == "🔴"

    def test_fear_greed_emoji(self):
        from services.ai_agent import _fear_greed_emoji
        assert _fear_greed_emoji(10) == "😱"
        assert _fear_greed_emoji(30) == "😟"
        assert _fear_greed_emoji(50) == "😐"
        assert _fear_greed_emoji(70) == "😊"
        assert _fear_greed_emoji(90) == "🤑"

    def test_sort_stocks_by_score(self, sample_summary):
        from services.ai_agent import _sort_stocks_by_score
        sorted_stocks = _sort_stocks_by_score(sample_summary.stocks)
        scores = [s.score.total_score for s in sorted_stocks]
        assert scores == sorted(scores, reverse=True)


class TestDailyMovers:
    """Tests für die Tagesgewinner/Tagesverlierer-Logik."""

    def test_get_daily_movers_returns_winners_and_losers(self, sample_summary):
        from services.ai_agent import _get_daily_movers
        winners, losers = _get_daily_movers(sample_summary.stocks)

        assert len(winners) == 2  # AAPL +2.1%, MSFT +0.5% (top_n=2)
        assert len(losers) == 1   # INTC -3.4% (nur 1 Verlierer vorhanden)
        assert winners[0][0].position.ticker == "AAPL"
        assert losers[0][0].position.ticker == "INTC"

    def test_get_daily_movers_filters_none_and_zero(self):
        from services.ai_agent import _get_daily_movers
        stocks = [
            StockFullData(
                position=PortfolioPosition(
                    ticker="X", shares=1, avg_cost=10, current_price=10,
                    daily_change_pct=None,
                ),
                data_sources=DataSourceStatus(),
            ),
            StockFullData(
                position=PortfolioPosition(
                    ticker="Y", shares=1, avg_cost=10, current_price=10,
                    daily_change_pct=0.0,
                ),
                data_sources=DataSourceStatus(),
            ),
        ]
        winners, losers = _get_daily_movers(stocks)
        assert winners == []
        assert losers == []

    def test_get_daily_movers_respects_top_n(self, sample_summary):
        from services.ai_agent import _get_daily_movers
        winners, losers = _get_daily_movers(sample_summary.stocks, top_n=1)
        assert len(winners) <= 1
        assert len(losers) <= 1

    def test_report_contains_daily_movers(self, sample_summary, sample_report):
        from services.ai_agent import _build_telegram_report
        text = _build_telegram_report(sample_summary, sample_report)

        assert "Tagesgewinner" in text
        assert "Tagesverlierer" in text
        assert "AAPL" in text
        assert "INTC" in text


class TestRunDailyReport:
    """Tests für den Agent-Hauptprozess."""

    @pytest.mark.asyncio
    async def test_run_daily_report_no_telegram(self):
        from services.ai_agent import run_daily_report

        with patch("services.ai_agent.settings") as mock_settings:
            mock_settings.telegram_configured = False
            await run_daily_report()
            # Soll ohne Fehler durchlaufen

    @pytest.mark.asyncio
    async def test_run_daily_report_no_portfolio(self):
        from services.ai_agent import run_daily_report

        with patch("services.ai_agent.settings") as mock_settings, \
             patch("services.telegram.send_message", new_callable=AsyncMock) as mock_send:
            mock_settings.telegram_configured = True
            mock_settings.TELEGRAM_BOT_TOKEN = "test"
            mock_settings.TELEGRAM_CHAT_ID = "123"

            # portfolio_data wird innerhalb der Funktion aus state importiert
            with patch.dict("state.portfolio_data", {"summary": None}):
                await run_daily_report()
