"""PortfolioPilot - Portfolio History Engine

Speichert tägliche Portfolio-Snapshots und lädt historische Daten.
Delegiert an die zentrale SQLite-Datenbank (database.py).
"""
import logging

from database import save_snapshot, load_snapshots

logger = logging.getLogger(__name__)


def save_snapshot_compat(
    total_value: float,
    total_cost: float,
    total_pnl: float,
    num_positions: int,
    eur_usd_rate: float = 1.0,
):
    """Wrapper — delegiert an database.save_snapshot."""
    save_snapshot(total_value, total_cost, total_pnl, num_positions, eur_usd_rate)


def load_history(days: int = 90) -> list[dict]:
    """Lädt historische Portfolio-Snapshots aus SQLite."""
    return load_snapshots(days=days)


