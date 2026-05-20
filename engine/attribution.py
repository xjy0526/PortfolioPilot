"""PortfolioPilot - Performance Attribution Engine

Zerlegt das Portfolio-P&L in seine Treiber:
  - Kursgewinne pro Position (absolut + % des Gesamt-P&L)
  - Sektor-Beitrag (aggregierte Attribution pro Sektor)
  - Dividenden-Ertrag (aus Parqet Activities)
  - Top/Flop-Ranking nach absolutem P&L-Beitrag
  - Konzentrations-Risiko (Herfindahl-Index)
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_attribution(
    stocks: list,  # list[StockFullData]
    activities: Optional[list[dict]] = None,
) -> dict:
    """Berechnet Performance Attribution für das Portfolio.

    Args:
        stocks: Liste aller StockFullData-Objekte
        activities: Parqet Activities (optional, für Dividenden)

    Returns:
        Dict mit Attribution-Daten
    """
    # Nur echte Aktien (kein CASH)
    positions = [s for s in stocks if s.position.ticker != "CASH"]
    if not positions:
        return _empty_attribution()

    total_value = sum(s.position.current_value for s in positions)
    total_cost = sum(s.position.total_cost for s in positions)
    total_pnl = total_value - total_cost

    # 1. Position-Level Attribution
    pos_attribution = []
    for s in positions:
        pos = s.position
        pnl = pos.pnl
        pnl_pct = pos.pnl_percent
        weight = pos.current_value / total_value if total_value > 0 else 0

        # Beitrag zum Gesamt-P&L (in Prozentpunkten)
        contribution_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

        pos_attribution.append({
            "ticker": pos.ticker,
            "name": pos.name,
            "sector": pos.sector or "Unknown",
            "pnl_eur": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 1),
            "weight": round(weight * 100, 1),
            "contribution_pct": round(contribution_pct, 2),
            "score": round(s.score.total_score, 1) if s.score else 0,
            "rating": s.score.rating.value if s.score else "hold",
        })

    # Sortiere nach absolutem P&L-Beitrag
    pos_attribution.sort(key=lambda x: x["pnl_eur"], reverse=True)

    # 2. Sektor-Attribution
    sector_data: dict[str, dict] = {}
    for p in pos_attribution:
        sector = p["sector"]
        if sector not in sector_data:
            sector_data[sector] = {
                "sector": sector,
                "pnl_eur": 0, "weight": 0, "contribution_pct": 0,
                "count": 0,
            }
        sd = sector_data[sector]
        sd["pnl_eur"] += p["pnl_eur"]
        sd["weight"] += p["weight"]
        sd["contribution_pct"] += p["contribution_pct"]
        sd["count"] += 1

    sector_attribution = sorted(
        [
            {k: round(v, 2) if isinstance(v, float) else v for k, v in sd.items()}
            for sd in sector_data.values()
        ],
        key=lambda x: x["pnl_eur"],
        reverse=True,
    )

    # 3. Dividenden aus Activities
    dividend_total = 0.0
    dividend_by_ticker: dict[str, float] = {}
    if activities:
        for act in activities:
            act_type = (act.get("type") or "").lower()
            if act_type == "dividend":
                amount = float(act.get("amount", 0) or 0)
                fee = float(act.get("fee", 0) or 0)
                tax = float(act.get("tax", 0) or 0)
                net = amount - fee - tax
                ticker = act.get("ticker", "")
                if net > 0:
                    dividend_total += net
                    dividend_by_ticker[ticker] = dividend_by_ticker.get(ticker, 0) + net

    dividend_positions = [
        {"ticker": t, "total_eur": round(v, 2)}
        for t, v in sorted(dividend_by_ticker.items(), key=lambda x: x[1], reverse=True)
    ]

    # 4. Top/Flop (nach absolutem P&L)
    top_3 = pos_attribution[:3]
    flop_3 = pos_attribution[-3:] if len(pos_attribution) > 3 else []
    # Flops aufsteigend (schlechtester zuerst)
    flop_3 = sorted(flop_3, key=lambda x: x["pnl_eur"])

    # 5. Konzentrations-Analyse
    # Herfindahl-Index auf P&L-Beiträge
    pnl_weights = []
    for p in pos_attribution:
        if total_pnl != 0:
            share = abs(p["pnl_eur"]) / max(abs(total_pnl), 1)
        else:
            share = 0
        pnl_weights.append(share)

    herfindahl = sum(w ** 2 for w in pnl_weights) if pnl_weights else 0
    # Normalisiert: 0 = perfekt diversifiziert, 100 = konzentriert
    n = len(pnl_weights)
    if n > 1 and herfindahl > 0:
        min_hhi = 1.0 / n
        concentration_score = round(
            min(100, max(0, (herfindahl - min_hhi) / (1 - min_hhi) * 100)), 1
        )
    else:
        concentration_score = 100.0

    # Top-3 P&L Anteil
    top3_pnl = sum(p["pnl_eur"] for p in top_3) if top_3 else 0
    top3_share = round(
        (top3_pnl / total_pnl * 100) if total_pnl != 0 else 0, 1
    )

    return {
        "total_pnl_eur": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_cost * 100) if total_cost > 0 else 0, 1),
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "positions": pos_attribution,
        "sectors": sector_attribution,
        "dividends": {
            "total_eur": round(dividend_total, 2),
            "positions": dividend_positions[:10],
        },
        "top_performers": top_3,
        "worst_performers": flop_3,
        "concentration": {
            "herfindahl_score": concentration_score,
            "top3_pnl_share": top3_share,
            "risk_level": (
                "Hoch" if concentration_score > 60
                else "Mittel" if concentration_score > 30
                else "Niedrig"
            ),
        },
        "num_positions": len(pos_attribution),
    }


def _empty_attribution() -> dict:
    return {
        "total_pnl_eur": 0, "total_pnl_pct": 0,
        "total_value": 0, "total_cost": 0,
        "positions": [], "sectors": [],
        "dividends": {"total_eur": 0, "positions": []},
        "top_performers": [], "worst_performers": [],
        "concentration": {"herfindahl_score": 0, "top3_pnl_share": 0, "risk_level": "Unbekannt"},
        "num_positions": 0,
    }
