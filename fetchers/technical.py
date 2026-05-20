"""PortfolioPilot - Technical Indicators Fetcher

Berechnet RSI(14), SMA(50/200) Crossover und 30-Tage-Momentum
aus yfinance-Historiedaten. Kein zusätzlicher API-Call nötig.

Ersetzt das fragile Stocknear-Scraping durch stabile Berechnungen.
"""
import logging
import concurrent.futures
from typing import Optional

from cache_manager import CacheManager
from models import TechnicalIndicators

logger = logging.getLogger(__name__)

_cache = CacheManager("technical", ttl_hours=4)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="tech")


async def fetch_technical_indicators(ticker_symbol: str) -> TechnicalIndicators:
    """Berechnet technische Indikatoren für einen Ticker."""
    import asyncio

    cache_key = f"tech_{ticker_symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return TechnicalIndicators(**cached)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _calc_indicators_sync, ticker_symbol)
        if result and (result.rsi_14 is not None or result.sma_50 is not None):
            _cache.set(cache_key, result.model_dump())
            return result  # flush am Batch-Ende in data_loader
    except Exception as e:
        logger.warning(f"Technical Indicators fehlgeschlagen für {ticker_symbol}: {e}")

    return TechnicalIndicators()


def _calc_indicators_sync(ticker_symbol: str) -> Optional[TechnicalIndicators]:
    """Synchrone Berechnung der technischen Indikatoren."""
    try:
        import yfinance as yf
        import re

        # Skip ISINs
        if re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", ticker_symbol):
            return None

        # Alias mapping (import von state für YFINANCE_ALIASES)
        try:
            from state import YFINANCE_ALIASES
            yf_ticker = YFINANCE_ALIASES.get(ticker_symbol, ticker_symbol)
        except ImportError:
            yf_ticker = ticker_symbol

        t = yf.Ticker(yf_ticker)
        hist = t.history(period="1y")

        if hist is None or hist.empty or len(hist) < 30:
            logger.debug(f"Nicht genug Historiedaten für {ticker_symbol} ({len(hist) if hist is not None else 0} Tage)")
            return None

        closes = hist["Close"].dropna()
        if len(closes) < 30:
            return None

        current_price = float(closes.iloc[-1])

        # --- RSI(14) ---
        rsi = _calc_rsi(closes, period=14)

        # --- SMA(50) und SMA(200) ---
        sma_50 = float(closes.rolling(window=50).mean().iloc[-1]) if len(closes) >= 50 else None
        sma_200 = float(closes.rolling(window=200).mean().iloc[-1]) if len(closes) >= 200 else None

        # --- Price vs SMA50 ---
        price_vs_sma50 = None
        if sma_50 and sma_50 > 0:
            price_vs_sma50 = round(((current_price / sma_50) - 1) * 100, 2)

        # --- SMA Cross ---
        sma_cross = "neutral"
        if sma_50 is not None and sma_200 is not None:
            if sma_50 > sma_200 * 1.01:
                sma_cross = "golden"  # Bullish
            elif sma_50 < sma_200 * 0.99:
                sma_cross = "death"   # Bearish

        # --- 30-Tage Momentum ---
        price_30d_ago = float(closes.iloc[-min(30, len(closes))])
        momentum_30d = round(((current_price - price_30d_ago) / price_30d_ago) * 100, 2)

        # --- 90-Tage (3M) Momentum ---
        momentum_90d = None
        if len(closes) >= 90:
            price_90d_ago = float(closes.iloc[-90])
            if price_90d_ago > 0:
                momentum_90d = round(((current_price - price_90d_ago) / price_90d_ago) * 100, 2)

        # --- 180-Tage (6M) Momentum ---
        momentum_180d = None
        if len(closes) >= 180:
            price_180d_ago = float(closes.iloc[-180])
            if price_180d_ago > 0:
                momentum_180d = round(((current_price - price_180d_ago) / price_180d_ago) * 100, 2)

        # --- Gesamtsignal ---
        bullish_signals = 0
        bearish_signals = 0

        if rsi is not None:
            if rsi < 30:
                bullish_signals += 1   # Überverkauft = Kaufchance
            elif rsi > 70:
                bearish_signals += 1   # Überkauft = Vorsicht

        if sma_cross == "golden":
            bullish_signals += 1
        elif sma_cross == "death":
            bearish_signals += 1

        if momentum_30d > 5:
            bullish_signals += 1
        elif momentum_30d < -5:
            bearish_signals += 1

        if price_vs_sma50 is not None:
            if price_vs_sma50 > 3:
                bullish_signals += 1
            elif price_vs_sma50 < -3:
                bearish_signals += 1

        if bullish_signals >= 2:
            signal = "Bullish"
        elif bearish_signals >= 2:
            signal = "Bearish"
        else:
            signal = "Neutral"

        return TechnicalIndicators(
            rsi_14=round(rsi, 2) if rsi is not None else None,
            sma_50=round(sma_50, 2) if sma_50 is not None else None,
            sma_200=round(sma_200, 2) if sma_200 is not None else None,
            price_vs_sma50=price_vs_sma50,
            sma_cross=sma_cross,
            momentum_30d=momentum_30d,
            momentum_90d=momentum_90d,
            momentum_180d=momentum_180d,
            signal=signal,
        )

    except Exception as e:
        logger.debug(f"Technical Indicators Berechnung fehlgeschlagen für {ticker_symbol}: {e}")
        return None


def _calc_rsi(closes, period: int = 14) -> Optional[float]:
    """Berechnet den Relative Strength Index (RSI).

    RSI = 100 - (100 / (1 + RS))
    RS = Average Gain / Average Loss (über `period` Tage)
    """
    if len(closes) < period + 1:
        return None

    deltas = closes.diff().dropna()

    gains = deltas.where(deltas > 0, 0.0)
    losses = (-deltas).where(deltas < 0, 0.0)

    # Exponential Moving Average (wie bei Wilder's RSI)
    avg_gain = gains.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]
    avg_loss = losses.ewm(alpha=1/period, min_periods=period, adjust=False).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def clear_cache():
    """Löscht den Technical Indicators Cache."""
    _cache.clear()
