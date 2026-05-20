"""PortfolioPilot - SSE Streaming Route.

Server-Sent Events Endpoint für Live-Preis-Updates (yFinance WS + Portfolio).
Alle Preise werden vor dem Senden ans Frontend in EUR konvertiert.
"""
import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from state import portfolio_data, YFINANCE_ALIASES

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_ws_connected() -> bool:
    """Prüft ob yFinance WebSocket verbunden ist."""
    try:
        from fetchers.yfinance_ws import get_yf_streamer
        return get_yf_streamer().is_connected
    except Exception:
        return False


@router.get("/api/prices/stream")
async def stream_prices(request: Request):
    """Server-Sent Events: Live-Preis-Updates ans Frontend."""

    async def _event_generator():
        """Sendet Preis-Diffs alle 3 Sekunden."""
        last_prices: dict[str, float] = {}
        timeout_at = time.time() + 300  # 5 Minuten Timeout

        while time.time() < timeout_at:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            # Collect current prices — ALL in EUR
            current_prices: dict[str, float] = {}

            # Get EUR conversion rate from portfolio summary
            summary = portfolio_data.get("summary")
            eur_usd = 1.08  # Default
            if summary and summary.eur_usd_rate and summary.eur_usd_rate > 0 and summary.eur_usd_rate != 1.0:
                eur_usd = summary.eur_usd_rate

            # Converter für yfinance (nutzt gecachte Wechselkurse)
            converter = None
            try:
                from services.currency_converter import CurrencyConverter
                converter = await CurrencyConverter.create(eur_usd_override=eur_usd)
            except Exception:
                pass

            # 1. yFinance WebSocket prices → convert to EUR
            try:
                from fetchers.yfinance_ws import get_yf_streamer
                yf_streamer = get_yf_streamer()
                if yf_streamer.is_connected and converter:
                    raw_yf = yf_streamer.get_all_prices()
                    for ticker, local_price in raw_yf.items():
                        # yfinance liefert in lokaler Währung (EUR/DKK/GBP/USD)
                        if local_price and local_price > 0:
                            current_prices[ticker] = converter.to_eur(local_price, ticker)
            except Exception:
                pass

            # 2. Portfolio prices for non-WS tickers (already in EUR)
            if summary:
                for stock in summary.stocks:
                    t = stock.position.ticker
                    if t not in current_prices and stock.position.current_price > 0:
                        current_prices[t] = stock.position.current_price

            # Find changed prices (all in EUR now)
            diffs = {}
            for ticker, price in current_prices.items():
                if last_prices.get(ticker) != price:
                    diffs[ticker] = price

            if diffs:
                last_prices.update(diffs)
                # Build SSE event — all prices in EUR
                event_data = json.dumps({
                    "prices": diffs,
                    "timestamp": datetime.now().isoformat(),
                    "ws_connected": _is_ws_connected(),
                })
                yield f"data: {event_data}\n\n"
            else:
                # Send keepalive comment
                yield ": keepalive\n\n"

            await asyncio.sleep(3)

        # Send close event
        yield f"data: {json.dumps({'type': 'timeout'})}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
