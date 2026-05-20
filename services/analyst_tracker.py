"""PortfolioPilot - Analyst Track Record Service

Bewertet die Qualität von Analysten-Empfehlungen anhand ihres historischen
Track Records. Vergleicht vergangene Ratings mit der tatsächlichen
Kursentwicklung (3-Monats-Forward-Return).

Ergebnis: Nur Analysten mit nachweisbarem Erfolg (≥60% Success Rate,
≥3 Ratings) fließen in den "Verified Consensus" ein.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from models import AnalystData, AnalystRating, AnalystTrackRecord

logger = logging.getLogger(__name__)

# Mindestanforderungen für "verifizierte" Analysten
MIN_SUCCESS_RATE = 60.0   # Mindestens 60% Erfolgsquote
MIN_RATINGS_COUNT = 3     # Mindestens 3 bewertbare Ratings
FORWARD_DAYS = 90         # 3-Monats-Forward-Return

# Grade-Klassifizierung: Welche Grades gelten als bullish/bearish?
_BULLISH_GRADES = {
    "buy", "strong buy", "strong-buy", "outperform", "overweight",
    "positive", "accumulate", "add", "top pick",
}
_BEARISH_GRADES = {
    "sell", "strong sell", "strong-sell", "underperform", "underweight",
    "negative", "reduce", "avoid",
}
_NEUTRAL_GRADES = {
    "hold", "neutral", "equal-weight", "equal weight", "market perform",
    "sector perform", "in-line", "peer perform",
}


def _classify_grade(grade: str) -> str:
    """Klassifiziert ein Rating-Grade als 'bullish', 'bearish' oder 'neutral'."""
    g = grade.strip().lower()
    if g in _BULLISH_GRADES:
        return "bullish"
    if g in _BEARISH_GRADES:
        return "bearish"
    if g in _NEUTRAL_GRADES:
        return "neutral"
    # Heuristik für unbekannte Grades (auch mit Bindestrich)
    g_clean = g.replace("-", "")
    if "buy" in g_clean or "outperform" in g_clean or "overweight" in g_clean:
        return "bullish"
    if "sell" in g_clean or "underperform" in g_clean or "underweight" in g_clean:
        return "bearish"
    return "neutral"


def _find_price_on_date(
    historical: list[dict], target_date: str, tolerance_days: int = 5
) -> Optional[float]:
    """Findet den Schlusskurs an einem bestimmten Datum (±Toleranz).

    Historical ist sortiert nach Datum aufsteigend: [{date, close}, ...]
    """
    if not historical or not target_date:
        return None

    try:
        target = datetime.strptime(target_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None

    best_price = None
    best_delta = tolerance_days + 1

    for entry in historical:
        try:
            entry_date = datetime.strptime(entry.get("date", "")[:10], "%Y-%m-%d")
            delta = abs((entry_date - target).days)
            if delta < best_delta:
                best_delta = delta
                best_price = entry.get("close")
        except (ValueError, TypeError):
            continue

    return best_price


def evaluate_track_records(
    ratings: list[AnalystRating],
    historical_prices: list[dict],
) -> list[AnalystTrackRecord]:
    """Bewertet den Track Record jeder Analysten-Firma.

    Args:
        ratings: Liste individueller Analyst-Ratings
        historical_prices: Historische Kurse [{date, close}, ...]
                          sortiert nach Datum aufsteigend

    Returns:
        Liste von AnalystTrackRecord, sortiert nach Success Rate absteigend
    """
    if not ratings or not historical_prices:
        return []

    # Sammle Ergebnisse pro Firma
    firm_results: dict[str, list[dict]] = {}

    for rating in ratings:
        if not rating.firm or not rating.date:
            continue

        # Klassifiziere das Rating
        grade = rating.to_grade or rating.action
        if not grade:
            continue
        direction = _classify_grade(grade)
        if direction == "neutral":
            continue  # Neutrale Ratings können nicht bewertet werden

        # Finde Kurs zum Zeitpunkt des Ratings
        price_at_rating = rating.price_at_rating
        if not price_at_rating or price_at_rating <= 0:
            price_at_rating = _find_price_on_date(historical_prices, rating.date)
        if not price_at_rating or price_at_rating <= 0:
            continue

        # Finde Kurs 3 Monate nach dem Rating
        try:
            rating_date = datetime.strptime(rating.date[:10], "%Y-%m-%d")
            forward_date = rating_date + timedelta(days=FORWARD_DAYS)
            forward_date_str = forward_date.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        price_after = _find_price_on_date(historical_prices, forward_date_str, tolerance_days=10)
        if not price_after or price_after <= 0:
            continue  # Noch nicht genug Zeit vergangen

        # Berechne Return
        return_pct = ((price_after - price_at_rating) / price_at_rating) * 100

        # Bewerte Erfolg
        if direction == "bullish":
            success = return_pct > 0  # Kurs gestiegen nach Buy-Rating
        else:  # bearish
            success = return_pct < 0  # Kurs gefallen nach Sell-Rating

        firm_name = rating.firm.strip()
        if firm_name not in firm_results:
            firm_results[firm_name] = []
        firm_results[firm_name].append({
            "success": success,
            "return_pct": return_pct if direction == "bullish" else -return_pct,
            "date": rating.date,
        })

    # Aggregiere pro Firma
    track_records = []
    for firm, results in firm_results.items():
        total = len(results)
        successful = sum(1 for r in results if r["success"])
        avg_return = sum(r["return_pct"] for r in results) / total if total > 0 else 0
        last_date = max(r["date"] for r in results) if results else ""

        track_records.append(AnalystTrackRecord(
            firm=firm,
            total_ratings=total,
            successful_ratings=successful,
            success_rate=round((successful / total) * 100, 1) if total > 0 else 0,
            avg_return_pct=round(avg_return, 2),
            last_rating_date=last_date,
        ))

    # Sortiere nach Success Rate (highest first)
    track_records.sort(key=lambda x: (x.success_rate, x.avg_return_pct), reverse=True)
    return track_records


def compute_verified_consensus(
    ratings: list[AnalystRating],
    track_records: list[AnalystTrackRecord],
) -> tuple[Optional[str], Optional[float]]:
    """Berechnet den Verified Consensus nur aus Analysten mit gutem Track Record.

    Args:
        ratings: Alle individuellen Ratings
        track_records: Bewertete Track Records pro Firma

    Returns:
        (verified_consensus, verified_target_price) oder (None, None)
    """
    # Identifiziere verifizierte Firmen
    verified_firms = {
        tr.firm
        for tr in track_records
        if tr.success_rate >= MIN_SUCCESS_RATE and tr.total_ratings >= MIN_RATINGS_COUNT
    }

    if not verified_firms:
        return None, None

    # Filtere auf aktuelle Ratings (letzte 6 Monate) von verifizierten Firmen
    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    verified_ratings = [
        r for r in ratings
        if r.firm.strip() in verified_firms
        and r.date >= cutoff
        and (r.to_grade or r.action)
    ]

    if not verified_ratings:
        return None, None

    # Zähle bullish/bearish/neutral
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    price_targets = []

    for r in verified_ratings:
        grade = r.to_grade or r.action
        direction = _classify_grade(grade)
        if direction == "bullish":
            bullish_count += 1
        elif direction == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1

        if r.price_at_rating and r.price_at_rating > 0:
            price_targets.append(r.price_at_rating)

    total = bullish_count + bearish_count + neutral_count
    if total == 0:
        return None, None

    # Bestimme Consensus
    if bullish_count > (bearish_count + neutral_count):
        consensus = "Buy"
    elif bearish_count > (bullish_count + neutral_count):
        consensus = "Sell"
    else:
        consensus = "Hold"

    # Durchschnittliches Preisziel (nur wenn genug Daten)
    avg_target = round(sum(price_targets) / len(price_targets), 2) if len(price_targets) >= 2 else None

    logger.info(
        f"Verified Consensus: {consensus} "
        f"({bullish_count}B/{neutral_count}H/{bearish_count}S "
        f"von {len(verified_firms)} verifizierten Firmen)"
    )

    return consensus, avg_target


def enrich_analyst_data(
    analyst: AnalystData,
    historical_prices: list[dict],
) -> AnalystData:
    """Reichert AnalystData mit Track Record und Verified Consensus an.

    Args:
        analyst: Bestehende AnalystData mit individual_ratings
        historical_prices: Historische Kurse [{date, close}, ...]

    Returns:
        Angereicherte AnalystData mit track_records und verified_consensus
    """
    if not analyst.individual_ratings:
        return analyst

    # Track Records berechnen
    track_records = evaluate_track_records(
        analyst.individual_ratings,
        historical_prices,
    )
    analyst.track_records = track_records

    # Verified Consensus berechnen
    verified_consensus, verified_target = compute_verified_consensus(
        analyst.individual_ratings,
        track_records,
    )
    analyst.verified_consensus = verified_consensus
    analyst.verified_target_price = verified_target

    if track_records:
        verified_count = sum(
            1 for tr in track_records
            if tr.success_rate >= MIN_SUCCESS_RATE and tr.total_ratings >= MIN_RATINGS_COUNT
        )
        logger.info(
            f"Analyst Track Records: {len(track_records)} Firmen, "
            f"{verified_count} verifiziert (≥{MIN_SUCCESS_RATE}% / ≥{MIN_RATINGS_COUNT} Ratings)"
        )

    return analyst
