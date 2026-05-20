"""PortfolioPilot - yfinance WebSocket Tests

Testet den YFinanceStreamer In-Memory-Cache, Subscribe/Unsubscribe-Logik
und Message-Processing. Keine echte WebSocket-Verbindung nötig.
Testet den yfinance WebSocket-Streamer (Unit-Tests).
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fetchers.yfinance_ws import YFinanceStreamer


# ==================== YFinanceStreamer Cache Tests ====================

class TestYFinanceStreamerCache:
    """Tests für den In-Memory-Preiscache."""

    def test_empty_cache_returns_none(self):
        streamer = YFinanceStreamer()
        assert streamer.get_price("SIE.DE") is None

    def test_update_and_get_price(self):
        streamer = YFinanceStreamer()
        streamer.update_price("SIE.DE", 175.50)
        assert streamer.get_price("SIE.DE") == 175.50

    def test_case_insensitive_get(self):
        streamer = YFinanceStreamer()
        streamer.update_price("sie.de", 175.50)
        assert streamer.get_price("SIE.DE") == 175.50

    def test_get_all_prices(self):
        streamer = YFinanceStreamer()
        streamer.update_price("SIE.DE", 175.0)
        streamer.update_price("DTE.DE", 25.0)
        prices = streamer.get_all_prices()
        assert prices == {"SIE.DE": 175.0, "DTE.DE": 25.0}

    def test_get_all_prices_returns_copy(self):
        """Modifizieren der Kopie ändert nicht den Original-Cache."""
        streamer = YFinanceStreamer()
        streamer.update_price("SIE.DE", 175.0)
        prices = streamer.get_all_prices()
        prices["SIE.DE"] = 999.0  # Modify copy
        assert streamer.get_price("SIE.DE") == 175.0  # Original unchanged

    def test_price_rounded_to_4_decimals(self):
        streamer = YFinanceStreamer()
        streamer.update_price("EURUSD=X", 1.123456789)
        assert streamer.get_price("EURUSD=X") == 1.1235

    def test_price_age(self):
        streamer = YFinanceStreamer()
        streamer.update_price("SIE.DE", 175.0)
        age = streamer.get_price_age("SIE.DE")
        assert age is not None
        assert age < 1.0  # Should be nearly instant

    def test_price_age_none_for_unknown(self):
        streamer = YFinanceStreamer()
        assert streamer.get_price_age("UNKNOWN") is None


# ==================== Subscribe/Unsubscribe Tests ====================

class TestYFinanceStreamerSubscribe:
    """Tests für Subscribe/Unsubscribe-Logik."""

    def test_subscribe_de_tickers(self):
        """Deutsche Ticker (.DE) werden akzeptiert."""
        with patch("fetchers.yfinance_ws.YFINANCE_ALIASES", {}, create=True):
            with patch("state.YFINANCE_ALIASES", {}):
                streamer = YFinanceStreamer()
                streamer.subscribe(["SIE.DE", "BAYN.DE", "SAP.DE"])
                assert "SIE.DE" in streamer._subscribed
                assert "BAYN.DE" in streamer._subscribed
                assert "SAP.DE" in streamer._subscribed

    def test_subscribe_us_tickers(self):
        """US-Ticker werden ebenfalls akzeptiert."""
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["AAPL", "MSFT", "GOOGL"])
            assert "AAPL" in streamer._subscribed
            assert "MSFT" in streamer._subscribed

    def test_subscribe_skips_cash(self):
        """CASH-Position wird übersprungen."""
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["CASH"])
            assert len(streamer._subscribed) == 0

    def test_subscribe_skips_isins(self):
        """ISIN-basierte Ticker werden übersprungen."""
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["US0378331005", "DE000BAY0017"])
            assert len(streamer._subscribed) == 0

    def test_subscribe_uses_aliases(self):
        """Ticker werden über YFINANCE_ALIASES gemapped."""
        with patch("state.YFINANCE_ALIASES", {"DTEGY": "DTE.DE"}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["DTEGY"])
            assert "DTE.DE" in streamer._subscribed
            assert "DTEGY" not in streamer._subscribed

    def test_subscribe_no_duplicates(self):
        """Doppelte Ticker werden nicht nochmal hinzugefügt."""
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["SIE.DE"])
            streamer.subscribe(["SIE.DE", "BAYN.DE"])
            assert len(streamer._subscribed) == 2

    def test_subscribe_mixed_tickers(self):
        """Gemischte Ticker: US + DE + CASH + ISIN."""
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["AAPL", "SIE.DE", "CASH", "US0378331005"])
            assert streamer._subscribed == {"AAPL", "SIE.DE"}

    def test_unsubscribe(self):
        with patch("state.YFINANCE_ALIASES", {}):
            streamer = YFinanceStreamer()
            streamer.subscribe(["SIE.DE", "BAYN.DE"])
            streamer.update_price("SIE.DE", 175.0)
            streamer.unsubscribe(["SIE.DE"])
            assert "SIE.DE" not in streamer._subscribed
            assert streamer.get_price("SIE.DE") is None  # Cache cleared
            assert "BAYN.DE" in streamer._subscribed  # Other unchanged


# ==================== Message Processing Tests ====================

class TestYFinanceStreamerMessageProcessing:
    """Tests für WebSocket-Nachrichtenverarbeitung."""

    def test_process_price_message(self):
        """Standard-Preisnachricht wird korrekt verarbeitet."""
        streamer = YFinanceStreamer()
        msg = {"id": "SIE.DE", "price": 175.50, "change": 1.5, "changePercent": 0.86}
        streamer._process_message(msg)
        assert streamer.get_price("SIE.DE") == 175.50

    def test_process_multiple_messages(self):
        """Mehrere Nachrichten nacheinander."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": 175.50})
        streamer._process_message({"id": "BAYN.DE", "price": 28.30})
        assert streamer.get_price("SIE.DE") == 175.50
        assert streamer.get_price("BAYN.DE") == 28.30

    def test_process_updates_latest_price(self):
        """Neuerer Preis überschreibt alten."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": 175.0})
        streamer._process_message({"id": "SIE.DE", "price": 176.5})
        assert streamer.get_price("SIE.DE") == 176.5

    def test_process_zero_price_ignored(self):
        """Preis 0 wird ignoriert."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": 0})
        assert streamer.get_price("SIE.DE") is None

    def test_process_negative_price_ignored(self):
        """Negativer Preis wird ignoriert."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": -5.0})
        assert streamer.get_price("SIE.DE") is None

    def test_process_none_price_ignored(self):
        """None-Preis wird ignoriert."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": None})
        assert streamer.get_price("SIE.DE") is None

    def test_process_missing_id_ignored(self):
        """Nachricht ohne ID wird ignoriert."""
        streamer = YFinanceStreamer()
        streamer._process_message({"price": 175.0})
        assert len(streamer.get_all_prices()) == 0

    def test_process_empty_message(self):
        """Leere Nachricht verursacht keinen Fehler."""
        streamer = YFinanceStreamer()
        streamer._process_message({})

    def test_process_non_dict_message(self):
        """Nicht-Dict-Nachricht verursacht keinen Fehler."""
        streamer = YFinanceStreamer()
        streamer._process_message("not a dict")
        streamer._process_message(None)
        streamer._process_message(42)

    def test_process_string_price_converted(self):
        """String-Preis wird zu Float konvertiert."""
        streamer = YFinanceStreamer()
        streamer._process_message({"id": "SIE.DE", "price": "175.50"})
        assert streamer.get_price("SIE.DE") == 175.50


# ==================== Connection Status Tests ====================

class TestYFinanceStreamerConnection:
    """Tests für Verbindungsstatus."""

    def test_not_connected_initially(self):
        streamer = YFinanceStreamer()
        assert not streamer.is_connected

    def test_is_connected_when_running_and_ws(self):
        streamer = YFinanceStreamer()
        streamer._running = True
        streamer._ws = MagicMock()
        assert streamer.is_connected

    def test_not_connected_when_not_running(self):
        streamer = YFinanceStreamer()
        streamer._running = False
        streamer._ws = MagicMock()
        assert not streamer.is_connected

    def test_not_connected_when_no_ws(self):
        streamer = YFinanceStreamer()
        streamer._running = True
        streamer._ws = None
        assert not streamer.is_connected


# ==================== Singleton Tests ====================

class TestYFinanceStreamerSingleton:
    """Tests für Singleton-Pattern."""

    def test_get_yf_streamer_returns_same_instance(self):
        from fetchers.yfinance_ws import get_yf_streamer
        s1 = get_yf_streamer()
        s2 = get_yf_streamer()
        assert s1 is s2


# ==================== Stop Tests ====================

class TestYFinanceStreamerStop:
    """Tests für sauberes Stoppen."""

    @pytest.mark.asyncio
    async def test_stop_clears_running(self):
        streamer = YFinanceStreamer()
        streamer._running = True
        streamer._ws = None
        await streamer.stop()
        assert not streamer._running

    @pytest.mark.asyncio
    async def test_stop_closes_ws(self):
        streamer = YFinanceStreamer()
        streamer._running = True
        mock_ws = AsyncMock()
        streamer._ws = mock_ws
        await streamer.stop()
        mock_ws.close.assert_called_once()
        assert streamer._ws is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        streamer = YFinanceStreamer()
        streamer._running = True
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        mock_task.__await__ = lambda self: iter([None])
        streamer._task = mock_task
        await streamer.stop()
        mock_task.cancel.assert_called_once()
