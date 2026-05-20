"""PortfolioPilot - Tests für den Analyst Track Record Service.

Tests für:
  - Track Record Berechnung (Bullish-Analyst korrekt/falsch)
  - Filtering nach Success Rate
  - Verified Consensus Berechnung
  - Edge Cases (keine Daten, zu wenige Ratings)
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import AnalystData, AnalystRating, AnalystTrackRecord
from services.analyst_tracker import (
    evaluate_track_records,
    compute_verified_consensus,
    enrich_analyst_data,
    _classify_grade,
    _find_price_on_date,
    MIN_SUCCESS_RATE,
    MIN_RATINGS_COUNT,
)


# --- Fixtures ---

@pytest.fixture
def historical_prices():
    """12 Monate historischer Kursdaten (monatlich, steigend)."""
    return [
        {"date": "2025-01-15", "close": 100.0},
        {"date": "2025-02-15", "close": 105.0},
        {"date": "2025-03-15", "close": 110.0},
        {"date": "2025-04-15", "close": 115.0},
        {"date": "2025-05-15", "close": 120.0},
        {"date": "2025-06-15", "close": 118.0},
        {"date": "2025-07-15", "close": 125.0},
        {"date": "2025-08-15", "close": 130.0},
        {"date": "2025-09-15", "close": 128.0},
        {"date": "2025-10-15", "close": 135.0},
        {"date": "2025-11-15", "close": 140.0},
        {"date": "2025-12-15", "close": 145.0},
    ]


@pytest.fixture
def declining_prices():
    """12 Monate sinkender Kursdaten."""
    return [
        {"date": "2025-01-15", "close": 150.0},
        {"date": "2025-02-15", "close": 145.0},
        {"date": "2025-03-15", "close": 140.0},
        {"date": "2025-04-15", "close": 130.0},
        {"date": "2025-05-15", "close": 125.0},
        {"date": "2025-06-15", "close": 120.0},
        {"date": "2025-07-15", "close": 115.0},
        {"date": "2025-08-15", "close": 110.0},
        {"date": "2025-09-15", "close": 105.0},
        {"date": "2025-10-15", "close": 100.0},
        {"date": "2025-11-15", "close": 95.0},
        {"date": "2025-12-15", "close": 90.0},
    ]


@pytest.fixture
def bullish_ratings():
    """Mehrere bullishe Ratings von verschiedenen Firmen."""
    return [
        AnalystRating(
            firm="Goldman Sachs", action="Buy", to_grade="Buy",
            from_grade="Hold", date="2025-01-15", price_at_rating=100.0,
        ),
        AnalystRating(
            firm="Goldman Sachs", action="Buy", to_grade="Buy",
            from_grade="Hold", date="2025-02-15", price_at_rating=105.0,
        ),
        AnalystRating(
            firm="Goldman Sachs", action="Outperform", to_grade="Outperform",
            from_grade="Neutral", date="2025-03-15", price_at_rating=110.0,
        ),
        AnalystRating(
            firm="Morgan Stanley", action="Overweight", to_grade="Overweight",
            from_grade="Equal-Weight", date="2025-01-15", price_at_rating=100.0,
        ),
        AnalystRating(
            firm="Morgan Stanley", action="Buy", to_grade="Buy",
            from_grade="Hold", date="2025-02-15", price_at_rating=105.0,
        ),
        AnalystRating(
            firm="Morgan Stanley", action="Buy", to_grade="Buy",
            from_grade="Hold", date="2025-03-15", price_at_rating=110.0,
        ),
    ]


@pytest.fixture
def bearish_ratings():
    """Bearishe Ratings (Preise passend zu declining_prices)."""
    return [
        AnalystRating(
            firm="Bear Capital", action="Sell", to_grade="Sell",
            from_grade="Hold", date="2025-01-15", price_at_rating=150.0,
        ),
        AnalystRating(
            firm="Bear Capital", action="Underperform", to_grade="Underperform",
            from_grade="Neutral", date="2025-02-15", price_at_rating=145.0,
        ),
        AnalystRating(
            firm="Bear Capital", action="Sell", to_grade="Sell",
            from_grade="Hold", date="2025-03-15", price_at_rating=140.0,
        ),
    ]


# --- Tests: Grade Classification ---

class TestClassifyGrade:
    def test_bullish_grades(self):
        assert _classify_grade("Buy") == "bullish"
        assert _classify_grade("Strong Buy") == "bullish"
        assert _classify_grade("Outperform") == "bullish"
        assert _classify_grade("Overweight") == "bullish"

    def test_bearish_grades(self):
        assert _classify_grade("Sell") == "bearish"
        assert _classify_grade("Strong Sell") == "bearish"
        assert _classify_grade("Underperform") == "bearish"
        assert _classify_grade("Underweight") == "bearish"

    def test_neutral_grades(self):
        assert _classify_grade("Hold") == "neutral"
        assert _classify_grade("Neutral") == "neutral"
        assert _classify_grade("Equal-Weight") == "neutral"

    def test_heuristic_fallback(self):
        assert _classify_grade("Strong-Buy") == "bullish"
        assert _classify_grade("Under-Perform") == "bearish"


# --- Tests: Price Lookup ---

class TestFindPriceOnDate:
    def test_exact_date(self, historical_prices):
        price = _find_price_on_date(historical_prices, "2025-03-15")
        assert price == 110.0

    def test_close_date(self, historical_prices):
        price = _find_price_on_date(historical_prices, "2025-03-13")
        assert price == 110.0  # Within tolerance

    def test_no_data(self):
        assert _find_price_on_date([], "2025-03-15") is None
        assert _find_price_on_date(None, "2025-03-15") is None

    def test_invalid_date(self, historical_prices):
        assert _find_price_on_date(historical_prices, "invalid") is None


# --- Tests: Track Record Evaluation ---

class TestEvaluateTrackRecords:
    def test_bullish_in_rising_market(self, bullish_ratings, historical_prices):
        """Bullishe Analysten in steigendem Markt → hohe Success Rate."""
        records = evaluate_track_records(bullish_ratings, historical_prices)
        assert len(records) >= 1
        # In steigendem Markt sollten Buy-Ratings erfolgreich sein
        for rec in records:
            assert rec.success_rate > 0

    def test_bearish_in_rising_market(self, historical_prices):
        """Bearishe Analysten in steigendem Markt → niedrige Success Rate."""
        # Bearish ratings mit Preisen passend zum steigenden Markt
        bearish_in_rising = [
            AnalystRating(
                firm="Bear Capital", action="Sell", to_grade="Sell",
                from_grade="Hold", date="2025-01-15", price_at_rating=100.0,
            ),
            AnalystRating(
                firm="Bear Capital", action="Underperform", to_grade="Underperform",
                from_grade="Neutral", date="2025-02-15", price_at_rating=105.0,
            ),
            AnalystRating(
                firm="Bear Capital", action="Sell", to_grade="Sell",
                from_grade="Hold", date="2025-03-15", price_at_rating=110.0,
            ),
        ]
        records = evaluate_track_records(bearish_in_rising, historical_prices)
        if records:
            bear = next((r for r in records if r.firm == "Bear Capital"), None)
            if bear:
                assert bear.success_rate < 50

    def test_bearish_in_declining_market(self, bearish_ratings, declining_prices):
        """Bearishe Analysten in sinkendem Markt → hohe Success Rate."""
        records = evaluate_track_records(bearish_ratings, declining_prices)
        if records:
            bear = next((r for r in records if r.firm == "Bear Capital"), None)
            if bear:
                assert bear.success_rate > 50

    def test_empty_ratings(self, historical_prices):
        records = evaluate_track_records([], historical_prices)
        assert records == []

    def test_no_prices(self, bullish_ratings):
        records = evaluate_track_records(bullish_ratings, [])
        assert records == []

    def test_sorted_by_success_rate(self, bullish_ratings, bearish_ratings, historical_prices):
        """Ergebnisse sollten nach Success Rate sortiert sein."""
        all_ratings = bullish_ratings + bearish_ratings
        records = evaluate_track_records(all_ratings, historical_prices)
        if len(records) >= 2:
            for i in range(len(records) - 1):
                assert records[i].success_rate >= records[i + 1].success_rate

    def test_neutral_ratings_ignored(self, historical_prices):
        """Neutrale Ratings (Hold) können nicht bewertet werden."""
        neutral = [
            AnalystRating(
                firm="Neutral Corp", action="Hold", to_grade="Hold",
                date="2025-01-15", price_at_rating=100.0,
            ),
        ]
        records = evaluate_track_records(neutral, historical_prices)
        assert records == []


# --- Tests: Verified Consensus ---

class TestComputeVerifiedConsensus:
    def test_bullish_verified(self):
        """Verifizierte Firmen mit Buy-Ratings → Verified Consensus = Buy."""
        ratings = [
            AnalystRating(firm="Good Firm", to_grade="Buy", date="2025-12-01"),
            AnalystRating(firm="Good Firm", to_grade="Buy", date="2025-11-01"),
        ]
        track_records = [
            AnalystTrackRecord(
                firm="Good Firm", total_ratings=5,
                successful_ratings=4, success_rate=80.0,
            ),
        ]
        consensus, target = compute_verified_consensus(ratings, track_records)
        assert consensus == "Buy"

    def test_firm_below_threshold_excluded(self):
        """Firmen unter dem Success-Rate-Schwellenwert werden ignoriert."""
        ratings = [
            AnalystRating(firm="Bad Firm", to_grade="Buy", date="2025-12-01"),
        ]
        track_records = [
            AnalystTrackRecord(
                firm="Bad Firm", total_ratings=5,
                successful_ratings=2, success_rate=40.0,
            ),
        ]
        consensus, target = compute_verified_consensus(ratings, track_records)
        assert consensus is None

    def test_firm_too_few_ratings_excluded(self):
        """Firmen mit zu wenigen Ratings werden ignoriert."""
        ratings = [
            AnalystRating(firm="New Firm", to_grade="Buy", date="2025-12-01"),
        ]
        track_records = [
            AnalystTrackRecord(
                firm="New Firm", total_ratings=2,
                successful_ratings=2, success_rate=100.0,
            ),
        ]
        consensus, target = compute_verified_consensus(ratings, track_records)
        assert consensus is None

    def test_empty_input(self):
        consensus, target = compute_verified_consensus([], [])
        assert consensus is None
        assert target is None


# --- Tests: Full Enrichment ---

class TestEnrichAnalystData:
    def test_enrichment_adds_track_records(self, bullish_ratings, historical_prices):
        analyst = AnalystData(
            consensus="Buy", num_analysts=10,
            individual_ratings=bullish_ratings,
        )
        enriched = enrich_analyst_data(analyst, historical_prices)
        assert len(enriched.track_records) > 0

    def test_enrichment_without_ratings(self, historical_prices):
        """Ohne individual_ratings sollte nichts verändert werden."""
        analyst = AnalystData(consensus="Buy", num_analysts=10)
        enriched = enrich_analyst_data(analyst, historical_prices)
        assert enriched.track_records == []
        assert enriched.verified_consensus is None

    def test_enrichment_preserves_existing_data(self, bullish_ratings, historical_prices):
        """Bestehende Analyst-Daten bleiben erhalten."""
        analyst = AnalystData(
            consensus="Buy", target_price=200.0, num_analysts=10,
            strong_buy_count=5, buy_count=3, hold_count=2,
            individual_ratings=bullish_ratings,
        )
        enriched = enrich_analyst_data(analyst, historical_prices)
        assert enriched.consensus == "Buy"
        assert enriched.target_price == 200.0
        assert enriched.num_analysts == 10


# --- Tests: Integration with Scoring ---

class TestScorerIntegration:
    """Tests dass der Scorer mit verified_consensus korrekt umgeht."""

    def test_verified_buy_boosts_score(self):
        from engine.scorer import _calc_analyst_score

        # Ohne Verified Consensus
        analyst_normal = AnalystData(
            consensus="Hold", num_analysts=10,
            hold_count=10,
        )
        score_normal = _calc_analyst_score(analyst_normal)

        # Mit Verified Buy Consensus
        analyst_verified = AnalystData(
            consensus="Hold", num_analysts=10,
            hold_count=10,
            verified_consensus="Buy",
        )
        score_verified = _calc_analyst_score(analyst_verified)

        assert score_verified > score_normal

    def test_verified_sell_reduces_score(self):
        from engine.scorer import _calc_analyst_score

        analyst_normal = AnalystData(
            consensus="Hold", num_analysts=10,
            hold_count=10,
        )
        score_normal = _calc_analyst_score(analyst_normal)

        analyst_verified = AnalystData(
            consensus="Hold", num_analysts=10,
            hold_count=10,
            verified_consensus="Sell",
        )
        score_verified = _calc_analyst_score(analyst_verified)

        assert score_verified < score_normal

    def test_no_verified_fallback(self):
        """Ohne verified_consensus verhält sich der Scorer wie vorher."""
        from engine.scorer import _calc_analyst_score

        analyst = AnalystData(
            consensus="Buy", target_price=200.0, num_analysts=20,
            strong_buy_count=15, buy_count=3, hold_count=2,
        )
        score = _calc_analyst_score(analyst, current_price=175.0)
        assert 60 <= score <= 100
