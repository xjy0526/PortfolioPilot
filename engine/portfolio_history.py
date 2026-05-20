"""PortfolioPilot - Portfolio History Engine

Rekonstruiert den historischen Wert jeder Einzelaktie und des
Gesamtportfolios über die Zeit.

Datenquellen:
  1. Parqet Activities → Täglicher Aktienbestand (Shares pro Ticker)
  2. Parqet Performance API → Initial-Holdings für Positionen vor Activity-Fenster
  3. yfinance → Historische Tagesschlusskurse (konvertiert in EUR)

Persistenz:
  SQLite-Tabelle `price_history` speichert bereits abgerufene Kurse.
  Beim nächsten Aufruf werden nur neue Tage nachgeladen (inkrementell).
  → Spart API-Calls, übersteht Restarts, funktioniert auch bei Teil-Abrufen.

Limitierungen:
  Die Parqet Connect API liefert max ~999 Activities via Cursor-Pagination
  (API-seitiges Limit, nicht unsere Beschränkung). Ältere Positionen werden
  über die Performance API vorbelegt.
"""
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from config import settings

logger = logging.getLogger(__name__)

# SQLite-Pfad (gleiche DB wie database.py)
_DB_PATH = settings.CACHE_DIR / "portfoliopilot.db"


# ─────────────────────────────────────────────────────────────
# SQLite Price Cache
# ─────────────────────────────────────────────────────────────

def _init_price_table():
    """Erstellt die price_history Tabelle falls sie nicht existiert."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_ticker ON price_history(ticker)"
    )
    conn.commit()
    conn.close()


def _load_cached_prices(tickers: list[str]) -> dict[str, dict[str, float]]:
    """Lädt alle gespeicherten Kurse aus SQLite.

    Returns: {ticker: {date: close, ...}, ...}
    """
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    result: dict[str, dict[str, float]] = defaultdict(dict)
    try:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"SELECT ticker, date, close FROM price_history WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        for ticker, date, close in rows:
            result[ticker][date] = close
    except Exception as e:
        logger.debug(f"Price-Cache laden fehlgeschlagen: {e}")
    finally:
        conn.close()
    return dict(result)


def _save_prices_to_cache(prices: dict[str, dict[str, float]]):
    """Speichert neue Kurse in SQLite (UPSERT)."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    try:
        rows = []
        for ticker, date_prices in prices.items():
            for date, close in date_prices.items():
                rows.append((ticker, date, close))
        if rows:
            conn.executemany(
                """INSERT INTO price_history (ticker, date, close)
                   VALUES (?, ?, ?)
                   ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close""",
                rows,
            )
            conn.commit()
            logger.info(f"💾 Price-Cache: {len(rows)} Kurse gespeichert")
    except Exception as e:
        logger.warning(f"Price-Cache speichern fehlgeschlagen: {e}")
    finally:
        conn.close()


def _get_last_cached_date(ticker: str) -> str | None:
    """Gibt das letzte gecachte Datum für einen Ticker zurück."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM price_history WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# Position Reconstruction
# ─────────────────────────────────────────────────────────────

def reconstruct_daily_holdings(activities: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """Rekonstruiert aus Activities den Aktienbestand pro Tag.

    Für jeden Ticker wird eine Timeline erstellt:
    [(date, cumulative_shares), ...]

    Nur buy/sell/transfer_in/transfer_out werden berücksichtigt.
    Vollständig verkaufte Positionen (0 End-Shares) werden beibehalten
    um historische Werte korrekt darzustellen.
    """
    events: dict[str, list[tuple[str, float]]] = defaultdict(list)

    for act in activities:
        act_type = (act.get("type") or "").lower()
        ticker = act.get("ticker", "")
        date = act.get("date", "")
        shares = float(act.get("shares") or 0)

        if not ticker or not date or shares <= 0:
            continue
        if ticker == "CASH":
            continue

        if act_type in ("buy", "kauf", "purchase", "transferin", "transfer_in"):
            events[ticker].append((date, shares))
        elif act_type in ("sell", "verkauf", "sale", "transferout", "transfer_out"):
            events[ticker].append((date, -shares))

    holdings: dict[str, list[tuple[str, float]]] = {}
    for ticker, ticker_events in events.items():
        ticker_events.sort(key=lambda x: x[0])
        cumulative = 0.0
        timeline = []
        for date, delta in ticker_events:
            cumulative += delta
            if abs(cumulative) < 0.001:
                cumulative = 0.0
            timeline.append((date, cumulative))
        if timeline:
            holdings[ticker] = timeline

    return holdings


def _get_shares_on_date(timeline: list[tuple[str, float]], date_str: str) -> float:
    """Gibt die Shares für einen Ticker an einem bestimmten Datum zurück."""
    result = 0.0
    for event_date, shares in timeline:
        if event_date <= date_str:
            result = shares
        else:
            break
    return result


# ─────────────────────────────────────────────────────────────
# Cash Balance Reconstruction
# ─────────────────────────────────────────────────────────────

def reconstruct_cash_timeline(
    raw_activities: list[dict],
    current_cash: float = 0.0,
) -> list[tuple[str, float]]:
    """Rekonstruiert den Cash-Bestand aus rohen Parqet Activities.

    Da die API nur die neuesten ~1000 Activities liefert, wird der
    aktuelle Cash-Bestand von Parqet als Ankerpunkt genutzt und
    rückwärts rekonstruiert.

    Args:
        raw_activities: Rohe Activities von der Parqet API
        current_cash: Aktueller Cash-Bestand aus Parqet Positions API

    Returns: [(date, cash_balance), ...] sortiert nach Datum
    """
    # 1. Cash-Deltas aus Activities sammeln
    deltas: list[tuple[str, float]] = []

    for act in raw_activities:
        hat = (act.get("holdingAssetType") or "").lower()
        if hat != "cash":
            continue

        act_type = (act.get("type") or "").lower()
        date = act.get("datetime") or act.get("date") or ""
        if date and "T" in date:
            date = date.split("T")[0]
        amount = float(act.get("amount") or 0)

        if not date:
            continue

        delta = 0.0
        if act_type in ("transferin", "transfer_in", "deposit"):
            delta = amount
        elif act_type in ("transferout", "transfer_out", "withdrawal"):
            delta = -amount
        elif act_type in ("buy", "kauf", "purchase"):
            delta = -amount  # Cash geht raus
        elif act_type in ("sell", "verkauf", "sale"):
            delta = amount   # Cash kommt rein
        elif act_type in ("dividend",):
            delta = amount
        elif act_type in ("interest",):
            delta = amount

        if delta != 0.0:
            deltas.append((date, delta))

    if not deltas:
        # Keine Cash-Activities → konstanter Cash-Bestand
        today = datetime.now().strftime("%Y-%m-%d")
        return [(today, current_cash)] if current_cash > 0 else []

    deltas.sort(key=lambda x: x[0])

    # 2. Vorwärts-Summe aller Deltas berechnen
    # cumulative[i] = Summe aller Deltas von deltas[0] bis deltas[i]
    cumulative_deltas = []
    running = 0.0
    for date, delta in deltas:
        running += delta
        cumulative_deltas.append((date, running))

    # 3. Rückwärts-Ankerung: letzter bekannter Cash = current_cash
    # cash_at_end = current_cash (von Parqet)
    # cash_at_start = current_cash - sum(alle deltas)
    total_delta = cumulative_deltas[-1][1]
    cash_at_start = current_cash - total_delta

    # 4. Timeline erstellen: cash_at_start + cumulative_delta[i]
    timeline = []
    for date, cum_delta in cumulative_deltas:
        cash = cash_at_start + cum_delta
        if abs(cash) < 0.01:
            cash = 0.0
        timeline.append((date, round(cash, 2)))

    return timeline


# ─────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────

async def build_portfolio_history(
    activities: list[dict],
    period_days: int = 180,
    raw_activities: list[dict] | None = None,
    current_cash: float = 0.0,
) -> dict:
    """Baut die komplette Portfolio-Historie für das Diagramm.

    Nutzt SQLite-Cache für bereits abgerufene Kurse und lädt nur
    fehlende Tage inkrementell von yfinance nach.

    Fixes:
      - Währungskonvertierung: yfinance-Preise werden in EUR konvertiert
      - Performance API Pre-Population: Positionen vor dem Activity-Fenster
        werden aus der Performance API vorbelegt
      - Cost Basis: Sells ziehen avg_cost × shares ab, nicht den Erlös
      - Demo Mode Interception: Wenn im Demo Mode, liefere direkt synthetische Daten
    """
    from state import portfolio_data
    summary = portfolio_data.get("summary")
    if summary and getattr(summary, "is_demo", False):
        from fetchers.demo_data import get_demo_portfolio_history
        demo_history = get_demo_portfolio_history(days=period_days)
        if not demo_history:
             return {"dates": [], "stocks": {}, "total": [], "total_cost": []}
             
        dates = [entry["date"] for entry in demo_history]
        total_values = [entry["total_value"] for entry in demo_history]
        pnl = [entry["pnl"] for entry in demo_history]
        total_cost = [entry.get("cost_basis", 100000) for entry in demo_history]
        
        # Vereinfachte Demo-Struktur (nur Apple + Microsoft als Platzhalter)
        demo_stocks = {
             "AAPL": {"name": "Apple Inc.", "values": [v * 0.6 for v in total_values]},
             "MSFT": {"name": "Microsoft Corporation", "values": [v * 0.4 for v in total_values]}
        }
        
        return {
            "dates": dates,
            "stocks": demo_stocks,
            "total": total_values,
            "total_cost": total_cost,
            "pnl": pnl,
        }

    if not activities:
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    # Tabelle sicherstellen
    _init_price_table()

    # 1. Holdings rekonstruieren
    holdings = reconstruct_daily_holdings(activities)
    if not holdings:
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    # 1b. Performance API: Positionen vorbelegen die vor dem Activity-Fenster liegen
    await _prepopulate_from_performance(holdings, activities)

    # 2. Cash-Timeline rekonstruieren (wenn raw data verfügbar)
    cash_timeline = None
    if raw_activities:
        cash_timeline = reconstruct_cash_timeline(raw_activities, current_cash=current_cash)

    # 3. Datumsgrenzen bestimmen
    all_dates = []
    for timeline in holdings.values():
        for date_str, _ in timeline:
            all_dates.append(date_str)
    if cash_timeline:
        for date_str, _ in cash_timeline:
            all_dates.append(date_str)

    if not all_dates:
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    earliest = min(all_dates)
    today = datetime.now().strftime("%Y-%m-%d")

    if period_days > 0 and period_days < 9999:
        cutoff = (datetime.now() - timedelta(days=period_days)).strftime("%Y-%m-%d")
        start_date = max(earliest, cutoff)
    else:
        start_date = earliest

    # 4. Ticker-Namen aus Activities extrahieren
    ticker_names: dict[str, str] = {}
    for act in activities:
        t = act.get("ticker", "")
        n = act.get("name", "")
        if t and n and t not in ticker_names:
            ticker_names[t] = n

    # 5. Historische Kurse: Cache + inkrementeller yfinance-Abruf
    tickers = list(holdings.keys())
    prices = await _fetch_prices_with_cache(tickers, start_date, today)

    if not prices:
        logger.warning("Keine historischen Kurse verfügbar (Cache + yfinance)")
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    # 5b. Währungskonvertierung: yfinance-Preise in EUR umrechnen
    from services.currency_converter import CurrencyConverter
    converter = await CurrencyConverter.create()
    prices = _convert_prices_to_eur(prices, converter)

    # 6. Gemeinsame Datums-Achse aus den Preisdaten ableiten
    all_price_dates: set[str] = set()
    for ticker_prices in prices.values():
        all_price_dates.update(ticker_prices.keys())

    if not all_price_dates:
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    dates = sorted(d for d in all_price_dates if d >= start_date)
    if not dates:
        return {"dates": [], "stocks": {}, "total": [], "total_cost": []}

    # 7. Einstandskosten-Timeline (korrigierte Logik: avg_cost bei Sells)
    active_tickers = set()
    for ticker, timeline in holdings.items():
        if timeline and timeline[-1][1] > 0:
            active_tickers.add(ticker)
    cost_timeline = _reconstruct_cost_timeline(activities, dates, active_tickers)

    # 8. Werte berechnen: Shares × Kurs (EUR) pro Tag
    stocks_data: dict[str, dict] = {}
    total_values = [0.0] * len(dates)

    for ticker, timeline in holdings.items():
        if ticker not in prices or not prices[ticker]:
            continue

        values = []
        for i, date_str in enumerate(dates):
            shares = _get_shares_on_date(timeline, date_str)
            price = prices[ticker].get(date_str, 0.0)

            # Forward-fill: letzten bekannten Preis nutzen
            if price <= 0:
                for prev_date in reversed(dates[:i]):
                    price = prices[ticker].get(prev_date, 0.0)
                    if price > 0:
                        break

            value = round(shares * price, 2) if shares > 0 and price > 0 else 0.0
            values.append(value)
            total_values[i] += value

        if any(v > 0 for v in values):
            name = ticker_names.get(ticker, ticker)
            stocks_data[ticker] = {"name": name, "values": values}

    # 9. Cash-Bestand zu den Werten hinzufügen
    if cash_timeline and len(cash_timeline) > 0:
        cash_values = []
        for i, date_str in enumerate(dates):
            cash = _get_shares_on_date(cash_timeline, date_str)
            if cash < 0:
                logger.warning(f"Negativer Cash-Stand am {date_str}: {cash:.2f}€ (fehlende Activities?)")
            cash_values.append(round(max(cash, 0), 2))
            total_values[i] += max(cash, 0)

        if any(v > 0 for v in cash_values):
            stocks_data["CASH"] = {"name": "💵 Cash", "values": cash_values}

    # Sortiere nach durchschnittlichem Wert (größte zuerst)
    sorted_stocks = dict(sorted(
        stocks_data.items(),
        key=lambda x: sum(x[1]["values"]) / max(len(x[1]["values"]), 1),
        reverse=True,
    ))

    # 10. P&L berechnen: Total - Cost (kann negativ sein)
    pnl = [round(total_values[i] - cost_timeline[i], 2) for i in range(len(dates))]

    return {
        "dates": dates,
        "stocks": sorted_stocks,
        "total": [round(v, 2) for v in total_values],
        "total_cost": cost_timeline,
        "pnl": pnl,
    }


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _reconstruct_cost_timeline(
    activities: list[dict],
    dates: list[str],
    active_tickers: set[str] | None = None,
) -> list[float]:
    """Rekonstruiert die Netto-Einstandskosten pro Tag.

    KORRIGIERTE Logik:
    - Buy: addiert amount (Kaufpreis × Shares)
    - Sell: subtrahiert avg_cost × verkaufte Shares (NICHT den Erlös!)
    - Dadurch bleibt die Cost-Basis korrekt unabhängig vom Verkaufspreis.
    """
    # Pro Ticker: avg_cost und total_invested tracken
    ticker_cost: dict[str, float] = {}    # Ticker → kumulierte Kosten
    ticker_shares: dict[str, float] = {}  # Ticker → kumulierte Shares

    cost_events: list[tuple[str, float]] = []  # (date, portfolio_cost)
    total_cost = 0.0

    sorted_acts = sorted(activities, key=lambda a: a.get("date", ""))
    for act in sorted_acts:
        act_type = (act.get("type") or "").lower()
        date = act.get("date", "")
        amount = float(act.get("amount") or 0)
        shares = float(act.get("shares") or 0)
        ticker = act.get("ticker", "")

        if not date or ticker == "CASH" or shares <= 0:
            continue

        # Wenn active_tickers gesetzt, nur diese berücksichtigen
        if active_tickers and ticker not in active_tickers:
            continue

        if act_type in ("buy", "kauf", "purchase", "transferin", "transfer_in"):
            # Kosten addieren
            ticker_cost[ticker] = ticker_cost.get(ticker, 0) + amount
            ticker_shares[ticker] = ticker_shares.get(ticker, 0) + shares
            total_cost += amount

        elif act_type in ("sell", "verkauf", "sale", "transferout", "transfer_out"):
            # Avg cost berechnen und anteilig abziehen
            current_shares = ticker_shares.get(ticker, 0)
            current_cost = ticker_cost.get(ticker, 0)

            if current_shares > 0:
                avg_cost = current_cost / current_shares
                cost_reduction = avg_cost * shares
            else:
                cost_reduction = amount  # Fallback

            ticker_cost[ticker] = max(0, current_cost - cost_reduction)
            ticker_shares[ticker] = max(0, current_shares - shares)
            total_cost = max(0, total_cost - cost_reduction)

        cost_events.append((date, round(total_cost, 2)))

    result = []
    for date_str in dates:
        cost = 0.0
        for event_date, event_cost in cost_events:
            if event_date <= date_str:
                cost = event_cost
            else:
                break
        result.append(round(cost, 2))

    return result


def _convert_prices_to_eur(
    prices: dict[str, dict[str, float]],
    converter,
) -> dict[str, dict[str, float]]:
    """Konvertiert alle Preise von Originalwährung in EUR.

    Nutzt den CurrencyConverter mit aktuellen Wechselkursen.
    Achtung: Nutzt aktuelle Kurse für historische Preise (Approximation).
    """
    converted = {}
    for ticker, date_prices in prices.items():
        if converter.is_eur_native(ticker):
            converted[ticker] = date_prices
            continue

        converted[ticker] = {
            date: round(converter.to_eur(price, ticker), 4)
            for date, price in date_prices.items()
        }

    eur_count = sum(1 for t in prices if converter.is_eur_native(t))
    fx_count = len(prices) - eur_count
    if fx_count > 0:
        logger.info(f"💱 Historie: {fx_count} Ticker in EUR konvertiert, {eur_count} nativ EUR")

    return converted


async def _prepopulate_from_performance(
    holdings: dict[str, list[tuple[str, float]]],
    activities: list[dict],
) -> None:
    """Ergänzt Holdings mit Daten aus der Performance API.

    Wenn eine Position laut Performance API vor dem ältesten Activity
    existierte, wird ein synthetischer Buy-Event eingefügt.
    """
    try:
        from fetchers.parqet import fetch_portfolio_performance
        perf_data = await fetch_portfolio_performance()
        if not perf_data:
            return
    except Exception as e:
        logger.debug(f"Performance API für Pre-Population nicht verfügbar: {e}")
        return

    # Frühestes Activity-Datum finden
    all_act_dates = [a.get("date", "") for a in activities if a.get("date")]
    if not all_act_dates:
        return
    earliest_activity = min(all_act_dates)

    prepop_count = 0
    for h in perf_data.get("holdings", []):
        ticker = h.get("ticker", "")
        if not ticker or ticker == "CASH" or h.get("type") == "cash":
            continue

        earliest_date = h.get("earliestActivityDate", "")
        shares = h.get("shares", 0)

        # Nur wenn Position VOR dem Activity-Fenster begann
        if not earliest_date or earliest_date >= earliest_activity:
            continue

        # Prüfe ob der Ticker bereits in holdings ist
        if ticker in holdings:
            # Prüfe ob erste Activity im Holdings-Fenster liegt
            first_holding_date = holdings[ticker][0][0] if holdings[ticker] else ""
            if first_holding_date and first_holding_date > earliest_date:
                # Es gibt einen Gap: Die Position existierte schon vorher
                # Berechne die Shares am Anfang des Activity-Fensters
                # (Aktuelle Shares aus Performance API als Startpunkt)
                if not h.get("isSold", False) and shares > 0:
                    # Füge den Anfangs-Bestand VOR den ersten bekannten Event ein
                    # Die Differenz = shares am Anfang des Fensters
                    existing_first_shares = holdings[ticker][0][1]
                    if existing_first_shares > 0:
                        # Es wurde bereits korrekt als Buy einsortiert
                        continue
        else:
            # Ticker existiert gar nicht in den Activities aber hat Shares
            if shares > 0 and not h.get("isSold", False):
                # Synthetischen Buy am earliestActivityDate einfügen
                holdings[ticker] = [(earliest_date, shares)]
                prepop_count += 1
                logger.info(
                    f"📦 Pre-Population: {ticker} mit {shares:.2f} Shares ab {earliest_date}"
                )

    if prepop_count > 0:
        logger.info(f"📦 {prepop_count} Positionen aus Performance API vorbelegt")


async def _fetch_prices_with_cache(
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, float]]:
    """Inkrementeller Kursabruf mit SQLite-Cache.

    1. Gecachte Kurse laden
    2. Pro Ticker prüfen: Welche Tage fehlen?
    3. Nur fehlende Tage von yfinance nachladen
    4. Neue Kurse in Cache speichern
    """
    from state import YFINANCE_ALIASES

    if not tickers:
        return {}

    # Ticker→yfinance Mapping mit ISIN-Auflösung
    from fetchers.parqet import ISIN_TICKER_MAP

    ticker_to_yf = {}
    yf_to_ticker = {}
    skip_tickers = set()

    for t in tickers:
        yf_t = YFINANCE_ALIASES.get(t, t)

        # ISIN-Auflösung: Wenn Ticker wie eine ISIN aussieht, versuche Mapping
        if len(yf_t) == 12 and yf_t[:2].isalpha():
            resolved = ISIN_TICKER_MAP.get(yf_t, "")
            if resolved and resolved != yf_t:
                yf_t = resolved
                logger.info(f"ISIN {t} → {yf_t} aufgelöst")
            else:
                logger.debug(f"ISIN {t} übersprungen (kein yfinance Ticker)")
                skip_tickers.add(t)
                continue

        ticker_to_yf[t] = yf_t
        yf_to_ticker[yf_t] = t

    if not ticker_to_yf:
        return {}

    valid_tickers = list(ticker_to_yf.keys())

    # 1. Gecachte Kurse laden
    cached = _load_cached_prices(valid_tickers)
    cached_count = sum(len(v) for v in cached.values())

    # 2. Bestimmen was nachgeladen werden muss
    # Pro Ticker: ab wann fehlen Daten?
    tickers_to_fetch: dict[str, str] = {}  # yf_ticker → fetch_from_date
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for orig_t, yf_t in ticker_to_yf.items():
        if orig_t in cached and cached[orig_t]:
            last_cached = max(cached[orig_t].keys())
            if last_cached >= yesterday:
                # Schon up-to-date → nur heute nachladen
                fetch_from = yesterday
            else:
                # Ab dem Tag nach dem letzten Cache nachladen
                last_dt = datetime.strptime(last_cached, "%Y-%m-%d")
                fetch_from = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            tickers_to_fetch[yf_t] = fetch_from
        else:
            # Gar nicht gecacht → alles laden
            tickers_to_fetch[yf_t] = start_date

    # 3. Fehlende Daten von yfinance holen
    if tickers_to_fetch:
        # Gruppiere nach fetch_from_date um Batch-Downloads zu optimieren
        # Einfachster Ansatz: Lade ab dem frühesten fehlenden Datum für alle
        earliest_fetch = min(tickers_to_fetch.values())

        # Prüfe ob ein Fetch überhaupt nötig ist
        if earliest_fetch <= end_date:
            yf_tickers = list(tickers_to_fetch.keys())
            logger.info(
                f"📊 Historie: {cached_count} Kurse aus Cache, "
                f"lade {len(yf_tickers)} Ticker ab {earliest_fetch}"
            )
            new_prices = await _fetch_from_yfinance(
                yf_tickers, yf_to_ticker, earliest_fetch, end_date
            )

            if new_prices:
                # In Cache speichern
                _save_prices_to_cache(new_prices)

                # Mit gecachten Daten mergen
                for ticker, date_prices in new_prices.items():
                    if ticker not in cached:
                        cached[ticker] = {}
                    cached[ticker].update(date_prices)
        else:
            logger.info(f"📊 Historie: Alle {cached_count} Kurse aus Cache geladen (aktuell)")
    else:
        logger.info(f"📊 Historie: Alle {cached_count} Kurse aus Cache geladen")

    return cached


async def _fetch_from_yfinance(
    yf_tickers: list[str],
    yf_to_ticker: dict[str, str],
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, float]]:
    """Lädt historische Kurse via yfinance Batch-Download.

    Returns: {original_ticker: {date: close, ...}, ...}
    """
    if not yf_tickers:
        return {}

    try:
        import yfinance as yf

        data = yf.download(
            tickers=yf_tickers,
            start=start_date,
            end=end_date,
            interval="1d",
            progress=False,
            group_by="ticker" if len(yf_tickers) > 1 else "column",
        )

        if data is None or data.empty:
            logger.warning("yfinance download returned empty data")
            return {}

        result: dict[str, dict[str, float]] = {}

        if len(yf_tickers) == 1:
            yf_t = yf_tickers[0]
            orig_ticker = yf_to_ticker.get(yf_t, yf_t)
            if "Close" in data.columns:
                closes = data["Close"].dropna()
                result[orig_ticker] = {
                    idx.strftime("%Y-%m-%d"): round(float(val), 4)
                    for idx, val in closes.items()
                }
        else:
            for yf_t in yf_tickers:
                orig_ticker = yf_to_ticker.get(yf_t, yf_t)
                try:
                    if yf_t in data.columns.get_level_values(0):
                        closes = data[yf_t]["Close"].dropna()
                        result[orig_ticker] = {
                            idx.strftime("%Y-%m-%d"): round(float(val), 4)
                            for idx, val in closes.items()
                        }
                except Exception as e:
                    logger.debug(f"Historie-Preis für {yf_t} fehlgeschlagen: {e}")

        new_count = sum(len(v) for v in result.values())
        logger.info(f"📊 yfinance: {new_count} neue Kurse für {len(result)}/{len(yf_tickers)} Ticker")
        return result

    except Exception as e:
        logger.error(f"yfinance batch download fehlgeschlagen: {e}")
        return {}
