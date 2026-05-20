"""PortfolioPilot - Tests für die Scoring Engine v4.

Angepasst für:
  - 9-Faktor System (quality, valuation, analyst, technical, growth,
    quantitative, sentiment, insider, esg)
  - _calc_analyst_score mit merged Preisziel (current_price Parameter)
  - Keine separaten _calc_fundamental_score, _calc_price_target_score,
    _calc_fmp_rating_score, _calc_sentiment_score mehr
"""
import pytest
from engine.scorer import (
    calculate_score,
    _calc_analyst_score,
    _calc_quality_score,
    _calc_valuation_score,
    _calc_quantitative_score,
    _calc_technical_score,
    _calc_growth_score,
    _calc_insider_score,
    _calc_esg_score,
    BUY_THRESHOLD,
    SELL_THRESHOLD,
)
from models import (
    AnalystData,
    FundamentalData,
    FmpRating,
    TechnicalIndicators,
    YFinanceData,
    FearGreedData,
    Rating,
)


# --- Fixtures ---

@pytest.fixture
def sample_fundamentals():
    return FundamentalData(
        pe_ratio=22.5, roe=0.25, gross_margin=0.45,
        debt_to_equity=0.6, operating_margin=0.30,
        altman_z_score=4.2, piotroski_score=7,
    )

@pytest.fixture
def sample_analyst():
    return AnalystData(
        consensus="Buy", target_price=220.0, num_analysts=30,
        strong_buy_count=15, buy_count=10, hold_count=4, sell_count=1,
    )

@pytest.fixture
def sample_technical():
    return TechnicalIndicators(
        rsi_14=52.0, sma_50=175.0, sma_200=160.0,
        price_vs_sma50=3.5, sma_cross="golden",
        momentum_30d=7.0, signal="Bullish",
    )

@pytest.fixture
def sample_yfinance():
    return YFinanceData(
        insider_buy_count=5, insider_sell_count=2, esg_risk_score=18.0,
    )

@pytest.fixture
def sample_fear_greed():
    return FearGreedData(value=62, label="Greed", source="CNN")


# --- Tests ---

class TestScoreThresholds:
    """Stellt sicher, dass Buy/Hold/Sell Schwellenwerte korrekt sind."""

    def test_buy_threshold(self):
        assert BUY_THRESHOLD == 63

    def test_sell_threshold(self):
        assert SELL_THRESHOLD == 40


class TestAnalystScore:
    def test_strong_buy_consensus(self):
        analyst = AnalystData(
            consensus="Buy", num_analysts=20,
            strong_buy_count=15, buy_count=3, hold_count=2,
        )
        score = _calc_analyst_score(analyst)
        assert score >= 70

    def test_sell_consensus(self):
        analyst = AnalystData(
            consensus="Sell", num_analysts=10,
            sell_count=5, strong_sell_count=3, hold_count=2,
        )
        score = _calc_analyst_score(analyst)
        assert score <= 40

    def test_no_analysts(self):
        analyst = AnalystData(num_analysts=0)
        score = _calc_analyst_score(analyst)
        assert score == 50

    def test_empty_analyst(self):
        analyst = AnalystData()
        score = _calc_analyst_score(analyst)
        assert 0 <= score <= 100

    def test_with_price_target_upside(self):
        """Merged Preisziel: 50% Upside sollte den Score erhöhen."""
        analyst = AnalystData(
            consensus="Hold", target_price=150.0, num_analysts=5,
            hold_count=5,
        )
        score_with = _calc_analyst_score(analyst, current_price=100.0)
        score_without = _calc_analyst_score(analyst, current_price=0.0)
        assert score_with > score_without

    def test_with_price_target_downside(self):
        """Merged Preisziel: 30% Downside sollte den Score senken."""
        analyst = AnalystData(
            consensus="Hold", target_price=70.0, num_analysts=5,
            hold_count=5,
        )
        score = _calc_analyst_score(analyst, current_price=100.0)
        assert score < 50


class TestQualityScore:
    def test_strong_quality(self, sample_fundamentals):
        score = _calc_quality_score(sample_fundamentals)
        assert 40 <= score <= 100
        assert isinstance(score, float)

    def test_empty_fundamentals(self):
        fd = FundamentalData()
        score = _calc_quality_score(fd)
        assert score == 50

    def test_high_roe(self):
        fd = FundamentalData(roe=0.35, gross_margin=0.60, operating_margin=0.30)
        score = _calc_quality_score(fd)
        assert score >= 60


class TestValuationScore:
    def test_cheap_valuation(self):
        fd = FundamentalData(pe_ratio=12)
        score = _calc_valuation_score(fd, sector="")
        assert score >= 60

    def test_expensive_valuation(self):
        fd = FundamentalData(pe_ratio=80)
        score = _calc_valuation_score(fd, sector="")
        assert score <= 40

    def test_sector_based_tech(self):
        """Tech-Aktien haben höhere P/E Schwellen."""
        fd = FundamentalData(pe_ratio=30)
        tech_score = _calc_valuation_score(fd, sector="Technology")
        default_score = _calc_valuation_score(fd, sector="")
        assert tech_score >= default_score


class TestQuantitativeScore:
    """Tests für den Quantitative Score (Altman Z + Piotroski)."""

    def test_strong_quantitative(self):
        fd = FundamentalData(altman_z_score=4.5, piotroski_score=8)
        score = _calc_quantitative_score(fd)
        assert score >= 80

    def test_weak_quantitative(self):
        fd = FundamentalData(altman_z_score=1.2, piotroski_score=2)
        score = _calc_quantitative_score(fd)
        assert score <= 40

    def test_no_data(self):
        fd = FundamentalData()
        score = _calc_quantitative_score(fd)
        assert score == 50


class TestTechnicalScore:
    """Tests für den Technical Score (RSI + SMA + Momentum)."""

    def test_bullish_technicals(self):
        tech = TechnicalIndicators(
            rsi_14=45, sma_cross="golden",
            momentum_30d=12, price_vs_sma50=5.0,
        )
        score = _calc_technical_score(tech)
        assert score >= 60

    def test_bearish_technicals(self):
        tech = TechnicalIndicators(
            rsi_14=78, sma_cross="death",
            momentum_30d=-12, price_vs_sma50=-8.0,
        )
        score = _calc_technical_score(tech)
        assert score <= 35

    def test_neutral_technicals(self):
        tech = TechnicalIndicators(
            rsi_14=50, sma_cross="neutral",
            momentum_30d=0, price_vs_sma50=0,
        )
        score = _calc_technical_score(tech)
        assert 40 <= score <= 60

    def test_oversold_rsi(self):
        """Überverkauft (RSI < 30) sollte zu höherem Score führen (Kaufchance)."""
        tech = TechnicalIndicators(rsi_14=22)
        score = _calc_technical_score(tech)
        assert score >= 75


class TestGrowthScore:
    def test_strong_growth(self):
        fd = FundamentalData(revenue_growth=0.25, roic=0.20)
        yf = YFinanceData(earnings_growth_yoy=30.0)
        score = _calc_growth_score(fd, yf)
        assert score >= 65

    def test_no_growth_data(self):
        fd = FundamentalData()
        score = _calc_growth_score(fd)
        assert score == 50


class TestEsgScore:
    def test_low_risk(self):
        score = _calc_esg_score(5.0)
        assert score >= 80

    def test_high_risk(self):
        score = _calc_esg_score(45.0)
        assert score <= 30

    def test_medium_risk(self):
        score = _calc_esg_score(25.0)
        assert 30 <= score <= 70


class TestInsiderScore:
    def test_many_buys(self):
        yf = YFinanceData(insider_buy_count=10, insider_sell_count=1)
        score = _calc_insider_score(yf)
        assert score >= 60

    def test_many_sells(self):
        yf = YFinanceData(insider_buy_count=1, insider_sell_count=10)
        score = _calc_insider_score(yf)
        assert score <= 45

    def test_no_activity(self):
        yf = YFinanceData(insider_buy_count=0, insider_sell_count=0)
        score = _calc_insider_score(yf)
        assert score == 50


class TestCalculateScore:
    """Integration-Tests für die Gesamtscore-Berechnung."""

    def test_full_data_score(
        self, sample_fundamentals, sample_analyst, sample_technical,
        sample_yfinance, sample_fear_greed,
    ):
        result = calculate_score(
            ticker="AAPL",
            name="Apple Inc.",
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
            current_price=175.0,
            yfinance_data=sample_yfinance,
            fear_greed=sample_fear_greed,
            technical=sample_technical,
        )
        assert result.ticker == "AAPL"
        assert 0 <= result.total_score <= 100
        assert result.rating in (Rating.BUY, Rating.HOLD, Rating.SELL)
        assert result.confidence > 0

    def test_no_data_returns_hold(self):
        result = calculate_score(
            ticker="UNKNOWN", name="Unknown Corp.",
            fundamentals=None, analyst=None,
        )
        assert result.rating == Rating.HOLD
        assert result.total_score == 50.0 or result.confidence == 0

    def test_score_has_breakdown(self, sample_fundamentals, sample_analyst, sample_technical):
        result = calculate_score(
            ticker="AAPL", name="Apple",
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
            current_price=175.0,
            technical=sample_technical,
        )
        bd = result.breakdown
        assert isinstance(bd.analyst_score, float)
        assert isinstance(bd.quantitative_score, float)
        assert isinstance(bd.technical_score, float)
        assert result.summary != ""

    def test_score_range(self, sample_fundamentals, sample_analyst, sample_technical):
        result = calculate_score(
            ticker="AAPL", name="Apple",
            fundamentals=sample_fundamentals,
            analyst=sample_analyst,
            current_price=175.0,
            technical=sample_technical,
        )
        assert 0 <= result.total_score <= 100
        for field in ['analyst_score', 'quantitative_score', 'technical_score']:
            val = getattr(result.breakdown, field)
            assert 0 <= val <= 100, f"{field} out of range: {val}"

    def test_good_stock_gets_buy(self):
        """Eine typisch gute Aktie (AAPL/MSFT-Level) sollte BUY bekommen."""
        fd = FundamentalData(
            pe_ratio=28, roe=0.22, gross_margin=0.55, operating_margin=0.30,
            debt_to_equity=0.5, net_margin=0.20, altman_z_score=3.5,
            piotroski_score=7, ev_to_ebitda=20, free_cashflow_yield=0.04,
            peg_ratio=1.5, roic=0.18, revenue_growth=0.10,
        )
        analyst = AnalystData(
            consensus="Buy", target_price=220, num_analysts=25,
            strong_buy_count=10, buy_count=10, hold_count=5,
        )
        tech = TechnicalIndicators(
            rsi_14=55, sma_cross="golden", momentum_30d=5,
            price_vs_sma50=3, momentum_90d=8, momentum_180d=15,
        )
        yf = YFinanceData(
            insider_buy_count=3, insider_sell_count=4,
            esg_risk_score=15, earnings_growth_yoy=12,
            earnings_beat_rate=75,
        )
        fg = FearGreedData(value=50, label="Neutral")

        result = calculate_score(
            ticker="GOOD", name="Good Tech Corp",
            fundamentals=fd, analyst=analyst,
            current_price=190, yfinance_data=yf,
            fear_greed=fg, technical=tech, sector="Technology",
        )
        assert result.rating == Rating.BUY, (
            f"Expected BUY but got {result.rating.value} "
            f"(score={result.total_score}, threshold={BUY_THRESHOLD})"
        )
        assert result.total_score >= BUY_THRESHOLD
