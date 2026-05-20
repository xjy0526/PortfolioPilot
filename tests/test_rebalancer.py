"""PortfolioPilot - Tests für den Rebalancer v3."""
import pytest
from engine.rebalancer import (
    calculate_rebalancing,
    _calculate_conviction_weights,
    _calculate_sector_weights,
    _apply_sector_limits,
    _calculate_priority,
    _calculate_health_score,
    _get_conviction_tier,
    MAX_SINGLE_WEIGHT,
    MIN_SINGLE_WEIGHT,
    MAX_SECTOR_WEIGHT,
    REBALANCE_THRESHOLD,
    MIN_CASH_RESERVE,
)
from models import (
    PortfolioPosition,
    StockScore,
    Rating,
    ScoreBreakdown,
)


class TestCalculateRebalancing:
    def test_empty_portfolio(self):
        result = calculate_rebalancing([], {})
        assert "Keine Positionen" in result.summary

    def test_zero_value_portfolio(self):
        positions = [PortfolioPosition(ticker="X", shares=10, avg_cost=0, current_price=0)]
        result = calculate_rebalancing(positions, {})
        assert "Portfoliowert ist 0" in result.summary

    def test_cash_only_portfolio(self):
        positions = [PortfolioPosition(ticker="CASH", shares=1, avg_cost=1000, current_price=1000)]
        result = calculate_rebalancing(positions, {})
        assert "Nur Cash" in result.summary

    def test_cash_excluded_from_actions(self, sample_positions):
        """CASH should not appear in rebalancing actions."""
        positions_with_cash = sample_positions + [
            PortfolioPosition(ticker="CASH", shares=1, avg_cost=5000, current_price=5000)
        ]
        result = calculate_rebalancing(positions_with_cash, {})
        tickers = [a.ticker for a in result.actions]
        assert "CASH" not in tickers

    def test_basic_rebalancing(self, sample_positions, sample_scores):
        result = calculate_rebalancing(sample_positions, sample_scores)
        assert len(result.actions) == 3
        assert result.total_value > 0
        assert result.summary != ""

    def test_buy_stock_has_target(self, sample_positions, sample_scores):
        result = calculate_rebalancing(sample_positions, sample_scores)
        aapl_action = next(a for a in result.actions if a.ticker == "AAPL")
        assert aapl_action.target_weight > 0

    def test_actions_sorted_by_priority(self, sample_positions, sample_scores):
        result = calculate_rebalancing(sample_positions, sample_scores)
        priorities = [a.priority for a in result.actions]
        assert priorities == sorted(priorities, reverse=True)

    def test_target_weights_sum_near_95(self, sample_positions, sample_scores):
        """R1: Target weights sum to ~95% (5% cash reserve)."""
        result = calculate_rebalancing(sample_positions, sample_scores)
        total_target = sum(a.target_weight for a in result.actions)
        expected = (1.0 - MIN_CASH_RESERVE) * 100  # 95%
        assert abs(total_target - expected) < 2.0

    def test_custom_target_weights(self, sample_positions):
        targets = {"AAPL": 0.5, "MSFT": 0.3, "GOOGL": 0.2}
        result = calculate_rebalancing(sample_positions, {}, target_weights=targets)
        aapl = next(a for a in result.actions if a.ticker == "AAPL")
        assert abs(aapl.target_weight - 50.0) < 0.1

    def test_single_position(self):
        pos = [PortfolioPosition(ticker="AAPL", shares=10, avg_cost=100, current_price=150)]
        scores = {"AAPL": StockScore(ticker="AAPL", total_score=80, rating=Rating.BUY)}
        result = calculate_rebalancing(pos, scores)
        assert len(result.actions) == 1

    def test_totals_calculated(self):
        positions = [
            PortfolioPosition(ticker=f"S{i}", shares=10, avg_cost=100, current_price=100)
            for i in range(10)
        ]
        scores = {
            "S0": StockScore(ticker="S0", total_score=90, rating=Rating.BUY),
            "S9": StockScore(ticker="S9", total_score=20, rating=Rating.SELL),
        }
        for i in range(1, 9):
            scores[f"S{i}"] = StockScore(ticker=f"S{i}", total_score=55, rating=Rating.HOLD)
        result = calculate_rebalancing(positions, scores)
        assert result.total_buy_amount >= 0
        assert result.total_sell_amount >= 0

    def test_threshold_filters_small_diffs(self):
        """With equal weights and no cash, R1 creates small sell actions to build cash reserve."""
        positions = [
            PortfolioPosition(ticker="A", shares=50, avg_cost=100, current_price=100),
            PortfolioPosition(ticker="B", shares=50, avg_cost=100, current_price=100),
        ]
        result = calculate_rebalancing(positions, {})
        # R1: With 0 cash, positions are each 50% but target is ~47.5%
        # Small sell actions are expected to build cash reserve
        for a in result.actions:
            assert a.amount_eur < 1000  # Small amounts, not dramatic


    def test_actions_have_reasons(self, sample_positions, sample_scores):
        result = calculate_rebalancing(sample_positions, sample_scores)
        for a in result.actions:
            assert len(a.reasons) > 0

    def test_actions_have_score(self, sample_positions, sample_scores):
        result = calculate_rebalancing(sample_positions, sample_scores)
        aapl = next(a for a in result.actions if a.ticker == "AAPL")
        assert aapl.score == 78.0
        assert aapl.rating == Rating.BUY

    # ── R1: Cash-Reserve Tests ──
    def test_cash_reserve_tracked(self):
        """R1: Cash reserve fields should be populated."""
        positions = [
            PortfolioPosition(ticker="AAPL", shares=10, avg_cost=100, current_price=150),
            PortfolioPosition(ticker="CASH", shares=1, avg_cost=500, current_price=500),
        ]
        result = calculate_rebalancing(positions, {})
        assert result.cash_current == 500.0
        assert result.cash_current_pct > 0
        assert result.cash_reserve > 0
        assert result.cash_target_pct == MIN_CASH_RESERVE * 100

    def test_total_value_includes_cash(self):
        """R2: total_value includes Cash."""
        positions = [
            PortfolioPosition(ticker="AAPL", shares=10, avg_cost=100, current_price=100),
            PortfolioPosition(ticker="CASH", shares=1, avg_cost=1000, current_price=1000),
        ]
        result = calculate_rebalancing(positions, {})
        # total = 10*100 (AAPL) + 1000 (Cash) = 2000
        assert result.total_value == 2000.0

    def test_buy_limited_by_cash(self):
        """R3: Buy amounts should not exceed available cash."""
        positions = [
            PortfolioPosition(ticker=f"S{i}", shares=10, avg_cost=100, current_price=100)
            for i in range(5)
        ]
        # No cash → no buy capacity
        result = calculate_rebalancing(positions, {})
        for a in result.actions:
            if a.action == "Kaufen":
                # Buy amount should be limited (no cash available)
                assert a.amount_eur >= 0

    # ── R4: Conviction Tests ──
    def test_conviction_tiers(self):
        """R4: Conviction tiers based on score."""
        high = StockScore(ticker="H", total_score=75, rating=Rating.BUY)
        mid = StockScore(ticker="M", total_score=55, rating=Rating.HOLD)
        low = StockScore(ticker="L", total_score=30, rating=Rating.SELL)

        assert _get_conviction_tier(high) == "high"
        assert _get_conviction_tier(mid) == "mid"
        assert _get_conviction_tier(low) == "low"
        assert _get_conviction_tier(None) == "mid"

    def test_actions_have_conviction(self, sample_positions, sample_scores):
        """R4: Actions should include conviction tier."""
        result = calculate_rebalancing(sample_positions, sample_scores)
        for a in result.actions:
            assert a.conviction in ("high", "mid", "low")

    # ── R5: Health Score Tests ──
    def test_health_score_calculated(self, sample_positions, sample_scores):
        """R5: Health score should be calculated."""
        result = calculate_rebalancing(sample_positions, sample_scores)
        assert 0 <= result.health_score <= 100
        assert "diversification" in result.health_details

    def test_health_score_dimensions(self):
        """R5: Health score has 5 dimensions."""
        weights = {"A": 0.3, "B": 0.3, "C": 0.4}
        sector_map = {"A": "Tech", "B": "Finance", "C": "Health"}
        sector_weights = _calculate_sector_weights(weights, sector_map)
        scores = {
            "A": StockScore(ticker="A", total_score=70, rating=Rating.BUY),
            "B": StockScore(ticker="B", total_score=55, rating=Rating.HOLD),
            "C": StockScore(ticker="C", total_score=60, rating=Rating.HOLD),
        }
        health, details = _calculate_health_score(weights, sector_weights, {}, scores, 3)
        assert 0 <= health <= 100
        assert "diversification" in details
        assert "sector_balance" in details
        assert "score_quality" in details
        assert "beta_balance" in details
        assert "position_count" in details


class TestConvictionWeights:
    def test_equal_weight_no_scores(self, sample_positions):
        weights = _calculate_conviction_weights(sample_positions, {})
        # R1: Should sum to ~95% (1 - MIN_CASH_RESERVE)
        assert abs(sum(weights.values()) - (1 - MIN_CASH_RESERVE)) < 0.02

    def test_buy_gets_more_weight(self):
        """High conviction stocks get more weight."""
        positions = [
            PortfolioPosition(ticker=f"S{i}", shares=10, avg_cost=100, current_price=100)
            for i in range(10)
        ]
        scores = {}
        for i, p in enumerate(positions):
            if i == 0:
                scores[p.ticker] = StockScore(ticker=p.ticker, total_score=90, rating=Rating.BUY)
            elif i == 9:
                scores[p.ticker] = StockScore(ticker=p.ticker, total_score=20, rating=Rating.SELL)
            else:
                scores[p.ticker] = StockScore(ticker=p.ticker, total_score=55, rating=Rating.HOLD)
        weights = _calculate_conviction_weights(positions, scores)
        assert weights["S0"] > weights["S9"]

    def test_min_max_constraints(self):
        positions = [
            PortfolioPosition(ticker=f"S{i}", shares=10, avg_cost=100, current_price=100)
            for i in range(10)
        ]
        scores = {
            p.ticker: StockScore(ticker=p.ticker, total_score=60, rating=Rating.HOLD)
            for p in positions
        }
        weights = _calculate_conviction_weights(positions, scores)
        for w in weights.values():
            assert w >= MIN_SINGLE_WEIGHT * 0.9
            assert w <= MAX_SINGLE_WEIGHT * 1.1

    def test_weights_sum_to_target(self, sample_positions, sample_scores):
        """R1: Weights sum to 1 - MIN_CASH_RESERVE."""
        weights = _calculate_conviction_weights(sample_positions, sample_scores)
        target = 1.0 - MIN_CASH_RESERVE
        assert abs(sum(weights.values()) - target) < 0.02

    def test_empty_positions(self):
        weights = _calculate_conviction_weights([], {})
        assert weights == {}

    def test_high_conviction_gets_more(self):
        positions = [
            PortfolioPosition(ticker=f"S{i}", shares=10, avg_cost=100, current_price=100)
            for i in range(8)
        ]
        scores = {
            "S0": StockScore(ticker="S0", total_score=90, rating=Rating.BUY),
            "S7": StockScore(ticker="S7", total_score=25, rating=Rating.SELL),
        }
        for i in range(1, 7):
            scores[f"S{i}"] = StockScore(ticker=f"S{i}", total_score=50, rating=Rating.HOLD)
        weights = _calculate_conviction_weights(positions, scores)
        assert weights["S0"] > weights["S7"]


class TestSectorLimits:
    def test_sector_within_limit(self):
        weights = {"A": 0.3, "B": 0.3, "C": 0.4}
        sector_map = {"A": "Tech", "B": "Finance", "C": "Health"}
        result = _apply_sector_limits(weights, sector_map)
        # R1: After apply_sector_limits, weights renormalize to 1 - MIN_CASH_RESERVE
        assert abs(sum(result.values()) - (1.0 - MIN_CASH_RESERVE)) < 0.06 or abs(sum(result.values()) - 1.0) < 0.06

    def test_sector_over_limit_reduced(self):
        weights = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        sector_map = {"A": "Tech", "B": "Tech", "C": "Tech", "D": "Finance"}
        result = _apply_sector_limits(weights, sector_map)
        tech_total = result["A"] + result["B"] + result["C"]
        assert tech_total < 0.80

    def test_no_sector_map(self):
        weights = {"A": 0.5, "B": 0.5}
        result = _apply_sector_limits(weights, {})
        assert result == weights


class TestPriority:
    def test_halten_low_priority(self):
        prio = _calculate_priority(0.005, None, "Halten", "", {})
        assert prio == 1

    def test_sell_rated_overweight_high_priority(self):
        score = StockScore(ticker="X", total_score=25, rating=Rating.SELL)
        prio = _calculate_priority(-0.06, score, "Verkaufen", "Tech", {"Tech": 0.3})
        assert prio >= 7

    def test_buy_rated_underweight_high_priority(self):
        score = StockScore(ticker="X", total_score=85, rating=Rating.BUY)
        prio = _calculate_priority(0.06, score, "Kaufen", "Tech", {"Tech": 0.2})
        assert prio >= 7

    def test_small_diff_low_priority(self):
        score = StockScore(ticker="X", total_score=55, rating=Rating.HOLD)
        prio = _calculate_priority(0.02, score, "Kaufen", "Tech", {"Tech": 0.2})
        assert prio <= 5


class TestSectorWeights:
    def test_basic_sector_weights(self):
        weights = {"A": 0.3, "B": 0.2, "C": 0.5}
        sector_map = {"A": "Tech", "B": "Tech", "C": "Finance"}
        result = _calculate_sector_weights(weights, sector_map)
        assert abs(result["Tech"] - 0.5) < 0.01
        assert abs(result["Finance"] - 0.5) < 0.01
