"""PortfolioPilot - Pytest Fixtures und Testdaten."""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from models import (
    PortfolioPosition,
    FundamentalData,
    AnalystData,
    YFinanceData,
    FmpRating,
    FearGreedData,
    StockScore,
    ScoreBreakdown,
    Rating,
    DividendInfo,
)


@pytest.fixture
def sample_position():
    return PortfolioPosition(
        ticker="AAPL",
        isin="US0378331005",
        name="Apple Inc.",
        shares=10.0,
        avg_cost=150.0,
        current_price=175.0,
        currency="USD",
        sector="Technology",
    )


@pytest.fixture
def sample_positions():
    return [
        PortfolioPosition(ticker="AAPL", name="Apple", shares=10, avg_cost=150, current_price=175, sector="Technology"),
        PortfolioPosition(ticker="MSFT", name="Microsoft", shares=5, avg_cost=300, current_price=350, sector="Technology"),
        PortfolioPosition(ticker="GOOGL", name="Alphabet", shares=8, avg_cost=120, current_price=140, sector="Technology"),
    ]


@pytest.fixture
def sample_fundamentals():
    return FundamentalData(
        pe_ratio=28.5,
        pb_ratio=45.0,
        ps_ratio=7.5,
        roe=1.5,
        roa=0.28,
        debt_to_equity=1.8,
        current_ratio=1.07,
        gross_margin=0.45,
        operating_margin=0.30,
        net_margin=0.25,
        revenue_growth=0.08,
        earnings_growth=0.11,
        dividend_yield=0.005,
        market_cap=2_800_000_000_000,
        beta=1.2,
        altman_z_score=5.5,
        piotroski_score=7,
    )


@pytest.fixture
def sample_analyst():
    return AnalystData(
        consensus="Buy",
        target_price=200.0,
        num_analysts=30,
        strong_buy_count=10,
        buy_count=12,
        hold_count=5,
        sell_count=2,
        strong_sell_count=1,
    )


@pytest.fixture
def sample_yfinance():
    return YFinanceData(
        recommendation_trend="Buy",
        insider_buy_count=5,
        insider_sell_count=2,
        esg_risk_score=15.0,
        earnings_growth_yoy=12.5,
    )


@pytest.fixture
def sample_fmp_rating():
    return FmpRating(
        rating="A",
        rating_score=4,
        dcf_score=4,
        roe_score=5,
        roa_score=4,
        de_score=3,
        pe_score=4,
        pb_score=3,
    )


@pytest.fixture
def sample_fear_greed():
    return FearGreedData(
        value=65,
        label="Greed",
        source="CNN",
    )


@pytest.fixture
def sample_scores():
    """Score-Dict für Rebalancer-Tests."""
    return {
        "AAPL": StockScore(
            ticker="AAPL", name="Apple", total_score=78.0, rating=Rating.BUY,
            breakdown=ScoreBreakdown(analyst_score=80, quality_score=75),
        ),
        "MSFT": StockScore(
            ticker="MSFT", name="Microsoft", total_score=55.0, rating=Rating.HOLD,
            breakdown=ScoreBreakdown(analyst_score=60, quality_score=50),
        ),
        "GOOGL": StockScore(
            ticker="GOOGL", name="Alphabet", total_score=35.0, rating=Rating.SELL,
            breakdown=ScoreBreakdown(analyst_score=30, quality_score=40),
        ),
    }
