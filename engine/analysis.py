"""PortfolioPilot - Analyse Engine

Erstellt vollständige Portfolio-Analyse-Reports mit Score-Historie
und Trend-Erkennung. Unterstützt 3 Analyse-Level:
  - full:  Alle Datenquellen frisch laden (FMP, yFinance, Technical, Fear&Greed)
  - mid:   Nur Price Target, Fear&Greed, yFinance (FMP-Fundamentals aus Cache)
  - light: Nur Preis-Update und Score-Neuberechnung mit gecachten Daten
"""
import logging
from datetime import datetime
from typing import Optional

import database as db
from config import settings
from models import (
    AnalysisReport,
    PositionAnalysis,
    Rating,
    StockScore,
)
from engine.scorer import BUY_THRESHOLD, SELL_THRESHOLD

logger = logging.getLogger(__name__)


def build_analysis_report(
    stocks_with_scores: list,  # list[StockFullData]
    analysis_level: str = "full",
    total_portfolio_value: float = 0.0,
) -> AnalysisReport:
    """Erstellt einen vollständigen Analyse-Report aus den Scoring-Ergebnissen.

    Args:
        stocks_with_scores: Liste von StockFullData mit berechneten Scores
        analysis_level: "full", "mid", oder "light"
        total_portfolio_value: Gesamtwert des Portfolios in EUR

    Returns:
        AnalysisReport mit Rankings, Trends und Zusammenfassung
    """
    # Lade letzte Scores für Trend-Berechnung
    previous_scores = _get_latest_scores()

    positions = []
    score_sum = 0.0
    weight_sum = 0.0
    data_quality = {
        "fmp": 0, "technical": 0, "yfinance": 0,
        "fear_greed": 0, "total": 0,
    }

    for stock in stocks_with_scores:
        if stock.position.ticker == "CASH" or not stock.score:
            continue

        score = stock.score
        ticker = stock.position.ticker

        # Portfolio-Gewicht
        pos_value = stock.position.current_value
        weight = (pos_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0

        # Vorheriger Score für Trend
        prev = previous_scores.get(ticker)
        change = round(score.total_score - prev, 1) if prev is not None else None

        pa = PositionAnalysis(
            ticker=ticker,
            name=stock.position.name,
            asset_type=stock.position.asset_type,
            market=stock.position.market,
            score=score.total_score,
            previous_score=prev,
            score_change=change,
            rating=score.rating,
            breakdown=score.breakdown,
            confidence=score.confidence,
            weight_in_portfolio=round(weight, 1),
            current_price=stock.position.current_price,
            summary=score.summary,
        )
        positions.append(pa)

        # Gewichteter Portfolio-Score
        score_sum += score.total_score * weight
        weight_sum += weight

        # Data quality tracking
        ds = stock.data_sources
        data_quality["total"] += 1
        if ds.fmp:
            data_quality["fmp"] += 1
        if ds.technical:
            data_quality["technical"] += 1
        if ds.yfinance:
            data_quality["yfinance"] += 1
        if ds.fear_greed:
            data_quality["fear_greed"] += 1

    # Portfolio-Gesamtscore (gewichtet nach Positionsgröße)
    portfolio_score = round(score_sum / weight_sum, 1) if weight_sum > 0 else 50.0

    # Portfolio-Rating (synchron mit scorer.py Schwellenwerten)
    if portfolio_score >= BUY_THRESHOLD:
        portfolio_rating = Rating.BUY
    elif portfolio_score < SELL_THRESHOLD:
        portfolio_rating = Rating.SELL
    else:
        portfolio_rating = Rating.HOLD

    # Top 3 Kaufsignale (höchste Scores)
    sorted_by_score = sorted(positions, key=lambda p: p.score, reverse=True)
    top_buys = [p for p in sorted_by_score[:3] if p.rating == Rating.BUY] or sorted_by_score[:3]

    # Top 3 Verkaufssignale (niedrigste Scores)
    top_sells = [p for p in sorted_by_score[-3:] if p.rating == Rating.SELL] or sorted_by_score[-3:]

    # Größte Score-Änderungen
    with_changes = [p for p in positions if p.score_change is not None]
    biggest_changes = sorted(with_changes, key=lambda p: abs(p.score_change or 0), reverse=True)[:3]

    # Durchschnittliche Confidence
    confidences = [p.confidence for p in positions]
    avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0

    # Zusammenfassung
    summary = _build_report_summary(portfolio_score, portfolio_rating, positions, biggest_changes)

    report = AnalysisReport(
        analysis_level=analysis_level,
        portfolio_score=portfolio_score,
        portfolio_rating=portfolio_rating,
        num_positions=len(positions),
        positions=positions,
        top_buys=top_buys,
        top_sells=top_sells,
        biggest_changes=biggest_changes,
        avg_confidence=avg_confidence,
        data_quality=data_quality,
        summary=summary,
    )

    # Speichere Report in Historie
    save_analysis(report)

    return report


def save_analysis(report: AnalysisReport):
    """Speichert einen Analyse-Report in SQLite."""
    try:
        scores = {
            p.ticker: {
                "score": p.score,
                "rating": p.rating.value,
                "confidence": p.confidence,
            }
            for p in report.positions
        }
        db.save_analysis_report(
            timestamp=report.timestamp.isoformat(),
            level=report.analysis_level,
            portfolio_score=report.portfolio_score,
            portfolio_rating=report.portfolio_rating.value,
            num_positions=report.num_positions,
            avg_confidence=report.avg_confidence,
            scores=scores,
        )
    except Exception as e:
        logger.warning(f"Analyse-Report Speicherung fehlgeschlagen: {e}")


def get_analysis_history(days: int = 30) -> list[dict]:
    """Liest die Analyse-Historie der letzten X Tage aus SQLite."""
    return db.get_analysis_history(days=days)


def get_score_trend(ticker: str, days: int = 7) -> list[dict]:
    """Gibt Score-Trend für einen einzelnen Ticker zurück."""
    return db.get_score_trend(ticker, days=days)


def _get_latest_scores() -> dict[str, float]:
    """Holt die Scores aus dem letzten Analyse-Report."""
    return db.get_latest_scores()


def _build_report_summary(
    portfolio_score: float,
    portfolio_rating: Rating,
    positions: list[PositionAnalysis],
    biggest_changes: list[PositionAnalysis],
) -> str:
    """Erstellt eine lesbare Zusammenfassung des Reports."""
    emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}[portfolio_rating.value]

    buy_count = sum(1 for p in positions if p.rating == Rating.BUY)
    hold_count = sum(1 for p in positions if p.rating == Rating.HOLD)
    sell_count = sum(1 for p in positions if p.rating == Rating.SELL)

    parts = [
        f"{emoji} Portfolio-Score: {portfolio_score:.1f}/100",
        f"({buy_count}× BUY, {hold_count}× HOLD, {sell_count}× SELL)",
    ]

    if biggest_changes:
        changes = []
        for p in biggest_changes[:2]:
            if p.score_change and abs(p.score_change) >= 3:
                arrow = "↑" if p.score_change > 0 else "↓"
                changes.append(f"{p.ticker} {arrow}{abs(p.score_change):.0f}")
        if changes:
            parts.append(f"Größte Änderungen: {', '.join(changes)}")

    return " | ".join(parts)
