"""PortfolioPilot - SQLite Persistence Layer

Ersetzt JSON-Dateien für persistente Daten:
  - Portfolio-Snapshots (tägliche Werte)
  - Score-History (Ticker-Scores pro Analyse)

Datenbank: cache/portfoliopilot.db
Für Cloud Run Persistenz: Litestream → GCS Backup.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

TZ_BERLIN = ZoneInfo("Europe/Berlin")

DB_PATH = settings.CACHE_DIR / "portfoliopilot.db"

# Thread-local connections (sqlite3 ist nicht thread-safe)
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Gibt eine Thread-lokale SQLite-Verbindung zurück."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def init_db():
    """Erstellt Tabellen falls sie nicht existieren."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            date TEXT PRIMARY KEY,
            total_value REAL NOT NULL,
            total_cost REAL NOT NULL,
            total_pnl REAL NOT NULL,
            num_positions INTEGER NOT NULL,
            eur_usd_rate REAL DEFAULT 1.0,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            score REAL NOT NULL,
            rating TEXT NOT NULL,
            confidence REAL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_score_ticker ON score_history(ticker);
        CREATE INDEX IF NOT EXISTS idx_score_timestamp ON score_history(timestamp);

        CREATE TABLE IF NOT EXISTS analysis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            portfolio_score REAL NOT NULL,
            portfolio_rating TEXT NOT NULL,
            num_positions INTEGER NOT NULL,
            avg_confidence REAL DEFAULT 0
        );

        -- ── Shadow Portfolio Agent Tables ──────────────────────────

        CREATE TABLE IF NOT EXISTS shadow_portfolio (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            shares REAL NOT NULL DEFAULT 0,
            avg_cost_eur REAL NOT NULL DEFAULT 0,
            current_price_eur REAL NOT NULL DEFAULT 0,
            sector TEXT NOT NULL DEFAULT 'Unknown',
            first_bought TEXT NOT NULL,
            last_updated TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shadow_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            shares REAL NOT NULL,
            price_eur REAL NOT NULL,
            total_eur REAL NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            score REAL DEFAULT NULL,
            confidence REAL DEFAULT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_shadow_tx_ticker ON shadow_transactions(ticker);
        CREATE INDEX IF NOT EXISTS idx_shadow_tx_timestamp ON shadow_transactions(timestamp);

        CREATE TABLE IF NOT EXISTS shadow_performance (
            date TEXT PRIMARY KEY,
            total_value_eur REAL NOT NULL,
            cash_eur REAL NOT NULL,
            invested_eur REAL NOT NULL,
            pnl_eur REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            num_positions INTEGER NOT NULL,
            real_portfolio_value REAL DEFAULT 0,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shadow_decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cycle_summary TEXT NOT NULL,
            trades_executed INTEGER NOT NULL DEFAULT 0,
            candidates_evaluated INTEGER NOT NULL DEFAULT 0,
            ai_reasoning TEXT NOT NULL DEFAULT '',
            total_value_eur REAL NOT NULL DEFAULT 0,
            cash_eur REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS shadow_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    logger.info(f"📦 SQLite-Datenbank initialisiert: {DB_PATH}")


# ─── Portfolio Snapshots ────────────────────────────────────

def save_snapshot(
    total_value: float,
    total_cost: float,
    total_pnl: float,
    num_positions: int,
    eur_usd_rate: float = 1.0,
):
    """Speichert einen täglichen Portfolio-Snapshot (UPSERT)."""
    today = datetime.now(tz=TZ_BERLIN).strftime("%Y-%m-%d")
    ts = datetime.now(tz=TZ_BERLIN).isoformat()

    conn = _get_conn()
    conn.execute(
        """INSERT INTO portfolio_snapshots (date, total_value, total_cost, total_pnl, num_positions, eur_usd_rate, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             total_value=excluded.total_value,
             total_cost=excluded.total_cost,
             total_pnl=excluded.total_pnl,
             num_positions=excluded.num_positions,
             eur_usd_rate=excluded.eur_usd_rate,
             timestamp=excluded.timestamp""",
        (today, round(total_value, 2), round(total_cost, 2),
         round(total_pnl, 2), num_positions, eur_usd_rate, ts),
    )
    conn.commit()
    logger.info(f"📸 Snapshot gespeichert: {today} — ${total_value * eur_usd_rate:,.2f} USD")


def load_snapshots(days: int = 90) -> list[dict]:
    """Lädt historische Portfolio-Snapshots."""
    conn = _get_conn()
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE date >= ? ORDER BY date",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY date"
        ).fetchall()

    return [dict(r) for r in rows]


# ─── Score History ──────────────────────────────────────────

def save_scores(timestamp: str, scores: dict[str, dict]):
    """Speichert Score-Snapshot für alle Ticker.

    Args:
        timestamp: ISO timestamp der Analyse
        scores: {ticker: {score, rating, confidence}}
    """
    conn = _get_conn()
    rows = [
        (timestamp, ticker, data["score"], data["rating"], data.get("confidence", 0))
        for ticker, data in scores.items()
    ]
    conn.executemany(
        "INSERT INTO score_history (timestamp, ticker, score, rating, confidence) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def save_analysis_report(
    timestamp: str,
    level: str,
    portfolio_score: float,
    portfolio_rating: str,
    num_positions: int,
    avg_confidence: float,
    scores: dict[str, dict],
):
    """Speichert einen kompletten Analyse-Report (Report-Metadaten + Ticker-Scores)."""
    conn = _get_conn()
    conn.execute(
        """INSERT INTO analysis_reports (timestamp, level, portfolio_score, portfolio_rating, num_positions, avg_confidence)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (timestamp, level, portfolio_score, portfolio_rating, num_positions, avg_confidence),
    )
    save_scores(timestamp, scores)
    conn.commit()
    logger.info(f"📊 Analyse-Report gespeichert: Score {portfolio_score:.1f} ({portfolio_rating})")


def get_analysis_history(days: int = 30) -> list[dict]:
    """Liest Analyse-History inkl. Scores.

    Rückgabe-Format kompatibel mit dem bisherigen JSON-Format:
    [{timestamp, level, portfolio_score, portfolio_rating, scores: {ticker: {score, rating, confidence}}}]
    """
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    reports = conn.execute(
        "SELECT * FROM analysis_reports WHERE timestamp >= ? ORDER BY timestamp",
        (cutoff,),
    ).fetchall()

    result = []
    for r in reports:
        # Scores für diesen Timestamp laden
        scores_rows = conn.execute(
            "SELECT ticker, score, rating, confidence FROM score_history WHERE timestamp = ?",
            (r["timestamp"],),
        ).fetchall()

        scores = {
            s["ticker"]: {"score": s["score"], "rating": s["rating"], "confidence": s["confidence"]}
            for s in scores_rows
        }

        result.append({
            "timestamp": r["timestamp"],
            "level": r["level"],
            "portfolio_score": r["portfolio_score"],
            "portfolio_rating": r["portfolio_rating"],
            "num_positions": r["num_positions"],
            "avg_confidence": r["avg_confidence"],
            "scores": scores,
        })

    return result


def get_score_trend(ticker: str, days: int = 7) -> list[dict]:
    """Score-Verlauf für einen einzelnen Ticker."""
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT timestamp, score, rating FROM score_history WHERE ticker = ? AND timestamp >= ? ORDER BY timestamp",
        (ticker, cutoff),
    ).fetchall()

    return [dict(r) for r in rows]


def get_latest_scores() -> dict[str, float]:
    """Holt die neuesten Scores pro Ticker."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT ticker, score FROM score_history
           WHERE timestamp = (SELECT MAX(timestamp) FROM score_history)""",
    ).fetchall()

    return {r["ticker"]: r["score"] for r in rows}


# ─── Migration: JSON → SQLite ──────────────────────────────

def migrate_json_to_sqlite():
    """Importiert bestehende JSON-History-Dateien in SQLite (einmalig)."""
    migrated = 0

    # 1. Portfolio-Snapshots
    history_file = settings.CACHE_DIR / "portfolio_history.json"
    if history_file.exists():
        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for entry in data:
                    try:
                        save_snapshot(
                            total_value=entry.get("total_value", 0),
                            total_cost=entry.get("total_cost", 0),
                            total_pnl=entry.get("total_pnl", 0),
                            num_positions=entry.get("num_positions", 0),
                            eur_usd_rate=entry.get("eur_usd_rate", 1.0),
                        )
                        migrated += 1
                    except Exception:
                        pass
            # Rename to .bak after successful migration
            history_file.rename(history_file.with_suffix(".json.bak"))
            logger.info(f"📦 {migrated} Portfolio-Snapshots von JSON migriert")
        except Exception as e:
            logger.warning(f"JSON-Migration Portfolio fehlgeschlagen: {e}")

    # 2. Analysis History
    analysis_file = settings.CACHE_DIR / "analysis_history.json"
    if analysis_file.exists():
        try:
            data = json.loads(analysis_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for entry in data:
                    try:
                        save_analysis_report(
                            timestamp=entry.get("timestamp", ""),
                            level=entry.get("level", "full"),
                            portfolio_score=entry.get("portfolio_score", 50),
                            portfolio_rating=entry.get("portfolio_rating", "hold"),
                            num_positions=entry.get("num_positions", 0),
                            avg_confidence=entry.get("avg_confidence", 0),
                            scores=entry.get("scores", {}),
                        )
                        migrated += 1
                    except Exception:
                        pass
            analysis_file.rename(analysis_file.with_suffix(".json.bak"))
            logger.info(f"📦 Analysis-History von JSON migriert")
        except Exception as e:
            logger.warning(f"JSON-Migration Analysis fehlgeschlagen: {e}")

    return migrated


# Automatisch Tabellen erstellen beim Import
init_db()


# ─── Shadow Portfolio Agent ─────────────────────────────────

def shadow_get_meta(key: str, default: str = "") -> str:
    """Liest einen Shadow-Meta-Wert."""
    conn = _get_conn()
    row = conn.execute("SELECT value FROM shadow_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def shadow_set_meta(key: str, value: str):
    """Setzt einen Shadow-Meta-Wert."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO shadow_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def shadow_get_cash() -> float:
    """Gibt den aktuellen Cash-Bestand des Shadow-Portfolios zurück."""
    val = shadow_get_meta("cash_eur", "0")
    try:
        return float(val)
    except ValueError:
        return 0.0


def shadow_set_cash(amount: float):
    """Setzt den Cash-Bestand."""
    shadow_set_meta("cash_eur", str(round(amount, 2)))


def shadow_get_positions() -> list[dict]:
    """Gibt alle Shadow-Positionen zurück."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM shadow_portfolio ORDER BY ticker").fetchall()
    return [dict(r) for r in rows]


def shadow_upsert_position(
    ticker: str,
    name: str,
    shares: float,
    avg_cost_eur: float,
    current_price_eur: float,
    sector: str = "Unknown",
):
    """Erstellt oder aktualisiert eine Shadow-Position."""
    conn = _get_conn()
    now = datetime.now(tz=TZ_BERLIN).isoformat()
    conn.execute(
        """INSERT INTO shadow_portfolio (ticker, name, shares, avg_cost_eur, current_price_eur, sector, first_bought, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
             name=excluded.name,
             shares=excluded.shares,
             avg_cost_eur=excluded.avg_cost_eur,
             current_price_eur=excluded.current_price_eur,
             sector=excluded.sector,
             last_updated=excluded.last_updated""",
        (ticker, name, round(shares, 6), round(avg_cost_eur, 4), round(current_price_eur, 4), sector, now, now),
    )
    conn.commit()


def shadow_remove_position(ticker: str):
    """Entfernt eine Shadow-Position (bei Komplett-Verkauf)."""
    conn = _get_conn()
    conn.execute("DELETE FROM shadow_portfolio WHERE ticker = ?", (ticker,))
    conn.commit()


def shadow_add_transaction(
    action: str,
    ticker: str,
    name: str,
    shares: float,
    price_eur: float,
    total_eur: float,
    reason: str = "",
    score: Optional[float] = None,
    confidence: Optional[float] = None,
):
    """Speichert eine Shadow-Transaktion."""
    conn = _get_conn()
    ts = datetime.now(tz=TZ_BERLIN).isoformat()
    conn.execute(
        """INSERT INTO shadow_transactions
           (timestamp, action, ticker, name, shares, price_eur, total_eur, reason, score, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, action, ticker, name, round(shares, 6), round(price_eur, 4),
         round(total_eur, 2), reason, score, confidence),
    )
    conn.commit()


def shadow_get_transactions(limit: int = 50) -> list[dict]:
    """Gibt die letzten Shadow-Transaktionen zurück."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM shadow_transactions ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def shadow_save_performance(
    total_value_eur: float,
    cash_eur: float,
    invested_eur: float,
    pnl_eur: float,
    pnl_pct: float,
    num_positions: int,
    real_portfolio_value: float = 0,
):
    """Speichert einen täglichen Shadow-Performance-Snapshot."""
    today = datetime.now(tz=TZ_BERLIN).strftime("%Y-%m-%d")
    ts = datetime.now(tz=TZ_BERLIN).isoformat()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO shadow_performance
           (date, total_value_eur, cash_eur, invested_eur, pnl_eur, pnl_pct, num_positions, real_portfolio_value, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             total_value_eur=excluded.total_value_eur,
             cash_eur=excluded.cash_eur,
             invested_eur=excluded.invested_eur,
             pnl_eur=excluded.pnl_eur,
             pnl_pct=excluded.pnl_pct,
             num_positions=excluded.num_positions,
             real_portfolio_value=excluded.real_portfolio_value,
             timestamp=excluded.timestamp""",
        (today, round(total_value_eur, 2), round(cash_eur, 2), round(invested_eur, 2),
         round(pnl_eur, 2), round(pnl_pct, 4), num_positions, round(real_portfolio_value, 2), ts),
    )
    conn.commit()


def shadow_get_performance(days: int = 90) -> list[dict]:
    """Gibt die Shadow-Performance-Historie zurück."""
    conn = _get_conn()
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM shadow_performance WHERE date >= ? ORDER BY date",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM shadow_performance ORDER BY date").fetchall()
    return [dict(r) for r in rows]


def shadow_add_decision_log(
    cycle_summary: str,
    trades_executed: int,
    candidates_evaluated: int,
    ai_reasoning: str,
    total_value_eur: float,
    cash_eur: float,
):
    """Speichert einen Agent-Zyklusbericht."""
    conn = _get_conn()
    ts = datetime.now(tz=TZ_BERLIN).isoformat()
    conn.execute(
        """INSERT INTO shadow_decision_log
           (timestamp, cycle_summary, trades_executed, candidates_evaluated, ai_reasoning, total_value_eur, cash_eur)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ts, cycle_summary, trades_executed, candidates_evaluated, ai_reasoning,
         round(total_value_eur, 2), round(cash_eur, 2)),
    )
    conn.commit()


def shadow_get_decision_log(limit: int = 20) -> list[dict]:
    """Gibt die letzten Zyklusberichte zurück."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM shadow_decision_log ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def shadow_reset():
    """Setzt das komplette Shadow-Portfolio zurück."""
    conn = _get_conn()
    conn.executescript("""
        DELETE FROM shadow_portfolio;
        DELETE FROM shadow_transactions;
        DELETE FROM shadow_performance;
        DELETE FROM shadow_decision_log;
        DELETE FROM shadow_meta WHERE key != 'config_json';
    """)
    conn.commit()
    logger.info("🗑️ Shadow-Portfolio vollständig zurückgesetzt")


# ─── Shadow Config ────────────────────────────────────────────

# Standardwerte für Agenten-Regeln
SHADOW_CONFIG_DEFAULTS: dict = {
    "max_positions": 20,
    "max_weight_pct": 10.0,
    "min_cash_pct": 5.0,
    "min_trade_eur": 500.0,
    "max_trades_per_cycle": 3,
    "max_sector_pct": 35.0,
    "min_buy_score": 60.0,
    "strategy_mode": "balanced",  # "aggressive" | "balanced" | "conservative"
}


def shadow_get_config() -> dict:
    """Gibt die aktuellen Agenten-Konfiguration zurück (mit Defaults)."""
    import json as _json
    raw = shadow_get_meta("config_json", "")
    if raw:
        try:
            saved = _json.loads(raw)
            # Merge mit Defaults (neue Keys werden ergänzt)
            result = dict(SHADOW_CONFIG_DEFAULTS)
            result.update(saved)
            return result
        except Exception:
            pass
    return dict(SHADOW_CONFIG_DEFAULTS)


def shadow_save_config(config: dict):
    """Speichert die Agenten-Konfiguration (nur bekannte Keys werden übernommen)."""
    import json as _json
    current = shadow_get_config()
    # Nur bekannte Keys übernehmen + Typen erzwingen
    for key in SHADOW_CONFIG_DEFAULTS:
        if key in config:
            default_val = SHADOW_CONFIG_DEFAULTS[key]
            try:
                if isinstance(default_val, int):
                    current[key] = int(config[key])
                elif isinstance(default_val, float):
                    current[key] = float(config[key])
                else:
                    current[key] = str(config[key])
            except (ValueError, TypeError):
                pass  # Ungültige Werte ignorieren
    shadow_set_meta("config_json", _json.dumps(current))
    logger.info(f"⚙️ Shadow-Config gespeichert: {current}")
