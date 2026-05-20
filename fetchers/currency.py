"""PortfolioPilot - Wechselkurs Fetcher

Holt aktuelle Wechselkurse für die Währungsumrechnung im Dashboard.
Nutzt FMP Forex-Endpoint oder Fallback auf ExchangeRate API.
Optimiert: Ein einziger API-Call für alle benötigten Kurse.
"""
import logging
from typing import Optional

import httpx

from cache_manager import CacheManager
from config import settings

logger = logging.getLogger(__name__)

_cache = CacheManager("currency", ttl_hours=12)  # Wechselkurse ändern sich <0.5%/Tag

# Default Fallback-Kurse
DEFAULT_EUR_USD = 1.08
DEFAULT_EUR_DKK = 7.46
DEFAULT_EUR_GBP = 0.855
DEFAULT_EUR_CNY = 7.80

# Reusable HTTP-Client (vermeidet TCP-Handshake pro Aufruf)
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Gibt den wiederverwendbaren HTTP-Client zurück."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=10)
    return _http_client


async def _fetch_all_rates_from_exchangerate_api() -> Optional[dict]:
    """Holt alle Wechselkurse mit einem einzigen API-Call."""
    cache_key = "all_rates"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = _get_client()
        response = await client.get("https://api.exchangerate-api.com/v4/latest/EUR")
        response.raise_for_status()
        data = response.json()
        rates = data.get("rates", {})
        if rates:
            _cache.set(cache_key, rates)
            logger.info(
                "Wechselkurse geladen: "
                f"USD={rates.get('USD')}, DKK={rates.get('DKK')}, "
                f"GBP={rates.get('GBP')}, CNY={rates.get('CNY')}"
            )
            return rates
    except Exception as e:
        logger.debug(f"ExchangeRate API fehlgeschlagen: {e}")

    return None


async def _fetch_from_fmp() -> Optional[float]:
    """Versucht den EUR/USD-Kurs über FMP zu holen."""
    if settings.demo_mode:
        return None

    try:
        url = f"{settings.FMP_BASE_URL}/quote"
        client = _get_client()
        response = await client.get(url, params={
            "symbol": "EURUSD",
            "apikey": settings.FMP_API_KEY,
        })
        response.raise_for_status()
        data = response.json()

        if isinstance(data, list) and len(data) > 0:
            price = data[0].get("price")
            if price and float(price) > 0:
                rate = round(float(price), 4)
                logger.info(f"EUR/USD via FMP: {rate}")
                return rate
        elif isinstance(data, dict):
            price = data.get("price")
            if price and float(price) > 0:
                rate = round(float(price), 4)
                logger.info(f"EUR/USD via FMP: {rate}")
                return rate
    except Exception as e:
        logger.debug(f"FMP Forex fehlgeschlagen: {e}")

    return None


async def fetch_eur_usd_rate() -> float:
    """Holt den aktuellen EUR/USD-Wechselkurs.

    Versucht FMP Forex, dann ExchangeRate API, dann Default.

    Returns:
        EUR/USD-Wechselkurs (z.B. 1.0850)
    """
    cache_key = "eur_usd"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    # Versuch 1: FMP
    rate = await _fetch_from_fmp()

    # Versuch 2: ExchangeRate API (lädt alle Kurse gleichzeitig)
    if rate is None:
        rates = await _fetch_all_rates_from_exchangerate_api()
        if rates:
            usd = rates.get("USD")
            if usd and float(usd) > 0:
                rate = round(float(usd), 4)

    if rate is None:
        logger.warning(f"EUR/USD-Kurs nicht verfügbar – nutze Default {DEFAULT_EUR_USD}")
        return DEFAULT_EUR_USD

    _cache.set(cache_key, rate)
    _cache.flush()  # EUR/USD ist der erste Kurs → flush auslösen
    return rate


async def fetch_eur_dkk_rate() -> float:
    """Holt den aktuellen EUR/DKK-Wechselkurs (für dänische Aktien wie Novo Nordisk).

    Returns:
        EUR/DKK-Wechselkurs (z.B. 7.46)
    """
    cache_key = "eur_dkk"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    rates = await _fetch_all_rates_from_exchangerate_api()
    if rates:
        dkk = rates.get("DKK")
        if dkk and float(dkk) > 0:
            rate = round(float(dkk), 4)
            _cache.set(cache_key, rate)
            return rate  # flush wird nach EUR/USD bereits aufgerufen

    logger.warning(f"EUR/DKK-Kurs nicht verfügbar – nutze Default {DEFAULT_EUR_DKK}")
    return DEFAULT_EUR_DKK


async def fetch_eur_gbp_rate() -> float:
    """Holt den aktuellen EUR/GBP-Wechselkurs (für britische Aktien).

    Returns:
        EUR/GBP-Wechselkurs (z.B. 0.855)
    """
    cache_key = "eur_gbp"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    rates = await _fetch_all_rates_from_exchangerate_api()
    if rates:
        gbp = rates.get("GBP")
        if gbp and float(gbp) > 0:
            rate = round(float(gbp), 4)
            _cache.set(cache_key, rate)
            return rate  # flush wird nach EUR/USD bereits aufgerufen

    logger.warning(f"EUR/GBP-Kurs nicht verfügbar – nutze Default {DEFAULT_EUR_GBP}")
    return DEFAULT_EUR_GBP


async def fetch_eur_cny_rate() -> float:
    """Holt den aktuellen EUR/CNY-Wechselkurs (für China A-Shares)."""
    cache_key = "eur_cny"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    rates = await _fetch_all_rates_from_exchangerate_api()
    if rates:
        cny = rates.get("CNY")
        if cny and float(cny) > 0:
            rate = round(float(cny), 4)
            _cache.set(cache_key, rate)
            return rate

    logger.warning(f"EUR/CNY-Kurs nicht verfügbar – nutze Default {DEFAULT_EUR_CNY}")
    return DEFAULT_EUR_CNY


def clear_cache():
    """Löscht den Currency Cache."""
    _cache.clear()
