"""PortfolioPilot - Parqet Portfolio Fetcher

Liest Portfolio-Daten aus Parqet und liefert Netto-Positionen + Cash.

Architektur:
-----------
Drei Datenquellen in Prioritaet (alle via connect.parqet.com):

  1. POST /performance  → Fertige Holdings (Positionen + Cash, 1 API-Call)
  2. GET  /activities   → Cursor-Pagination, manuell aggregieren (Fallback)
  3. GET  /activities   → Internal API (api.parqet.com), Offset-Pagination (Fallback)

API-Dokumentation: docs/Parqet API/

Cache-Strategie:
  1. Fresh Cache (TTL) → Positionen direkt laden
  2. API aufrufen → Positionen laden → Cache aktualisieren
  3. Stale Cache → Positionen ohne TTL, Preise auf 0 (yfinance berechnet neu)

Token-Erneuerung (parqet_auth.py):
  1. Gespeicherter Token pruefen (JWT exp)
  2. Connect API Refresh (OAuth2 refresh_token)
  3. Firefox-Cookie Fallback (nur lokal)
"""
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

from cache_manager import CacheManager
from models import PortfolioPosition
from config import settings, BASE_DIR
from fetchers.parqet_auth import (
    ensure_valid_token as _ensure_valid_token,
    is_token_expired as _is_token_expired,
    refresh_token_from_firefox as _refresh_token_from_firefox,
    get_valid_token as _get_valid_token,
    refresh_oauth_token as _refresh_oauth_token,
    load_token_file as _load_token_file,
    save_token_file as _save_token_file,
    TOKEN_FILE,
)

logger = logging.getLogger(__name__)

# Zentraler Cache für Parqet-Daten (ersetzt die alten _load_cache/_save_cache)
_cache = CacheManager("parqet", ttl_hours=12)

ACTIVITIES_CACHE_FILE = settings.CACHE_DIR / "parqet_activities.json"

# Parqet API URLs
PARQET_INTERNAL_API = "https://api.parqet.com/v1"  # Interne API (Supabase JWT)
PARQET_CONNECT_API = settings.PARQET_API_BASE_URL   # Connect API (OAuth2): https://connect.parqet.com

# Cached activities from last API call (reused by history endpoint)
_cached_activities: list = []

# Maximale Anzahl Activities die auf Disk gespeichert werden
# Muss hoch genug sein um ALLE historischen Activities zu speichern
# (für korrektes Cash-Saldo und Historie-Chart)
_MAX_CACHED_ACTIVITIES = 5000


def _save_activities_cache(activities: list):
    """Speichert Activities auf Disk (begrenzt auf die neuesten Einträge).

    Reduziert die Dateigröße von ~1.2 MB auf ~150 KB bei gleichbleibender
    Funktionalität für History/Attribution.
    """
    try:
        # Nur die neuesten N Activities speichern (bereits chronologisch sortiert)
        limited = activities[-_MAX_CACHED_ACTIVITIES:] if len(activities) > _MAX_CACHED_ACTIVITIES else activities
        ACTIVITIES_CACHE_FILE.write_text(
            json.dumps(limited, default=str), encoding="utf-8"
        )
        if len(activities) > _MAX_CACHED_ACTIVITIES:
            logger.info(
                f"Activities-Cache: {len(limited)}/{len(activities)} gespeichert "
                f"(begrenzt auf {_MAX_CACHED_ACTIVITIES})"
            )
    except Exception as e:
        logger.debug(f"Activities-Cache Speichern fehlgeschlagen: {e}")


def _load_cache() -> list | None:
    """Laedt Positionen aus dem Cache (via CacheManager, TTL-geprüft)."""
    cached = _cache.get("positions")
    if cached is not None and isinstance(cached, list) and cached:
        if not _cache._stale:
            return cached
        # Stale → nicht als frisch zurückgeben
        return None
    return None


def _load_stale_cache() -> list[PortfolioPosition]:
    """Laedt Positionen vom Vortag OHNE TTL-Check.

    Preise werden auf 0 gesetzt — yFinance berechnet sie neu.
    Stueckzahlen, ISIN, Ticker und Sektoren bleiben erhalten.
    """
    # CacheManager lädt stale Daten automatisch beim ersten Zugriff
    cached = _cache.get("positions")
    if not cached or not isinstance(cached, list):
        return []

    result = []
    for p in cached:
        pos = PortfolioPosition(**p)
        if pos.ticker != "CASH":
            pos.current_price = 0.0
            pos.daily_change_pct = None
            pos.price_currency = ""
        result.append(pos)

    logger.info(f"Parqet Stale-Cache: {len(result)} Positionen geladen (Preise auf 0)")
    return result


def _save_cache(positions: list[dict]):
    """Speichert Positionen im Cache (via CacheManager)."""
    _cache.set("positions", positions)
    _cache.flush()
    logger.info(f"Parqet Cache gespeichert: {len(positions)} Positionen")

# ISIN-to-Ticker mapping (includes all portfolio positions)
ISIN_TICKER_MAP = {
    # US Large-Cap
    "US0378331005": "AAPL", "US5949181045": "MSFT", "US02079K3059": "GOOGL",
    "US0231351067": "AMZN", "US30303M1027": "META", "US88160R1014": "TSLA",
    "US67066G1040": "NVDA", "US79466L3024": "CRM", "US46625H1005": "JPM",
    "US92826C8394": "V",   "US0846707026": "BRK-B", "US4781601046": "JNJ",
    "US7427181091": "PG",  "US2546871060": "DIS",  "US17275R1023": "CSCO",
    "US4592001014": "IBM", "US0079031078": "AMD",  "US22160K1051": "COST",
    "US7170811035": "PFE", "US58933Y1055": "MRK",  "US11135F1012": "AVGO",
    "US00724F1012": "ADBE", "US6541061031": "NFLX", "US70450Y1038": "PYPL",
    "US8740391003": "TSM", "US4370761029": "HD",
    # Portfolio-spezifisch (US)
    "US7475251036": "QCOM",       # Qualcomm
    "US2515661054": "DTEGY",      # Deutsche Telekom ADR
    "US4330001060": "HIMS",       # Hims & Hers Health
    "US5657881067": "MARA",       # Mara Holdings
    "US03831W1080": "APP",        # AppLovin
    "US23804L1035": "DDOG",       # Datadog
    "US98980G1022": "ZS",         # Zscaler
    "US22788C1053": "CRWD",       # Crowdstrike
    # DE / EU (mit .DE-Suffix für korrekte Währungserkennung)
    "DE0007164600": "SAP.DE",     # SAP
    "DE0007236101": "SIE.DE",     # Siemens
    "DE000BAY0017": "BAYN.DE",    # Bayer
    "NL0010273215": "ASML",       # ASML (Amsterdam, USD-notiert)
    "DE0005557508": "DTE.DE",     # Deutsche Telekom
    "DE0008430026": "MUV2.DE",    # Munich Re
    "DE0007100000": "MBG.DE",     # Mercedes-Benz
    "DE0005810055": "DB1.DE",     # Deutsche Börse
    "DE0008404005": "ALV.DE",     # Allianz
    "DE0007037129": "RWE.DE",     # RWE
    "DE0007231326": "SIX2.DE",    # Sixt
    "DK0062498333": "NOVO-B.CO",  # Novo Nordisk B
    # Historische Positionen (für Historie-Chart)
    "US09075V1026": "BNTX",       # BioNTech
    "US60770K1079": "MDB",        # MongoDB
    "DE0007664039": "VOW3.DE",    # Volkswagen VZ
    "US4581401001": "INTC",       # Intel
    "US45662N1037": "INDI",       # Indie Semiconductor
    "DE000BASF111": "BAS.DE",     # BASF
    "US0494681010": "TEAM",       # Atlassian
    "NO0010872468": "MOWI.OL",    # Mowi (Lachs-Zucht, Oslo)
    "DE0005933931": "EXS1.DE",    # iShares Core DAX (ETF)
    "IE00BMFKG444": "DBXD.DE",    # Xtrackers MSCI USA IT (ETF)
    "IE00B1TXK627": "EXSA.DE",    # iShares STOXX Europe 600 (ETF)
    "IE00B1XNHC34": "VGWL.DE",   # Vanguard FTSE All-World (ETF)
    "IE00BLRPQH31": "IUSQ.DE",   # iShares MSCI ACWI (ETF)
    "IE00BYZK4776": "IS3R.DE",   # iShares Core MSCI EM IMI (ETF)
    "IE00BYZK4552": "EUNL.DE",   # iShares Core MSCI World (ETF)
    # Neue Positionen (ab Mai 2024)
    "DE000RENK730": "R3NK.DE",    # Renk Group
    "DE0006231004": "IFX.DE",     # Infineon Technologies
    "DE0008232125": "LHA.DE",     # Lufthansa
    "US36467W1099": "GE",         # GE Aerospace
    "US6544453037": "NKE",        # Nike
    "US91332U1016": "U",          # Unity Software
    "US5324571083": "LLY",        # Eli Lilly
    "US1696561059": "CHPT",       # Chipotle (ex-CMG) — prüfe Ticker
    "US7960542030": "SMSN.IL",    # Samsung SDI (London)
    "KYG6683N1034": "NIO",        # NIO (Cayman)
    "US0404131064": "ARM",        # ARM Holdings (Class A)
    "US0404132054": "ARM",        # ARM Holdings (Class A) alternate
    "IE00BM67HW99": "VGWD.DE",   # Vanguard FTSE All-World High Dividend (ETF)
    # Fonds (keine yfinance-Daten verfügbar)
    "DE000A2QJLA8": "DE000A2QJLA8",  # BIT Global Fintech (Fonds, bleibt ISIN)
    "DE000A2QDRW2": "DE000A2QDRW2",  # BIT Global Leaders (Fonds, bleibt ISIN)
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_portfolio() -> list[PortfolioPosition]:
    """
    Holt Portfolio-Daten. Prioritaet:
    1. Cache (frisch, innerhalb TTL)
    2. Parqet API (braucht gueltigen Token)
    3. Staler Cache (Vortag) — Positionen + Stueck bleiben, Preise werden auf 0 gesetzt
    """
    # Check fresh cache first
    cached = _load_cache()
    if cached:
        logger.info(f"Parqet: {len(cached)} Positionen aus Cache geladen")
        return [PortfolioPosition(**p) for p in cached]

    # Parqet API
    if settings.parqet_api_configured:
        positions = await _fetch_via_api()
        if positions:
            _save_cache([p.model_dump() for p in positions])
            return positions

    # Fallback: Stale Cache — Positionen vom Vortag, Preise werden resettet
    stale = _load_stale_cache()
    if stale:
        logger.warning(
            f"Parqet API nicht verfuegbar — verwende {len(stale)} Positionen "
            f"vom letzten erfolgreichen Laden (Preise werden neu berechnet)"
        )
        return stale

    logger.warning("Keine Portfolio-Daten von Parqet API erhalten")
    return []


# ---------------------------------------------------------------------------
# Parqet API (Internal + Connect)
# ---------------------------------------------------------------------------

async def _fetch_via_api() -> list[PortfolioPosition]:
    """
    Holt Portfolio-Daten ueber die Parqet API.
    Prioritaet:
    1. Connect API: POST /performance (fertige Holdings, 1 Call)
    2. Connect API: GET /activities (Cursor-Pagination, manuell aggregieren)
    3. Internal API: GET /activities (Offset-Pagination, Supabase JWT)
    """
    # Token vorab pruefen und bei Bedarf erneuern
    access_token = await _ensure_valid_token()
    if not access_token:
        logger.warning("Parqet API: Kein gueltiger Access-Token verfuegbar")
        return []

    portfolio_id = settings.PARQET_PORTFOLIO_ID
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Connect API: Performance Endpoint (beste Datenqualitaet)
            positions = await _try_performance_api(client, headers, portfolio_id)
            if positions:
                return positions

            # 2. Connect API: Activities (Cursor-Pagination)
            positions = await _try_connect_api(client, headers, portfolio_id)
            if positions:
                return positions

            # 3. Fallback: Internal API (Supabase Token + Offset-Pagination)
            positions = await _try_internal_api(client, headers, portfolio_id)
            if positions:
                return positions

            return []

    except httpx.HTTPError as e:
        logger.error(f"Parqet API Netzwerk-Fehler: {e}")
        return []
    except Exception as e:
        logger.error(f"Parqet API unerwarteter Fehler: {e}")
        return []


async def _try_performance_api(
    client: httpx.AsyncClient, headers: dict, portfolio_id: str
) -> list[PortfolioPosition]:
    """Holt Portfolio-Daten ueber den Connect API Performance-Endpoint.

    POST /performance liefert fertige Holdings mit exakten Positionen:
    - shares, purchasePrice, purchaseValue, currentPrice, currentValue
    - asset.isin, asset.name, asset.type
    - KPIs: XIRR, TTWROR, Dividenden

    Vorteil: 1 API-Call statt 10+ Seiten Activities aggregieren.
    """
    url = f"{PARQET_CONNECT_API}/performance"

    try:
        payload = {
            "portfolioIds": [portfolio_id],
            "interval": {
                "type": "relative",
                "value": "max"
            }
        }
        resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 401 and settings.PARQET_REFRESH_TOKEN:
            logger.info("Parqet Performance API: Token abgelaufen, versuche Refresh…")
            new_token = await _refresh_oauth_token()
            if not new_token:
                return []
            headers["Authorization"] = f"Bearer {new_token}"
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.debug(f"Parqet Performance API: {resp.status_code}")
            return []

        data = resp.json()
        holdings = data.get("holdings", [])
        if not holdings:
            logger.debug("Parqet Performance API: Keine Holdings")
            return []

        positions = []
        cash_total = 0.0
        cash_accounts = []

        for h in holdings:
            asset = h.get("asset") or {}
            position = h.get("position") or {}
            quote = h.get("quote") or {}

            asset_type = asset.get("type", "")

            # Cash-Holdings: Verrechnungskonten separat tracken
            if asset_type == "cash":
                if not position.get("isSold", False):
                    current_value = float(position.get("currentValue", 0) or 0)
                    name = asset.get("name", "Cash")
                    if current_value > 0:
                        cash_total += current_value
                        cash_accounts.append(f"{name}: {current_value:,.2f} EUR")
                continue

            # Nur aktive Securities/Crypto (nicht verkaufte)
            if asset_type not in ("security", "crypto"):
                continue
            if position.get("isSold", False):
                continue

            isin = asset.get("isin", "")
            name = asset.get("name", "")
            shares = float(position.get("shares", 0) or 0)
            purchase_price = float(position.get("purchasePrice", 0) or 0)
            current_price = float(position.get("currentPrice", 0) or 0)

            # Fallback: Quote-Preis verwenden
            if not current_price and quote:
                current_price = float(quote.get("price", 0) or 0)

            if not isin or shares <= 0:
                continue

            # Ticker aus ISIN-Map oder ISIN selbst verwenden
            ticker = ISIN_TICKER_MAP.get(isin, isin)

            # Performance API liefert currentPrice/purchasePrice in Portfolio-
            # Waehrung (EUR). Keine weitere Konvertierung noetig!
            # Die quote.fx.originalCurrency zeigt nur die Boersenwaehrung,
            # aber der Preis ist bereits konvertiert.
            currency = "EUR"

            positions.append(PortfolioPosition(
                ticker=ticker.upper(),
                isin=isin,
                name=name or ticker,
                shares=shares,
                avg_cost=purchase_price,
                current_price=current_price,
                currency=currency,
            ))

        # Cash-Position hinzufuegen wenn vorhanden
        if cash_total > 1:
            positions.append(PortfolioPosition(
                ticker="CASH",
                isin="",
                name="Verrechnungskonto",
                shares=1,
                avg_cost=cash_total,
                current_price=cash_total,
                currency="EUR",
            ))
            logger.info(f"💰 Cash-Bestand: {cash_total:,.2f} EUR ({len(cash_accounts)} Konten)")
            for acc in cash_accounts:
                logger.info(f"   {acc}")

        if positions:
            n_securities = len([p for p in positions if p.ticker != "CASH"])
            logger.info(
                f"✅ Parqet Performance API: {n_securities} Positionen + "
                f"Cash {cash_total:,.2f} EUR geladen "
                f"(von {len(holdings)} Holdings gesamt)"
            )

        return positions

    except Exception as e:
        logger.warning(f"Parqet Performance API Fehler: {e}")
        return []


async def _try_internal_api(
    client: httpx.AsyncClient, headers: dict, portfolio_id: str
) -> list[PortfolioPosition]:
    """Holt Portfolio-Daten über die interne Parqet API.

    Nutzt /v1/activities mit Pagination (max 100 pro Request, ~1584 gesamt).
    Rekonstruiert aktuelle Positionen aus Buy/Sell/Transfer/Dividend Activities.
    """
    # Fetch all activities with pagination
    all_activities = []
    offset = 0
    while True:
        try:
            url = f"{PARQET_INTERNAL_API}/activities?portfolioId={portfolio_id}&limit=100&offset={offset}"
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                if resp.status_code == 401:
                    # Token trotz Vorab-Check abgelaufen → nochmal erneuern
                    logger.info("Parqet API 401 trotz Vorab-Pruefung, erneuere Token...")
                    new_token = await _ensure_valid_token()
                    if new_token:
                        headers["Authorization"] = f"Bearer {new_token}"
                        resp = await client.get(url, headers=headers)
                        if resp.status_code != 200:
                            logger.warning(f"Parqet API nach Token-Refresh: {resp.status_code}")
                            break
                    else:
                        logger.warning("Parqet API: Token abgelaufen, kein Refresh moeglich")
                        break
                else:
                    logger.debug(f"Parqet API: {resp.status_code}")
                    break

            data = resp.json()
            activities = data.get("activities", []) if isinstance(data, dict) else data
            if not activities:
                break
            all_activities.extend(activities)
            logger.debug(f"Parqet API: {len(activities)} Activities bei Offset {offset}")

            has_more = data.get("hasMore", False) if isinstance(data, dict) else len(activities) >= 100
            if not has_more:
                break
            offset += 100
        except Exception as e:
            logger.error(f"Parqet API Fehler bei Offset {offset}: {e}")
            break

    if not all_activities:
        return []

    # Cache activities fuer History-Endpoint (vermeidet zweiten API-Call)
    global _cached_activities
    _cached_activities = all_activities
    # Auch auf Disk speichern (fuer Restarts wenn Portfolio aus Cache geladen wird)
    _save_activities_cache(all_activities)
    logger.info(f"Parqet API: {len(all_activities)} Activities geladen (cached)")
    
    return _aggregate_activities(all_activities)


async def _try_connect_api(
    client: httpx.AsyncClient, headers: dict, portfolio_id: str
) -> list[PortfolioPosition]:
    """Versucht Portfolio-Daten über die Connect API zu laden.

    Parqet Connect API (developer.parqet.com):
    GET /portfolios/{portfolioId}/activities?limit=500&cursor=...
    Response: {"activities": [...], "cursor": "string|null"}
    - limit: 10-500 (default 100)
    - cursor: null = keine weiteren Seiten
    """
    base_url = f"{PARQET_CONNECT_API}/portfolios/{portfolio_id}/activities"

    # Erste Seite mit max limit laden (500 statt default 100)
    first_url = f"{base_url}?limit=500"
    resp = await client.get(first_url, headers=headers)

    if resp.status_code == 401 and settings.PARQET_REFRESH_TOKEN:
        logger.info("Parqet Connect API: Token abgelaufen, versuche Refresh…")
        new_token = await _refresh_oauth_token()
        if not new_token:
            return []
        headers["Authorization"] = f"Bearer {new_token}"
        resp = await client.get(first_url, headers=headers)

    if resp.status_code != 200:
        logger.debug(f"Parqet Connect API: {resp.status_code}")
        # Debug: Verfügbare Portfolios auflisten
        try:
            port_resp = await client.get(f"{PARQET_CONNECT_API}/portfolios", headers=headers)
            if port_resp.status_code == 200:
                portfolios = port_resp.json().get("items", [])
                ids = [p.get("id", "?") for p in portfolios]
                logger.info(f"Parqet Connect API: Verfügbare Portfolios: {ids}")
                logger.info(f"Parqet Connect API: Angefragte Portfolio-ID: {portfolio_id}")
        except Exception:
            pass
        return []

    # Cursor-Pagination: alle Seiten sammeln
    all_activities = []
    page = 1

    while True:
        body = resp.json()

        # Offizielles Format: {"activities": [...], "cursor": "string|null"}
        if isinstance(body, dict):
            activities = body.get("activities", [])
        elif isinstance(body, list):
            activities = body
        else:
            activities = []

        if not activities:
            break

        all_activities.extend(activities)
        logger.info(f"Connect API Seite {page}: {len(activities)} Activities (gesamt: {len(all_activities)})")

        # Cursor für nächste Seite (null = Ende)
        cursor = body.get("cursor") if isinstance(body, dict) else None
        if not cursor:
            break

        page += 1
        resp = await client.get(f"{base_url}?limit=500&cursor={cursor}", headers=headers)
        if resp.status_code != 200:
            logger.warning(f"Connect API Pagination Fehler auf Seite {page}: {resp.status_code}")
            break

    logger.info(f"Parqet Connect API: {len(all_activities)} Activities über {page} Seite(n) geladen")

    positions = _aggregate_activities(all_activities)
    if positions:
        logger.info(f"Parqet Connect API: {len(positions)} Positionen aggregiert")
        # Activities cachen für History-Endpoint
        global _cached_activities
        _cached_activities = all_activities
        _save_activities_cache(all_activities)
        await _enrich_positions(client, headers, portfolio_id, positions)

    return positions


def _parse_portfolio_response(data: dict | list, url: str) -> list[PortfolioPosition]:
    """
    Parst verschiedene Parqet API-Antwortformate zu PortfolioPositions.
    Die interne API hat unterschiedliche Formate je nach Endpoint.
    """
    # Activities endpoint → aggregate
    if "activities" in url or "activity" in url:
        activities = data if isinstance(data, list) else data.get("activities", data.get("data", []))
        return _aggregate_activities(activities)

    # Portfolio endpoint with holdings
    if isinstance(data, dict):
        holdings = (data.get("holdings", []) or data.get("positions", [])
                    or data.get("items", []))
        if holdings:
            return _parse_holdings(holdings)

        # Maybe it's a portfolio list
        portfolios = data.get("portfolios", [])
        if portfolios:
            # Return holdings from the first/matched portfolio
            for p in portfolios:
                h = p.get("holdings", []) or p.get("positions", [])
                if h:
                    return _parse_holdings(h)

    # Direct list of portfolios or holdings
    if isinstance(data, list):
        # Check if it's a list of holdings or portfolios
        if data and isinstance(data[0], dict):
            if any(k in data[0] for k in ["shares", "quantity", "amount", "ticker", "isin"]):
                return _parse_holdings(data)
            if any(k in data[0] for k in ["type", "activityType"]):
                return _aggregate_activities(data)
            # Might be portfolios
            for p in data:
                h = p.get("holdings", []) or p.get("positions", [])
                if h:
                    return _parse_holdings(h)

    return []


def _parse_holdings(holdings: list) -> list[PortfolioPosition]:
    """Parst eine Liste von Holding-Objekten zu PortfolioPositions."""
    positions = []
    for h in holdings:
        if not isinstance(h, dict):
            continue

        # Extract fields with various key names
        asset = h.get("asset", {}) if isinstance(h.get("asset"), dict) else {}
        ticker = h.get("ticker") or h.get("symbol") or asset.get("ticker", "")
        isin = h.get("isin") or asset.get("isin", "")
        name = h.get("name") or h.get("assetName") or asset.get("name", "")

        if not ticker and isin:
            ticker = ISIN_TICKER_MAP.get(isin, isin)
        if not ticker:
            continue

        shares = float(h.get("shares", h.get("quantity", h.get("amount", 0))) or 0)
        avg_cost = float(h.get("purchasePrice", h.get("avgCost",
                   h.get("averageCost", h.get("purchaseValue", 0)))) or 0)
        current_price = float(h.get("currentPrice", h.get("price",
                        h.get("lastPrice", h.get("currentValue", 0)))) or 0)
        currency = h.get("currency", "EUR")

        # Handle case where avg_cost is total cost instead of per-share
        if avg_cost > 0 and shares > 0 and avg_cost > current_price * 10:
            avg_cost = avg_cost / shares

        if shares > 0:
            positions.append(PortfolioPosition(
                ticker=ticker.upper(),
                isin=isin,
                name=name or ticker,
                shares=shares,
                avg_cost=avg_cost,
                current_price=current_price,
                currency=currency,
            ))

    return positions

def _aggregate_activities(activities: list) -> list[PortfolioPosition]:
    """
    Aggregiert Buy/Sell-Aktivitäten zu Netto-Positionen.
    Unterstützt sowohl CSV- als auch API-Format.
    """
    holdings: dict[str, dict] = {}
    cash_balance = 0.0
    # Parqet API liefert Activities in umgekehrter Reihenfolge (neueste zuerst)
    # → chronologisch sortieren, damit Sells nach Buys verarbeitet werden
    activities = sorted(
        [a for a in activities if isinstance(a, dict)],
        key=lambda a: a.get("datetime") or a.get("date") or ""
    )

    for act in activities:

        act_type = (act.get("type") or act.get("activityType") or "").lower()
        
        # API-Format: holdingAssetType=Cash/cash → Cash-Transaktion (überspringen für Positionen)
        # Connect API sendet lowercase ("cash"), Internal API sendet "Cash"
        if (act.get("holdingAssetType") or "").lower() == "cash":
            # Cash-Activities tracken für Saldo
            amount = float(act.get("amount") or 0)
            fee = float(act.get("fee") or 0)
            tax = float(act.get("tax") or 0)
            if act_type in ("buy",):
                cash_balance -= (amount + fee + tax)
            elif act_type in ("sell",):
                cash_balance += (amount - fee - tax)
            elif act_type in ("transferin", "transfer_in", "interest", "deposit"):
                cash_balance += amount
            elif act_type in ("transferout", "transfer_out", "withdrawal"):
                cash_balance -= amount
            elif act_type in ("dividend",):
                cash_balance += amount - fee - tax
            elif act_type in ("cost", "fees_taxes"):
                # tax is negative for Steuererstattungen (tax refunds)
                # fee = Kosten (subtracted), tax = negative refund (added back)
                cash_balance -= (fee + tax)
            continue

        isin = act.get("isin") or act.get("identifier") or ""
        # Connect API: asset.isin / Internal API: asset.identifier
        if not isin:
            asset = act.get("asset") or {}
            isin = asset.get("isin") or asset.get("identifier", "")
        
        ticker = act.get("ticker") or act.get("symbol") or ""
        # API-Format: sharedAsset.name
        name = act.get("name") or act.get("assetName") or ""
        if not name:
            name = (act.get("sharedAsset") or {}).get("name", "")
        
        shares = float(act.get("shares") or act.get("quantity") or 0)
        price = float(act.get("price") or act.get("unitPrice") or 0)
        amount = float(act.get("amount") or 0)
        fee = float(act.get("fee") or 0)
        tax = float(act.get("tax") or 0)
        currency = act.get("currency") or "EUR"

        if not isin and not ticker:
            continue

        # Resolve ticker from ISIN if needed
        if not ticker and isin:
            ticker = ISIN_TICKER_MAP.get(isin, isin)

        key = (ticker or isin).upper()

        if key not in holdings:
            holdings[key] = {
                "ticker": ticker.upper() if ticker else isin,
                "isin": isin,
                "name": name or key,
                "shares": 0.0,
                "total_cost": 0.0,
                "currency": currency,
            }

        h = holdings[key]

        if act_type in ("buy", "kauf", "purchase"):
            h["shares"] += shares
            h["total_cost"] += amount if amount > 0 else (shares * price)
        elif act_type in ("transferin", "transfer_in"):
            h["shares"] += shares
            h["total_cost"] += shares * price if price > 0 else amount
        elif act_type in ("sell", "verkauf", "sale"):
            # Reduce cost proportionally using avg acquisition cost (NOT sell price!)
            if h["shares"] > 0:
                avg_per_share = h["total_cost"] / h["shares"]
                h["total_cost"] -= shares * avg_per_share
            h["shares"] -= shares
        elif act_type in ("transferout", "transfer_out"):
            if h["shares"] > 0:
                avg_per_share = h["total_cost"] / h["shares"]
                h["total_cost"] -= shares * avg_per_share
            h["shares"] -= shares
        # Note: Dividends/fees for Cash are handled by holdingAssetType=Cash above
        # Always keep name up-to-date
        if name:
            h["name"] = name

    positions = []
    
    # Add Cash position if significant
    if cash_balance > 1:
        positions.append(PortfolioPosition(
            ticker="CASH",
            isin="",
            name="Verrechnungskonto",
            shares=1,
            avg_cost=cash_balance,
            current_price=cash_balance,
            currency="EUR",
        ))
    
    # Add stock positions
    for h in holdings.values():
        if abs(h["shares"]) <= 0.001:
            continue
        if h["shares"] < -0.001:
            logger.warning(f"Position {h['ticker']} hat negative Shares: {h['shares']:.4f} – übersprungen")
            continue
        
        avg = h["total_cost"] / h["shares"] if h["shares"] > 0 else 0
        ticker = h["ticker"]
        isin = h["isin"]
        
        # Fonds mit ISIN als Ticker kennzeichnen
        sector = "Unknown"
        if ticker == isin and len(ticker) == 12:
            sector = "Fund"
        
        positions.append(PortfolioPosition(
            ticker=ticker,
            isin=isin,
            name=h["name"],
            shares=h["shares"],
            avg_cost=avg,
            sector=sector,
            currency=h["currency"],
        ))
    
    return positions


async def _enrich_positions(
    client: httpx.AsyncClient,
    headers: dict,
    portfolio_id: str,
    positions: list[PortfolioPosition],
):
    """
    Versucht, aktuelle Kurse aus dem Portfolio-Endpunkt zu laden
    und in die Positionen einzutragen.
    """
    try:
        url = f"{PARQET_INTERNAL_API}/portfolios/{portfolio_id}"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return

        data = resp.json()
        # Build lookup by ticker/ISIN
        price_map: dict[str, float] = {}
        for item in data.get("holdings", data.get("positions", [])):
            if not isinstance(item, dict):
                continue
            t = (item.get("ticker") or item.get("symbol") or "").upper()
            i = item.get("isin") or ""
            p = float(item.get("currentPrice") or item.get("price") or item.get("lastPrice") or 0)
            if t:
                price_map[t] = p
            if i:
                price_map[i] = p

        for pos in positions:
            cp = price_map.get(pos.ticker) or price_map.get(pos.isin or "") or 0
            if cp:
                pos.current_price = cp

    except Exception as e:
        logger.debug(f"Parqet API: Kurs-Enrichment fehlgeschlagen: {e}")


# ---------------------------------------------------------------------------
# OAuth2 Token Management
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cache (delegiert an CacheManager, Funktionen oben definiert)
# ---------------------------------------------------------------------------



async def fetch_portfolio_performance(timeframe: str = "max") -> list[dict]:
    """Holt Portfolio-Performance-Chart von der Parqet API.

    Args:
        timeframe: Zeitraum ("1m", "3m", "6m", "1y", "3y", "max")

    Returns:
        Liste von {date, totalValue, investedCapital} Datenpunkten
    """
    access_token = await _ensure_valid_token()
    if not access_token:
        return []

    portfolio_id = settings.PARQET_PORTFOLIO_ID
    if not portfolio_id:
        return []

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Parqet internal API: Portfolio-Performance-Chart
            url = f"{PARQET_INTERNAL_API}/portfolios/{portfolio_id}/performance"
            params = {"timeframe": timeframe}
            resp = await client.get(url, headers=headers, params=params)

            if resp.status_code == 401:
                new_token = await _ensure_valid_token()
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    resp = await client.get(url, headers=headers, params=params)

            if resp.status_code != 200:
                logger.warning(f"Parqet Performance API: {resp.status_code}")
                return []

            data = resp.json()

            # Parqet gibt verschiedene Formate zurück
            if isinstance(data, list):
                # Direkte Liste von Datenpunkten
                return [
                    {
                        "date": entry.get("date", ""),
                        "totalValue": entry.get("totalValue") or entry.get("value") or 0,
                        "investedCapital": entry.get("investedCapital") or entry.get("invested") or 0,
                    }
                    for entry in data
                    if entry.get("date")
                ]
            elif isinstance(data, dict):
                # Verschachteltes Format mit chart/data key
                chart_data = data.get("chart", data.get("data", data.get("performance", [])))
                if isinstance(chart_data, list):
                    return [
                        {
                            "date": entry.get("date", ""),
                            "totalValue": entry.get("totalValue") or entry.get("value") or 0,
                            "investedCapital": entry.get("investedCapital") or entry.get("invested") or 0,
                        }
                        for entry in chart_data
                        if entry.get("date")
                    ]

            return []

    except Exception as e:
        logger.error(f"Parqet Performance API Fehler: {e}")
        return []


async def fetch_portfolio_activities_raw() -> list[dict]:
    """Gibt alle Kauf/Verkauf/Dividend Activities mit Datum zurueck.

    Nutzt den Cache aus _try_internal_api (kein separater API-Call noetig).
    Fallback: Neuer API-Call wenn Cache leer.

    Returns:
        Liste von {date, type, ticker, name, shares, price, amount, currency}
    """
    global _cached_activities

    # Bevorzugt gecachte Activities verwenden
    all_activities = _cached_activities

    # Fallback 1: Disk-Cache laden
    if not all_activities and ACTIVITIES_CACHE_FILE.exists():
        try:
            all_activities = json.loads(ACTIVITIES_CACHE_FILE.read_text(encoding="utf-8"))
            _cached_activities = all_activities
            logger.info(f"Parqet Activities: {len(all_activities)} aus Disk-Cache geladen")
        except Exception:
            pass

    # Fallback 2: Connect API mit Cursor-Pagination + Safety Cap (OAuth-Token)
    # Offset-Pagination ist NICHT verwendbar (API gibt endlos Duplikate zurück!)
    MAX_ACTIVITIES = 5000  # Sicherheitslimit gegen Endlos-Loops
    if not all_activities:
        access_token = await _ensure_valid_token()
        if not access_token:
            return []
        portfolio_id = settings.PARQET_PORTFOLIO_ID
        if not portfolio_id:
            return []
        headers = {"Authorization": f"Bearer {access_token}"}
        base_url = f"{PARQET_CONNECT_API}/portfolios/{portfolio_id}/activities"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                all_activities = []
                cursor = None
                page = 0
                while True:
                    url = f"{base_url}?limit=500"
                    if cursor:
                        url += f"&cursor={cursor}"
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 401:
                        new_token = await _refresh_oauth_token()
                        if new_token:
                            headers["Authorization"] = f"Bearer {new_token}"
                            resp = await client.get(url, headers=headers)
                    if resp.status_code != 200:
                        logger.warning(f"Connect API Activities: {resp.status_code}")
                        break
                    body = resp.json()
                    activities = body.get("activities", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
                    if not activities:
                        break
                    all_activities.extend(activities)
                    page += 1
                    logger.info(f"Activities Seite {page}: {len(activities)} (gesamt: {len(all_activities)})")
                    cursor = body.get("cursor") if isinstance(body, dict) else None
                    if not cursor:
                        break
                    if len(all_activities) >= MAX_ACTIVITIES:
                        logger.warning(f"Activities Safety-Limit ({MAX_ACTIVITIES}) erreicht!")
                        break
                if all_activities:
                    _cached_activities = all_activities
                    _save_activities_cache(all_activities)
                    logger.info(f"Activities geladen: {len(all_activities)} via Connect API ({page} Seiten)")
        except Exception as e:
            logger.error(f"Parqet Activities API Fehler: {e}")
            return []

    if not all_activities:
        return []

    # Parse zu strukturierten Eintraegen
    result = []
    for act in all_activities:
        act_type = (act.get("type") or "").lower()
        hat = (act.get("holdingAssetType") or "").lower()
        if hat == "cash" and act_type not in ("transferin", "transferout", "transfer_in", "transfer_out", "deposit", "withdrawal"):
            continue

        date = act.get("datetime") or act.get("date") or ""
        if date and "T" in date:
            date = date.split("T")[0]

        # ISIN aus verschiedenen API-Formaten auslesen
        asset = act.get("asset") or {}
        isin = act.get("isin") or asset.get("isin") or asset.get("identifier", "")
        ticker = ISIN_TICKER_MAP.get(isin, isin) if isin else ""
        name = act.get("name") or (act.get("sharedAsset") or {}).get("name", "")

        result.append({
            "date": date,
            "type": act_type,
            "ticker": ticker,
            "name": name,
            "shares": float(act.get("shares") or 0),
            "price": float(act.get("price") or 0),
            "amount": float(act.get("amount") or 0),
            "currency": act.get("currency") or "EUR",
        })

    result.sort(key=lambda x: x["date"])
    return result


def clear_cache():
    """Loescht Cache und gespeicherte Tokens."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    logger.info("Parqet Cache geloescht")


# ─────────────────────────────────────────────────────────────
# Parqet Performance API (POST /performance)
# ─────────────────────────────────────────────────────────────

_performance_cache = CacheManager("parqet_performance", ttl_hours=1)

async def fetch_portfolio_performance() -> dict | None:
    """Lädt die komplette Portfolio-Performance über die Connect API.

    POST https://connect.parqet.com/performance
    Body: {"portfolioIds": ["<id>"]}

    Returns: Strukturiertes Dict mit KPIs, Holdings, Steuern, Dividenden.
    """
    # 1. Cache prüfen
    cached = _performance_cache.get("performance_data")
    if cached:
        logger.debug("Performance: aus Cache geladen")
        return cached

    # 2. API-Call
    access_token = await _ensure_valid_token()
    if not access_token:
        logger.warning("Performance API: Kein Token verfügbar")
        return None

    portfolio_id = settings.PARQET_PORTFOLIO_ID
    if not portfolio_id:
        logger.warning("Performance API: Keine Portfolio-ID konfiguriert")
        return None

    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{PARQET_CONNECT_API}/performance"
    body = {"portfolioIds": [portfolio_id]}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)

            if resp.status_code == 401:
                new_token = await _refresh_oauth_token()
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    resp = await client.post(url, headers=headers, json=body)

            if resp.status_code != 200:
                logger.warning(f"Performance API: {resp.status_code}")
                return None

            data = resp.json()

    except Exception as e:
        logger.error(f"Performance API Fehler: {e}")
        return None

    # 3. Response parsen
    perf = data.get("performance", {})
    raw_holdings = data.get("holdings", [])
    interval = data.get("interval", {})

    # Portfolio-Level KPIs
    kpis = {
        "valuation": perf.get("valuation", {}).get("atIntervalEnd", 0),
        "unrealizedGains": perf.get("unrealizedGains", {}).get("inInterval", {}),
        "realizedGains": perf.get("realizedGains", {}).get("inInterval", {}),
        "dividends": perf.get("dividends", {}).get("inInterval", {}),
        "taxes": perf.get("taxes", {}).get("inInterval", {}).get("taxes", 0),
        "fees": perf.get("fees", {}).get("inInterval", {}).get("fees", 0),
        "interval": interval,
    }

    # Holdings parsen
    holdings = []
    for h in raw_holdings:
        asset = h.get("asset", {})
        pos = h.get("position", {})
        h_perf = h.get("performance", {})

        holding = {
            "id": h.get("id", ""),
            "name": asset.get("name", h.get("nickname", "")),
            "isin": asset.get("isin", ""),
            "type": asset.get("type", ""),  # "security" | "cash"
            "logo": h.get("logo", ""),
            "earliestActivityDate": h.get("earliestActivityDate", ""),
            "activityCount": h.get("activityCount", 0),
            "isSold": pos.get("isSold", False),
            # Position
            "shares": pos.get("shares", 0),
            "purchasePrice": pos.get("purchasePrice", 0),
            "purchaseValue": pos.get("purchaseValue", 0),
            "currentPrice": pos.get("currentPrice", 0),
            "currentValue": pos.get("currentValue", 0),
            # Performance
            "unrealizedGainGross": h_perf.get("unrealizedGains", {}).get("inInterval", {}).get("gainGross", 0),
            "unrealizedReturnGross": h_perf.get("unrealizedGains", {}).get("inInterval", {}).get("returnGross", 0),
            "realizedGainGross": h_perf.get("realizedGains", {}).get("inInterval", {}).get("gainGross", 0),
            "dividendsGross": h_perf.get("dividends", {}).get("inInterval", {}).get("gainGross", 0),
            "dividendsNet": h_perf.get("dividends", {}).get("inInterval", {}).get("gainNet", 0),
            "taxes": h_perf.get("taxes", {}).get("inInterval", {}).get("taxes", 0),
            "fees": h_perf.get("fees", {}).get("inInterval", {}).get("fees", 0),
        }

        # Ticker aus ISIN auflösen
        if holding["isin"]:
            holding["ticker"] = ISIN_TICKER_MAP.get(holding["isin"], holding["isin"])
        elif holding["type"] == "cash":
            holding["ticker"] = "CASH"
            holding["name"] = holding["name"] or "💵 Cash"
        else:
            holding["ticker"] = ""

        holdings.append(holding)

    # Sortieren: aktive zuerst, dann nach currentValue absteigend
    holdings.sort(key=lambda h: (h["isSold"], -h["currentValue"]))

    result = {
        "kpis": kpis,
        "holdings": holdings,
        "holdingsActive": [h for h in holdings if not h["isSold"]],
        "holdingsSold": [h for h in holdings if h["isSold"]],
        "totalHoldings": len(holdings),
        "activeHoldings": sum(1 for h in holdings if not h["isSold"]),
        "soldHoldings": sum(1 for h in holdings if h["isSold"]),
    }

    # 4. Cachen
    _performance_cache.set("performance_data", result)
    logger.info(
        f"Performance API: {result['activeHoldings']} aktiv, "
        f"{result['soldHoldings']} verkauft, "
        f"Wert: {kpis['valuation']:,.2f}€"
    )

    return result
