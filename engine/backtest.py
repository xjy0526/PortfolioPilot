"""PortfolioPilot - Score Backtesting Engine (A1)

Misst die Prädiktivität der Score-Engine durch Vergleich
historischer Scores mit tatsächlicher Kursperformance.

Funktionsweise:
  1. Lädt Score-Historie (score_history.json)
  2. Für jede historische Score-Messung: holt Kurs X Tage später
  3. Vergleicht Rating (BUY/HOLD/SELL) mit tatsächlicher Performance
  4. Berechnet Hit-Rate, Avg Return per Rating, Score-Correlation

Wird über /api/backtest aufgerufen (max 1x täglich, gecacht).
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

BACKTEST_CACHE_FILE = settings.CACHE_DIR / "backtest_results.json"


def run_backtest(lookback_days: int = 30, forward_days: int = 14) -> dict:
    """Führt Score-Backtesting durch.

    Args:
        lookback_days: Wie weit zurück Score-Snapshots betrachtet werden
        forward_days: Tage nach Score-Messung für Performance-Messung

    Returns:
        Dict mit Backtest-Ergebnissen (hit_rate, returns_by_rating, etc.)
    """
    # Gecachtes Ergebnis laden (max 1 Berechnung pro Tag)
    cached = _load_cached_results()
    if cached:
        return cached

    from engine.analysis import get_analysis_history

    history = get_analysis_history(days=lookback_days + forward_days)
    if len(history) < 2:
        return {"error": "Nicht genug Score-Historie für Backtesting", "entries": len(history)}

    # Score-Snapshots sammeln: {ticker: [{date, score, rating}]}
    ticker_snapshots: dict[str, list[dict]] = {}
    for entry in history:
        ts = entry.get("timestamp", "")
        scores = entry.get("scores", {})
        for ticker, data in scores.items():
            if ticker not in ticker_snapshots:
                ticker_snapshots[ticker] = []
            ticker_snapshots[ticker].append({
                "date": ts[:10] if ts else "",
                "score": data.get("score", 50),
                "rating": data.get("rating", "hold"),
            })

    # Kurs-Performance nach X Tagen holen (via yfinance)
    results_by_rating = {"buy": [], "hold": [], "sell": []}
    total_predictions = 0
    correct_predictions = 0

    for ticker, snapshots in ticker_snapshots.items():
        if len(snapshots) < 2:
            continue

        # Vergleiche älteste Snapshots mit neuesten Kursen
        for i, snap in enumerate(snapshots[:-1]):
            # Suche forward_days spätere Messung
            later = [s for s in snapshots[i+1:] if _days_between(snap["date"], s["date"]) >= forward_days]
            if not later:
                continue
            next_snap = later[0]

            # Score-basierte Prognose vs. tatsächliche Score-Änderung
            score_change = next_snap["score"] - snap["score"]
            rating = snap["rating"]

            # "Korrekt" = BUY und Score steigt ODER SELL und Score fällt
            if rating == "buy":
                correct = score_change >= 0
                results_by_rating["buy"].append(score_change)
            elif rating == "sell":
                correct = score_change <= 0
                results_by_rating["sell"].append(score_change)
            else:
                correct = abs(score_change) < 10  # HOLD = stabil
                results_by_rating["hold"].append(score_change)

            total_predictions += 1
            if correct:
                correct_predictions += 1

    if total_predictions == 0:
        return {"error": "Nicht genug Datenpunkte für Backtesting", "entries": len(history)}

    # Ergebnis zusammenstellen
    result = {
        "hit_rate": round(correct_predictions / total_predictions * 100, 1),
        "total_predictions": total_predictions,
        "correct_predictions": correct_predictions,
        "lookback_days": lookback_days,
        "forward_days": forward_days,
        "ratings": {
            rating: {
                "count": len(changes),
                "avg_score_change": round(sum(changes) / len(changes), 1) if changes else 0,
                "positive_pct": round(
                    sum(1 for c in changes if c > 0) / len(changes) * 100, 1
                ) if changes else 0,
            }
            for rating, changes in results_by_rating.items()
        },
        "tickers_analyzed": len(ticker_snapshots),
        "timestamp": datetime.now().isoformat(),
    }

    # Cache speichern
    _save_cached_results(result)
    logger.info(f"📊 Backtest: {result['hit_rate']}% Hit-Rate ({total_predictions} Vorhersagen)")

    return result


def _days_between(date1: str, date2: str) -> int:
    """Berechnet Tage zwischen zwei ISO-Datum-Strings."""
    try:
        d1 = datetime.fromisoformat(date1[:10])
        d2 = datetime.fromisoformat(date2[:10])
        return abs((d2 - d1).days)
    except (ValueError, TypeError):
        return 0


def _load_cached_results() -> Optional[dict]:
    """Lädt gecachte Backtest-Ergebnisse (max 1 Tag alt)."""
    if not BACKTEST_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(BACKTEST_CACHE_FILE.read_text(encoding="utf-8"))
        ts = data.get("timestamp", "")
        if ts:
            cached_date = datetime.fromisoformat(ts).date()
            if cached_date == datetime.now().date():
                return data
    except Exception:
        pass
    return None


def _save_cached_results(results: dict):
    """Speichert Backtest-Ergebnisse auf Disk."""
    BACKTEST_CACHE_FILE.write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8",
    )
