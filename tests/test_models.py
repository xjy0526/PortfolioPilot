"""PortfolioPilot - Tests für Pydantic Models."""
import pytest
from models import (
    PortfolioPosition,
    FundamentalData,
    AnalystData,
    StockScore,
    ScoreBreakdown,
    Rating,
    StockFullData,
    DataSourceStatus,
    PortfolioSummary,
    RebalancingAction,
    RebalancingAdvice,
    DividendInfo,
    SectorAllocation,
)


class TestPortfolioPosition:
    def test_basic_creation(self, sample_position):
        assert sample_position.ticker == "AAPL"
        assert sample_position.shares == 10.0
        assert sample_position.avg_cost == 150.0
        assert sample_position.current_price == 175.0

    def test_total_cost(self, sample_position):
        assert sample_position.total_cost == 1500.0  # 10 * 150

    def test_current_value(self, sample_position):
        assert sample_position.current_value == 1750.0  # 10 * 175

    def test_pnl_positive(self, sample_position):
        assert sample_position.pnl == 250.0  # 1750 - 1500

    def test_pnl_percent(self, sample_position):
        expected = (250.0 / 1500.0) * 100  # ~16.67%
        assert abs(sample_position.pnl_percent - expected) < 0.01

    def test_pnl_negative(self):
        pos = PortfolioPosition(ticker="X", shares=5, avg_cost=100, current_price=80)
        assert pos.pnl == -100.0  # (5*80) - (5*100) = -100

    def test_pnl_percent_zero_cost(self):
        pos = PortfolioPosition(ticker="X", shares=5, avg_cost=0, current_price=50)
        assert pos.pnl_percent == 0.0

    def test_defaults(self):
        pos = PortfolioPosition(ticker="TEST")
        assert pos.name == ""
        assert pos.asset_type == "equity"
        assert pos.market == "global"
        assert pos.shares == 0.0
        assert pos.avg_cost == 0.0
        assert pos.current_price == 0.0
        assert pos.currency == "EUR"
        assert pos.sector == "Unknown"

    def test_value_eur_supports_cny(self):
        pos = PortfolioPosition(
            ticker="600519.SS",
            shares=1,
            current_price=780,
            price_currency="CNY",
        )
        assert pos.value_eur(eur_cny=7.8) == 100.0


class TestStockScore:
    def test_defaults(self):
        score = StockScore(ticker="AAPL")
        assert score.total_score == 0.0
        assert score.rating == Rating.HOLD
        assert score.confidence == 0.0

    def test_custom_score(self):
        score = StockScore(
            ticker="AAPL", total_score=85.0, rating=Rating.BUY,
            confidence=0.9, summary="Strong Buy"
        )
        assert score.total_score == 85.0
        assert score.rating == Rating.BUY


class TestScoreBreakdown:
    def test_defaults(self):
        bd = ScoreBreakdown()
        assert bd.analyst_score == 0.0
        assert bd.quality_score == 0.0
        assert bd.valuation_score == 0.0

    def test_custom(self):
        bd = ScoreBreakdown(analyst_score=80, quality_score=70, growth_score=65)
        assert bd.analyst_score == 80
        assert bd.quality_score == 70


class TestDividendInfo:
    def test_creation(self):
        div = DividendInfo(
            yield_percent=1.5, annual_dividend=3.28,
            ex_date="2024-02-09", frequency="Quarterly"
        )
        assert div.yield_percent == 1.5
        assert div.frequency == "Quarterly"

    def test_defaults(self):
        div = DividendInfo()
        assert div.yield_percent is None
        assert div.annual_dividend is None


class TestStockFullData:
    def test_minimal(self, sample_position):
        stock = StockFullData(position=sample_position)
        assert stock.fundamentals is None
        assert stock.analyst is None
        assert stock.dividend is None
        assert stock.data_sources.parqet is True

    def test_full(self, sample_position, sample_fundamentals, sample_analyst):
        stock = StockFullData(
            position=sample_position,
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
        )
        assert stock.fundamentals.pe_ratio == 28.5
        assert stock.analyst.consensus == "Buy"


class TestPortfolioSummary:
    def test_defaults(self):
        s = PortfolioSummary()
        assert s.total_value == 0.0
        assert s.is_demo is False
        assert s.display_currency == "USD"
        assert s.eur_usd_rate == 1.0

    def test_currency_fields(self):
        s = PortfolioSummary(eur_usd_rate=1.08, eur_cny_rate=7.8, display_currency="CNY")
        assert s.eur_usd_rate == 1.08
        assert s.eur_cny_rate == 7.8
        assert s.display_currency == "CNY"


class TestDataSourceStatus:
    def test_defaults(self):
        ds = DataSourceStatus()
        assert ds.parqet is True
        assert ds.fmp is False

    def test_all_active(self):
        ds = DataSourceStatus(parqet=True, fmp=True, yfinance=True, technical=True, fear_greed=True)
        assert all([ds.parqet, ds.fmp, ds.yfinance, ds.technical, ds.fear_greed])


class TestRating:
    def test_values(self):
        assert Rating.BUY.value == "buy"
        assert Rating.HOLD.value == "hold"
        assert Rating.SELL.value == "sell"

    def test_string_enum(self):
        assert str(Rating.BUY) == "Rating.BUY"
        assert Rating("buy") == Rating.BUY
