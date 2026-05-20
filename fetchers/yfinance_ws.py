"""PortfolioPilot - yfinance Echtzeit-Kurs-Streaming

WebSocket-basierter Echtzeit-Kursfetcher über yfinance AsyncWebSocket.
Kein API-Key nötig. Primäre Echtzeit-Preisquelle für alle Ticker (US + EU).

Features:
  - AsyncWebSocket-Streaming mit Auto-Reconnect
  - In-Memory-Preiscache (thread-safe)
  - Subscribe/Unsubscribe für Ticker
  - Alle Ticker (US + EU: .DE, .L, .CO)
"""
import asyncio
import logging
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class YFinanceStreamer:
    """WebSocket-Client für yfinance Echtzeit-Kurse.

    Verbindet sich zum Yahoo Finance WebSocket, empfängt Preis-Updates
    und speichert letzte Preise in einem In-Memory-Dict.
    Primäre Echtzeit-Preisquelle für alle Ticker.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}       # ticker -> last price
        self._timestamps: dict[str, float] = {}    # ticker -> unix timestamp
        self._subscribed: set[str] = set()
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._reconnect_delay = 2.0  # seconds, exponential backoff
        self._connected_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    async def start(self):
        """Startet den WebSocket-Stream im Hintergrund."""
        if self._running:
            logger.debug("yfinance WebSocket läuft bereits")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("🔌 yfinance WebSocket-Stream gestartet")

    async def stop(self):
        """Stoppt den WebSocket-Stream sauber."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected_event.clear()
        logger.info("🔌 yfinance WebSocket-Stream gestoppt")

    def subscribe(self, tickers: list[str]):
        """Registriert Ticker für Streaming.

        Fokus auf Nicht-US-Ticker (mit Punkt-Suffix wie .DE, .L, .CO).
        US-Ticker ohne Suffix werden ebenfalls akzeptiert (falls gewünscht).
        """
        from state import YFINANCE_ALIASES
        import re

        new_tickers = []
        # Pattern to detect ISINs (12 chars, 2 letter country + 10 alphanumeric)
        isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")

        for t in tickers:
            t_upper = t.upper()
            if t_upper == "CASH":
                continue
            if isin_pattern.match(t_upper):
                continue

            # Map durch Aliases (z.B. DTEGY → DTE.DE)
            yf_symbol = YFINANCE_ALIASES.get(t_upper, t_upper)

            if yf_symbol not in self._subscribed:
                new_tickers.append(yf_symbol)
                self._subscribed.add(yf_symbol)

        # Send subscribe if already connected
        if new_tickers and self._ws:
            asyncio.create_task(self._send_subscribes(new_tickers))

        if new_tickers:
            logger.info(
                f"yfinance WS: {len(new_tickers)} neue Ticker abonniert: "
                f"{new_tickers[:10]}{'...' if len(new_tickers) > 10 else ''}"
            )

    def unsubscribe(self, tickers: list[str]):
        """Entfernt Ticker aus dem Streaming."""
        for t in tickers:
            t = t.upper()
            self._subscribed.discard(t)
            self._prices.pop(t, None)
            self._timestamps.pop(t, None)

    def get_price(self, ticker: str) -> Optional[float]:
        """Letzten gecachten Kurs für einen Ticker."""
        return self._prices.get(ticker.upper())

    def get_all_prices(self) -> dict[str, float]:
        """Alle gecachten Kurse."""
        return dict(self._prices)

    def get_price_age(self, ticker: str) -> Optional[float]:
        """Alter des letzten Kurses in Sekunden."""
        ts = self._timestamps.get(ticker.upper())
        if ts is None:
            return None
        return time.time() - ts

    def update_price(self, ticker: str, price: float):
        """Manuelles Preis-Update (z.B. aus anderem Fetcher)."""
        ticker = ticker.upper()
        self._prices[ticker] = round(price, 4)
        self._timestamps[ticker] = time.time()

    async def _run_loop(self):
        """Hauptschleife: Connect → Subscribe → Receive → Reconnect."""
        while self._running:
            try:
                import yfinance as yf

                logger.info("yfinance WS: Verbinde zum WebSocket...")
                ws = yf.AsyncWebSocket(verbose=False)
                self._ws = ws

                # Subscribe all registered tickers
                if self._subscribed:
                    await ws.subscribe(list(self._subscribed))
                    logger.info(
                        f"yfinance WebSocket verbunden – "
                        f"{len(self._subscribed)} Ticker abonniert"
                    )

                self._reconnect_delay = 2.0  # Reset backoff on success
                self._connected_event.set()

                # Listen with custom handler
                await ws.listen(message_handler=self._process_message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws = None
                self._connected_event.clear()
                if not self._running:
                    break
                logger.warning(
                    f"yfinance WebSocket-Fehler: {e} – "
                    f"Reconnect in {self._reconnect_delay:.0f}s"
                )
                await asyncio.sleep(self._reconnect_delay)
                # Exponential backoff: 2s → 4s → 8s → 16s → max 60s
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

        self._ws = None
        self._connected_event.clear()

    async def _send_subscribes(self, tickers: list[str]):
        """Sendet Subscribe-Requests an den WebSocket."""
        if not self._ws:
            return
        try:
            await self._ws.subscribe(tickers)
        except Exception as e:
            logger.debug(f"yfinance WS subscribe fehlgeschlagen: {e}")

    def _process_message(self, message: dict):
        """Verarbeitet eine WebSocket-Nachricht von yfinance.

        yfinance WebSocket liefert dicts mit Preis-Informationen.
        Typische Felder: 'id' (Symbol), 'price', 'change', 'changePercent',
        'dayVolume', 'time', etc.
        """
        try:
            if not isinstance(message, dict):
                return

            symbol = message.get("id", "")
            price = message.get("price")

            if symbol and price is not None:
                try:
                    price_float = float(price)
                    if price_float > 0:
                        self._prices[symbol.upper()] = round(price_float, 4)
                        self._timestamps[symbol.upper()] = time.time()
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass


# --- Singleton Instance ---
_yf_streamer = YFinanceStreamer()


def get_yf_streamer() -> YFinanceStreamer:
    """Gibt die Singleton-YFinanceStreamer-Instanz zurück."""
    return _yf_streamer
