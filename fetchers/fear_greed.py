"""PortfolioPilot - Fear & Greed Index Fetcher

Holt den aktuellen Fear & Greed Index als Markt-Sentiment-Indikator.
Primär: CNN Fear & Greed (inoffiziell)
Fallback: alternative.me Crypto Fear & Greed

Kein API-Key nötig, kein striktes Rate Limit.
"""
import logging
from typing import Optional

import httpx

from cache_manager import CacheManager
from config import settings

logger = logging.getLogger(__name__)

_cache = CacheManager("fear_greed", ttl_hours=6)  # Ändert sich nicht so schnell

# CNN Fear & Greed API (inoffiziell)
CNN_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

# alternative.me API (kostenlos & stabil)
ALTERNATIVE_ME_URL = "https://api.alternative.me/fng/"


def _classify_fear_greed(value: int) -> str:
    """Klassifiziert den Fear & Greed Wert."""
    if value <= 20:
        return "Extreme Fear"
    elif value <= 40:
        return "Fear"
    elif value <= 60:
        return "Neutral"
    elif value <= 80:
        return "Greed"
    else:
        return "Extreme Greed"


async def _fetch_cnn_fear_greed() -> Optional[dict]:
    """Versucht den CNN Fear & Greed Index zu holen."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                CNN_FEAR_GREED_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

            fg_data = data.get("fear_and_greed", {})
            score = fg_data.get("score")

            if score is not None:
                value = int(round(float(score)))
                return {
                    "value": max(0, min(100, value)),
                    "label": _classify_fear_greed(value),
                    "source": "CNN",
                }
    except Exception as e:
        logger.debug(f"CNN Fear & Greed fehlgeschlagen: {e}")

    return None


async def _fetch_alternative_me() -> Optional[dict]:
    """Fallback: alternative.me Crypto Fear & Greed Index."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                ALTERNATIVE_ME_URL,
                params={"limit": 1, "format": "json"},
            )
            response.raise_for_status()
            data = response.json()

            if "data" in data and len(data["data"]) > 0:
                entry = data["data"][0]
                value = int(entry.get("value", 50))
                label = entry.get("value_classification", _classify_fear_greed(value))

                return {
                    "value": max(0, min(100, value)),
                    "label": label,
                    "source": "alternative.me",
                }
    except Exception as e:
        logger.debug(f"alternative.me Fear & Greed fehlgeschlagen: {e}")

    return None


async def fetch_fear_greed_index():
    """Holt den aktuellen Fear & Greed Index."""
    from models import FearGreedData

    cache_key = "fear_greed"
    cached = _cache.get(cache_key)
    if cached is not None:
        return FearGreedData(**cached)

    # Versuch 1: CNN
    result = await _fetch_cnn_fear_greed()

    # Versuch 2: alternative.me Fallback
    if result is None:
        result = await _fetch_alternative_me()

    if result is None:
        logger.warning("Fear & Greed Index nicht verfügbar - nutze Default")
        return FearGreedData()

    fg_data = FearGreedData(
        value=result["value"],
        label=result["label"],
        source=result.get("source", "Unknown"),
    )

    _cache.set(cache_key, fg_data.model_dump())
    _cache.flush()

    return fg_data


def clear_cache():
    """Löscht den Fear & Greed Cache."""
    _cache.clear()
