"""Estimated history helpers for local CSV portfolios."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def build_estimated_portfolio_history(summary: Any, days: int = 90, source: str = "csv") -> list[dict]:
    """Build a deterministic, clearly-marked history series from current holdings.

    Local CSV portfolios often have only one saved snapshot. This fallback keeps
    charts useful without pretending to be market data: each point is marked as
    estimated and interpolates between cost basis and current value.
    """
    if not summary or float(getattr(summary, "total_value", 0) or 0) <= 0:
        return []

    point_count = _point_count(days)
    end_value = float(getattr(summary, "total_value", 0) or 0)
    invested = float(getattr(summary, "total_cost", 0) or 0)
    start_value = invested if invested > 0 else end_value

    today = date.today()
    history: list[dict] = []
    for idx in range(point_count):
        progress = idx / max(point_count - 1, 1)
        value = start_value + (end_value - start_value) * progress
        d = today - timedelta(days=point_count - idx - 1)
        history.append({
            "date": d.isoformat(),
            "total_value": round(value, 2),
            "invested_capital": round(invested, 2),
            "total_cost": round(invested, 2),
            "total_pnl": round(value - invested, 2),
            "num_positions": int(getattr(summary, "num_positions", 0) or 0),
            "estimated": True,
            "source": source,
        })
    return history


def build_estimated_history_detail(summary: Any, days: int = 180, source: str = "csv") -> dict:
    """Build the stacked-history response shape used by the history tab."""
    history = build_estimated_portfolio_history(summary, days=days, source=source)
    if not history:
        return {"error": "No portfolio data"}

    dates = [row["date"] for row in history]
    stocks: dict[str, dict] = {}
    stock_items = [s for s in (getattr(summary, "stocks", []) or []) if _ticker(s) != "CASH"]

    for stock in stock_items:
        pos = stock.position
        ticker = pos.ticker
        start_value = float(pos.total_cost or 0.0)
        end_value = float(pos.current_value or 0.0)
        values = []
        for idx in range(len(history)):
            progress = idx / max(len(history) - 1, 1)
            values.append(round(start_value + (end_value - start_value) * progress, 2))
        stocks[ticker] = {
            "name": pos.name or ticker,
            "values": values,
            "sector": pos.sector,
        }

    total = [row["total_value"] for row in history]
    total_cost = [row["total_cost"] for row in history]
    pnl = [round(v - c, 2) for v, c in zip(total, total_cost)]

    return {
        "dates": dates,
        "stocks": stocks,
        "total": total,
        "total_cost": total_cost,
        "pnl": pnl,
        "estimated": True,
        "source": source,
    }


def _point_count(days: int) -> int:
    if days <= 0 or days >= 9999:
        return 365
    return max(2, min(int(days), 365))


def _ticker(stock: Any) -> str:
    return str(getattr(getattr(stock, "position", None), "ticker", "") or "").upper()
