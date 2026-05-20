"""PortfolioPilot - Sektor-Rotation-Analyse (A3)

Berechnet relative Sektor-Performance und identifiziert
Rotations-Signale im Portfolio:
  - Rolling 1M/3M Sektor-Momentum
  - Relative Stärke vs. Markt (S&P 500)
  - Rotation-Signal: Aufwärts/Abwärts/Neutral

Datenquelle: yfinance Sektor-ETFs (keine FMP-Calls benötigt).
"""
import logging
import concurrent.futures
from typing import Optional

logger = logging.getLogger(__name__)

# Sektor-ETF-Mapping (SPDR Sector ETFs)
SECTOR_ETFS = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
}

MARKET_ETF = "SPY"  # S&P 500 als Benchmark

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="sector")


async def get_sector_rotation(portfolio_sectors: Optional[list[str]] = None) -> list[dict]:
    """Berechnet Sektor-Rotation-Signale.

    Args:
        portfolio_sectors: Optional — nur diese Sektoren analysieren

    Returns:
        Liste von Dicts: [{sector, etf, mom_1m, mom_3m, rel_strength, signal}]
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _calc_sector_rotation_sync, portfolio_sectors)
        return result or []
    except Exception as e:
        logger.warning(f"Sektor-Rotation-Analyse fehlgeschlagen: {e}")
        return []


def _calc_sector_rotation_sync(portfolio_sectors: Optional[list[str]] = None) -> list[dict]:
    """Synchrone Sektor-Rotation-Berechnung via yfinance."""
    try:
        import yfinance as yf

        # Relevante Sektoren bestimmen
        if portfolio_sectors:
            sectors = {s: SECTOR_ETFS[s] for s in portfolio_sectors if s in SECTOR_ETFS}
        else:
            sectors = SECTOR_ETFS

        if not sectors:
            return []

        # Alle ETFs + Benchmark laden (6 Monate Historie)
        all_tickers = list(set(sectors.values())) + [MARKET_ETF]
        data = yf.download(all_tickers, period="6mo", progress=False, auto_adjust=True)

        if data is None or data.empty:
            return []

        closes = data["Close"] if "Close" in data.columns else data

        # Benchmark (SPY) Performance
        spy_returns_1m = _calc_return(closes, MARKET_ETF, 21)
        spy_returns_3m = _calc_return(closes, MARKET_ETF, 63)

        results = []
        for sector, etf in sectors.items():
            if etf not in closes.columns:
                continue

            mom_1m = _calc_return(closes, etf, 21)
            mom_3m = _calc_return(closes, etf, 63)

            if mom_1m is None:
                continue

            # Relative Stärke vs. Markt
            rel_1m = (mom_1m - spy_returns_1m) if spy_returns_1m is not None else 0
            rel_3m = (mom_3m - spy_returns_3m) if spy_returns_3m is not None and mom_3m is not None else 0

            # Signal bestimmen
            if rel_1m > 2 and rel_3m > 3:
                signal = "Aufwärts"
                signal_emoji = "🟢"
            elif rel_1m < -2 and rel_3m < -3:
                signal = "Abwärts"
                signal_emoji = "🔴"
            elif rel_1m > 3:
                signal = "Dreht hoch"
                signal_emoji = "🟡↑"
            elif rel_1m < -3:
                signal = "Dreht runter"
                signal_emoji = "🟡↓"
            else:
                signal = "Neutral"
                signal_emoji = "⚪"

            results.append({
                "sector": sector,
                "etf": etf,
                "momentum_1m": round(mom_1m, 1),
                "momentum_3m": round(mom_3m, 1) if mom_3m is not None else None,
                "relative_1m": round(rel_1m, 1),
                "relative_3m": round(rel_3m, 1),
                "signal": signal,
                "signal_emoji": signal_emoji,
            })

        # Sortieren nach relativer Stärke (3M)
        results.sort(key=lambda x: x.get("relative_3m", 0), reverse=True)

        logger.info(f"📊 Sektor-Rotation: {len(results)} Sektoren analysiert")
        return results

    except Exception as e:
        logger.warning(f"Sektor-Rotation Berechnung fehlgeschlagen: {e}")
        return []


def _calc_return(closes, ticker: str, days: int) -> Optional[float]:
    """Berechnet prozentuale Rendite über X Tage."""
    try:
        if ticker not in closes.columns:
            return None
        col = closes[ticker].dropna()
        if len(col) < days:
            return None
        current = float(col.iloc[-1])
        past = float(col.iloc[-days])
        if past <= 0:
            return None
        return ((current - past) / past) * 100
    except Exception:
        return None
