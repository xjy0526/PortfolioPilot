"""PortfolioPilot - Rebalancing Engine v3

Berechnet Portfolio-Rebalancing Empfehlungen basierend auf:
- Aktueller Allokation vs. Ziel-Allokation
- Stock Scores (Conviction-basierte Gewichtung)
- Sektor-Diversifikation (max. 35% pro Sektor)
- Risiko-Anpassung via Beta
- Cash-Reserve Management (min. 5%)
- Portfolio-Health-Score (Diversifikation, Beta, Sektor-Spread)
- Prioritätsbasierte Sortierung

v3 Änderungen (R1-R5):
  R1: Cash-Reserve — min. 5% Cash wird nicht investiert
  R2: Gesamt-Portfolio-Basis — total_value inkl. Cash
  R3: Investierbares Cash — Kaufempfehlungen auf verfügbares Cash limitiert
  R4: Conviction-based Sizing — High/Mid/Low statt Gleichgewichtung
  R5: Portfolio-Health-Score — Quantitative Diversifikations-Bewertung
"""
import logging
import math
from typing import Optional

from models import (
    PortfolioPosition,
    Rating,
    RebalancingAction,
    RebalancingAdvice,
    StockScore,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────

MAX_SINGLE_WEIGHT = 0.15   # 15% max pro Einzelaktie
MIN_SINGLE_WEIGHT = 0.02   # 2% min pro Einzelaktie
MAX_SECTOR_WEIGHT = 0.35   # 35% max pro Sektor
REBALANCE_THRESHOLD = 0.015  # 1.5% Mindestabweichung für Aktion
MIN_CASH_RESERVE = 0.05    # R1: 5% Cash-Reserve
MAX_CASH_RESERVE = 0.15    # Max Cash 15% (sonst Warnung)

# Conviction Tiers (R4)
CONVICTION_HIGH_THRESHOLD = 70  # Score >= 70 → High Conviction
CONVICTION_LOW_THRESHOLD = 45   # Score < 45 → Low Conviction

# Beta-Grenzen für Risk-Adjustment
BETA_NEUTRAL = 1.0
BETA_MAX_PENALTY = 2.0
BETA_WEIGHT_FLOOR = 0.6


def calculate_rebalancing(
    positions: list[PortfolioPosition],
    scores: dict[str, StockScore],
    target_weights: Optional[dict[str, float]] = None,
    stocks: Optional[list] = None,
) -> RebalancingAdvice:
    """Berechnet Rebalancing-Empfehlungen (v3).

    R1: Berücksichtigt Cash-Reserve (min. 5%)
    R2: total_value inkl. Cash
    R3: Kaufempfehlungen auf verfügbares Cash begrenzt
    R4: Conviction-basierte Zielgewichtung
    R5: Portfolio-Health-Score
    """
    if not positions:
        return RebalancingAdvice(summary="Keine Positionen im Portfolio")

    # ── R2: Gesamt-Portfolio inkl. Cash berechnen ──
    stock_positions = [p for p in positions if p.ticker != "CASH"]
    cash_positions = [p for p in positions if p.ticker == "CASH"]

    if not stock_positions:
        return RebalancingAdvice(summary="Nur Cash im Portfolio")

    stocks_value = sum(p.current_value for p in stock_positions)
    cash_value = sum(p.current_price for p in cash_positions)  # Cash hat price = value
    total_value = stocks_value + cash_value  # R2: Gesamtwert inkl. Cash

    if total_value <= 0:
        return RebalancingAdvice(summary="Portfoliowert ist 0")

    # ── R1: Cash-Reserve berechnen ──
    cash_current_pct = (cash_value / total_value * 100) if total_value > 0 else 0
    cash_reserve = total_value * MIN_CASH_RESERVE  # Minimum Cash
    available_to_invest = max(0, cash_value - cash_reserve)  # R3: Verfügbar

    # Build lookup maps für erweiterte Daten
    beta_map = {}
    sector_map = {}
    analyst_map = {}
    if stocks:
        for s in stocks:
            t = s.position.ticker
            if t == "CASH":
                continue
            sector_map[t] = s.position.sector or "Unknown"
            if s.fundamentals and s.fundamentals.beta is not None:
                beta_map[t] = s.fundamentals.beta
            if s.analyst:
                analyst_map[t] = s.analyst
    else:
        for p in stock_positions:
            sector_map[p.ticker] = p.sector or "Unknown"

    # Score-Änderungen aus History laden
    score_changes = _load_score_changes(scores)

    # ── Gewichtungen berechnen (R2: auf Basis total_value inkl. Cash) ──
    current_weights = {}
    for p in stock_positions:
        current_weights[p.ticker] = p.current_value / total_value  # R2: inkl. Cash-Basis

    # Zielgewichtungen (R4: Conviction-basiert)
    if target_weights:
        t_weights = target_weights
    else:
        t_weights = _calculate_conviction_weights(
            stock_positions, scores, beta_map, sector_map,
        )

    # Sektor-Analyse & Warnungen
    sector_weights = _calculate_sector_weights(current_weights, sector_map)
    sector_warnings = []
    for sector, weight in sector_weights.items():
        if weight > MAX_SECTOR_WEIGHT:
            pct = round(weight * 100, 1)
            limit_pct = round(MAX_SECTOR_WEIGHT * 100, 0)
            sector_warnings.append(
                f"⚠️ {sector}: {pct}% > {limit_pct:.0f}% Limit"
            )

    # ── Rebalancing-Aktionen berechnen (R3: Cash-limitiert) ──
    actions = []
    for p in stock_positions:
        ticker = p.ticker
        current_w = current_weights.get(ticker, 0)
        target_w = t_weights.get(ticker, current_w)
        score = scores.get(ticker)

        diff_w = target_w - current_w
        diff_amount = diff_w * total_value

        # Action bestimmen (Schwelle: 1.5%)
        if abs(diff_w) < REBALANCE_THRESHOLD:
            action_str = "Halten"
        elif diff_w > 0:
            action_str = "Kaufen"
        else:
            action_str = "Verkaufen"

        # R3: Kaufbeträge auf verfügbares Cash begrenzen
        if action_str == "Kaufen" and diff_amount > available_to_invest:
            diff_amount = available_to_invest
            if diff_amount < 50:  # Unter Mindestbetrag -> nicht empfehlen
                action_str = "Halten"
                diff_amount = 0

        # Shares delta
        shares_delta = 0.0
        if p.current_price > 0:
            shares_delta = diff_amount / p.current_price

        # R4: Conviction Tier
        conviction = _get_conviction_tier(score)

        # Begründungen
        reasons = _build_reasons(
            ticker, current_w, target_w, score,
            sector_map.get(ticker, ""),
            sector_weights,
            beta_map.get(ticker),
            analyst_map.get(ticker),
            score_changes.get(ticker),
            cash_current_pct,
        )

        reason = " | ".join(reasons[:3]) if reasons else "Keine Änderung"

        # Priorität berechnen
        priority = _calculate_priority(
            diff_w, score, action_str, sector_map.get(ticker, ""),
            sector_weights,
        )

        actions.append(RebalancingAction(
            ticker=ticker,
            name=p.name,
            current_weight=round(current_w * 100, 1),
            target_weight=round(target_w * 100, 1),
            action=action_str,
            amount_eur=round(abs(diff_amount), 2),
            shares_delta=round(shares_delta, 2),
            rating=score.rating if score else Rating.HOLD,
            reason=reason,
            priority=priority,
            sector=sector_map.get(ticker, ""),
            score=score.total_score if score else 0.0,
            score_change=score_changes.get(ticker),
            reasons=reasons,
            conviction=conviction,
        ))

    # Sortierung: Priorität → Betrag
    actions.sort(key=lambda a: (-a.priority, -abs(a.amount_eur)))

    # Gesamtbeträge
    total_buy = sum(a.amount_eur for a in actions if a.action == "Kaufen")
    total_sell = sum(a.amount_eur for a in actions if a.action == "Verkaufen")

    # ── R5: Portfolio-Health-Score ──
    health_score, health_details = _calculate_health_score(
        current_weights, sector_weights, beta_map, scores, len(stock_positions),
    )

    # Summary
    buys = [a for a in actions if a.action == "Kaufen"]
    sells = [a for a in actions if a.action == "Verkaufen"]
    holds = [a for a in actions if a.action == "Halten"]

    summary_parts = []
    if sells:
        summary_parts.append(f"📉 {len(sells)}× Reduzieren")
    if buys:
        summary_parts.append(f"📈 {len(buys)}× Aufstocken")
    if holds:
        summary_parts.append(f"✅ {len(holds)}× Halten")
    if sector_warnings:
        summary_parts.append(f"⚠️ {len(sector_warnings)} Sektor-Warnung{'en' if len(sector_warnings) > 1 else ''}")

    # Cash-Info im Summary
    if cash_current_pct < MIN_CASH_RESERVE * 100:
        summary_parts.append(f"💰 Cash {cash_current_pct:.1f}% < {MIN_CASH_RESERVE*100:.0f}% Reserve")
    elif cash_current_pct > MAX_CASH_RESERVE * 100:
        summary_parts.append(f"💰 Cash {cash_current_pct:.1f}% — Investmentchance")

    summary = " | ".join(summary_parts) if summary_parts else "Portfolio ist gut ausbalanciert ✅"

    return RebalancingAdvice(
        total_value=round(total_value, 2),
        actions=actions,
        summary=summary,
        sector_warnings=sector_warnings,
        total_buy_amount=round(total_buy, 2),
        total_sell_amount=round(total_sell, 2),
        net_rebalance=round(total_buy - total_sell, 2),
        # v3: Cash & Health
        cash_current=round(cash_value, 2),
        cash_current_pct=round(cash_current_pct, 1),
        cash_target_pct=round(MIN_CASH_RESERVE * 100, 1),
        cash_reserve=round(cash_reserve, 2),
        available_to_invest=round(available_to_invest, 2),
        health_score=round(health_score, 1),
        health_details=health_details,
    )


# ─────────────────────────────────────────────────────────────
# R4: Conviction-based Weights
# ─────────────────────────────────────────────────────────────

def _get_conviction_tier(score: Optional[StockScore]) -> str:
    """Bestimmt Conviction-Tier basierend auf Score."""
    if not score:
        return "mid"
    if score.total_score >= CONVICTION_HIGH_THRESHOLD:
        return "high"
    elif score.total_score < CONVICTION_LOW_THRESHOLD:
        return "low"
    return "mid"


def _calculate_conviction_weights(
    positions: list[PortfolioPosition],
    scores: dict[str, StockScore],
    beta_map: dict[str, float] = None,
    sector_map: dict[str, str] = None,
) -> dict[str, float]:
    """Berechnet Conviction-basierte Ziel-Gewichtungen (R4).

    High Conviction (Score >= 70): 1.5× Basis
    Mid Conviction  (Score 45-69): 1.0× Basis
    Low Conviction  (Score < 45):  0.6× Basis

    Dann: Beta-Adjustment → Sektor-Limits → Min/Max Caps
    Summe normalisiert auf (1 - Cash-Reserve), d.h. max 95% in Aktien.
    """
    n = len(positions)
    if n == 0:
        return {}

    beta_map = beta_map or {}
    sector_map = sector_map or {}

    base_weight = 1.0 / n

    raw_weights = {}
    for p in positions:
        score = scores.get(p.ticker)
        conviction = _get_conviction_tier(score)

        # R4: Conviction-basierter Multiplikator
        if conviction == "high":
            conv_mult = 1.5
        elif conviction == "low":
            conv_mult = 0.6
        else:
            conv_mult = 1.0

        # Feinere Score-Kurve innerhalb des Tiers
        if score:
            score_bonus = (score.total_score - 50) / 200  # -0.25 bis +0.25
            conv_mult += score_bonus

        # Beta-Adjustment
        beta = beta_map.get(p.ticker, BETA_NEUTRAL)
        if beta > 0:
            beta_mult = max(BETA_WEIGHT_FLOOR, BETA_NEUTRAL / max(beta, 0.5))
        else:
            beta_mult = 1.0

        raw_weights[p.ticker] = base_weight * conv_mult * beta_mult

    # Normalize — Summe = 1 - MIN_CASH_RESERVE (R1: Cash-Platz lassen)
    total_raw = sum(raw_weights.values())
    target_stocks_weight = 1.0 - MIN_CASH_RESERVE  # z.B. 95% in Aktien

    if total_raw > 0:
        normalized = {k: (v / total_raw) * target_stocks_weight for k, v in raw_weights.items()}
    else:
        normalized = {p.ticker: target_stocks_weight / n for p in positions}

    # Min/Max Caps
    for ticker in normalized:
        normalized[ticker] = max(MIN_SINGLE_WEIGHT, min(MAX_SINGLE_WEIGHT, normalized[ticker]))

    # Re-normalize nach Caps (auf target_stocks_weight)
    total = sum(normalized.values())
    if total > 0:
        normalized = {k: (v / total) * target_stocks_weight for k, v in normalized.items()}

    # Sektor-Limits
    normalized = _apply_sector_limits(normalized, sector_map)

    return normalized


def _apply_sector_limits(
    weights: dict[str, float],
    sector_map: dict[str, str],
) -> dict[str, float]:
    """Reduziert Gewichte wenn ein Sektor > MAX_SECTOR_WEIGHT ist."""
    if not sector_map:
        return weights

    sector_weights = _calculate_sector_weights(weights, sector_map)

    needs_adjustment = False
    for sector, sw in sector_weights.items():
        if sw > MAX_SECTOR_WEIGHT + 0.01:
            needs_adjustment = True
            break

    if not needs_adjustment:
        return weights

    adjusted = dict(weights)
    for _ in range(5):
        sector_weights = _calculate_sector_weights(adjusted, sector_map)
        any_over = False

        for sector, sw in sector_weights.items():
            if sw > MAX_SECTOR_WEIGHT + 0.01:
                any_over = True
                sector_tickers = [t for t, s in sector_map.items() if s == sector and t in adjusted]
                if not sector_tickers:
                    continue
                reduction_factor = MAX_SECTOR_WEIGHT / sw
                for t in sector_tickers:
                    adjusted[t] *= reduction_factor

        if not any_over:
            break

    # Re-normalize
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total * (1.0 - MIN_CASH_RESERVE) for k, v in adjusted.items()}

    return adjusted


def _calculate_sector_weights(
    weights: dict[str, float],
    sector_map: dict[str, str],
) -> dict[str, float]:
    """Berechnet die Gewichtung pro Sektor."""
    sector_weights: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sector_map.get(ticker, "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0) + weight
    return sector_weights


# ─────────────────────────────────────────────────────────────
# R5: Portfolio-Health-Score
# ─────────────────────────────────────────────────────────────

def _calculate_health_score(
    current_weights: dict[str, float],
    sector_weights: dict[str, float],
    beta_map: dict[str, float],
    scores: dict[str, StockScore],
    num_positions: int,
) -> tuple[float, dict]:
    """Berechnet Portfolio-Health-Score 0-100 (R5).

    5 Dimensionen:
      1. Diversifikation (20): HHI-basiert
      2. Sektor-Balance (20): Abweichung vom Sektor-Limit
      3. Score-Qualität (20): Durchschnitt der Stock-Scores
      4. Beta-Balance (20): Wie nah am Ziel-Beta 1.0
      5. Positions-Anzahl (20): Genug diversifiziert?
    """
    details = {}

    # 1. Diversifikation (HHI = Herfindahl-Hirschman-Index)
    # HHI = Σ(weight²). Perfekt diversifiziert → HHI = 1/n
    if current_weights:
        hhi = sum(w ** 2 for w in current_weights.values())
        n = len(current_weights)
        ideal_hhi = 1.0 / n if n > 0 else 1.0
        # Score: 100 wenn HHI nahe ideal, 0 wenn eine Position dominiert
        hhi_ratio = ideal_hhi / max(hhi, 0.001)
        div_score = min(100, hhi_ratio * 100)
    else:
        div_score = 0
    details["diversification"] = round(div_score, 0)

    # 2. Sektor-Balance
    if sector_weights:
        max_sector = max(sector_weights.values()) if sector_weights else 0
        # 100 wenn max Sektor <= 25%, 0 wenn ein Sektor >= 60%
        sector_score = max(0, min(100, (0.60 - max_sector) / 0.35 * 100))
    else:
        sector_score = 50
    details["sector_balance"] = round(sector_score, 0)

    # 3. Score-Qualität (Durchschnitt der Aktien-Scores)
    score_vals = [s.total_score for s in scores.values() if s]
    if score_vals:
        avg_score = sum(score_vals) / len(score_vals)
        quality_score = min(100, avg_score * 1.2)  # Leicht skaliert
    else:
        quality_score = 50
    details["score_quality"] = round(quality_score, 0)

    # 4. Beta-Balance (Portfolió-Beta nahe 1.0)
    if beta_map and current_weights:
        weighted_beta = sum(
            beta_map.get(t, 1.0) * w
            for t, w in current_weights.items()
        ) / max(sum(current_weights.values()), 0.01)
        # 100 wenn Beta = 1.0, fällt für Abweichung
        beta_dev = abs(weighted_beta - 1.0)
        beta_score = max(0, 100 - beta_dev * 100)
    else:
        beta_score = 70  # Default wenn kein Beta verfügbar
    details["beta_balance"] = round(beta_score, 0)

    # 5. Positions-Anzahl (ideal: 8-20)
    if num_positions >= 8 and num_positions <= 20:
        pos_score = 100
    elif num_positions >= 5:
        pos_score = 70
    elif num_positions >= 3:
        pos_score = 50
    else:
        pos_score = 30
    details["position_count"] = round(pos_score, 0)

    # Gewichteter Durchschnitt (alle 5 gleich: 20%)
    health = (
        div_score * 0.20 +
        sector_score * 0.20 +
        quality_score * 0.20 +
        beta_score * 0.20 +
        pos_score * 0.20
    )

    return health, details


# ─────────────────────────────────────────────────────────────
# Priority
# ─────────────────────────────────────────────────────────────

def _calculate_priority(
    diff_w: float,
    score: Optional[StockScore],
    action: str,
    sector: str,
    sector_weights: dict[str, float],
) -> int:
    """Berechnet Priorität 1-10 für eine Rebalancing-Aktion."""
    if action == "Halten":
        return 1

    prio = 3  # Basis

    abs_diff = abs(diff_w)
    if abs_diff > 0.05:
        prio += 3
    elif abs_diff > 0.03:
        prio += 2
    elif abs_diff > 0.015:
        prio += 1

    if score:
        if action == "Verkaufen" and score.rating == Rating.SELL:
            prio += 2
        elif action == "Kaufen" and score.rating == Rating.BUY:
            prio += 2
        elif action == "Verkaufen" and score.rating == Rating.BUY:
            prio -= 1

    sw = sector_weights.get(sector, 0)
    if sw > MAX_SECTOR_WEIGHT and action == "Verkaufen":
        prio += 1

    return max(1, min(10, prio))


# ─────────────────────────────────────────────────────────────
# Begründungen
# ─────────────────────────────────────────────────────────────

def _build_reasons(
    ticker: str,
    current_w: float,
    target_w: float,
    score: Optional[StockScore],
    sector: str,
    sector_weights: dict[str, float],
    beta: Optional[float],
    analyst: Optional[object],
    score_change: Optional[float],
    cash_pct: float = 0,
) -> list[str]:
    """Erstellt detaillierte, actionable Begründungen."""
    reasons = []
    diff_pct = (target_w - current_w) * 100

    # 1. Gewichtungs-Abweichung
    if abs(diff_pct) < 1.5:
        reasons.append("✅ Gewichtung passt")
    elif diff_pct > 0:
        reasons.append(f"📉 Untergewichtet ({current_w*100:.1f}% → {target_w*100:.1f}%)")
    else:
        reasons.append(f"📈 Übergewichtet ({current_w*100:.1f}% → {target_w*100:.1f}%)")

    # 2. Conviction-Tier
    conviction = _get_conviction_tier(score)
    if score:
        emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}[score.rating.value]
        conv_label = {"high": "⭐ High", "mid": "", "low": "⬇️ Low"}.get(conviction, "")
        score_str = f"Score: {score.total_score:.0f}/100 {emoji}"
        if conv_label:
            score_str += f" ({conv_label} Conviction)"
        if score_change is not None and abs(score_change) >= 3:
            arrow = "↑" if score_change > 0 else "↓"
            score_str += f" {arrow}{abs(score_change):.0f}"
        reasons.append(score_str)

    # 3. Sektor-Warnung
    sw = sector_weights.get(sector, 0)
    if sw > MAX_SECTOR_WEIGHT:
        reasons.append(f"⚠️ {sector}: {sw*100:.0f}% > {MAX_SECTOR_WEIGHT*100:.0f}% Limit")

    # 4. Beta-Risiko
    if beta is not None and abs(beta - 1.0) > 0.3:
        if beta > 1.3:
            reasons.append(f"⚡ High Beta ({beta:.1f}) → reduzierte Gewichtung")
        elif beta < 0.7:
            reasons.append(f"🛡️ Low Beta ({beta:.1f}) → defensiv")

    # 5. Analysten-Kontext
    if analyst and hasattr(analyst, 'num_analysts') and analyst.num_analysts > 0:
        parts = []
        if analyst.consensus:
            parts.append(analyst.consensus)
        if analyst.target_price and analyst.target_price > 0:
            parts.append(f"Zielkurs: {analyst.target_price:.0f}")
        if parts:
            reasons.append(f"👨‍💼 Analysten ({analyst.num_analysts}): {', '.join(parts)}")

    # 6. Cash-Kontext (R1)
    if diff_pct > 0 and cash_pct < MIN_CASH_RESERVE * 100:
        reasons.append(f"💰 Cash-Reserve {cash_pct:.1f}% < {MIN_CASH_RESERVE*100:.0f}% → Kauf limitiert")

    return reasons


# ─────────────────────────────────────────────────────────────
# Score-Änderungen (aus History)
# ─────────────────────────────────────────────────────────────

def _load_score_changes(scores: dict[str, StockScore]) -> dict[str, float]:
    """Lädt Score-Änderungen seit der letzten Analyse."""
    try:
        from engine.analysis import _get_latest_scores
        previous = _get_latest_scores()
        changes = {}
        for ticker, score in scores.items():
            prev = previous.get(ticker)
            if prev is not None:
                changes[ticker] = round(score.total_score - prev, 1)
        return changes
    except Exception:
        return {}
