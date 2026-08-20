"""Provider identity and no-network adapter contract tests."""
from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import pytest

from app.providers.market_data import MarketSecurity, YFinanceProvider
from app.services.security_master import canonicalize_security


def test_a_share_and_us_symbols_are_explicitly_mapped() -> None:
    shanghai = canonicalize_security(
        ticker="600519.SH", exchange="SSE", market="CN-A", currency="CNY"
    )
    assert shanghai.canonical_symbol == "600519.SH"
    assert shanghai.provider_symbols == {
        "tushare": "600519.SH",
        "yfinance": "600519.SS",
    }
    us = canonicalize_security(
        ticker="AAPL", exchange="NASDAQ", market="US", currency="USD"
    )
    assert us.canonical_symbol == "AAPL"
    assert us.provider_symbols == {"yfinance": "AAPL", "fmp": "AAPL"}


def test_currency_is_never_inferred_from_ticker_suffix() -> None:
    identity = canonicalize_security(
        ticker="600519.SH", exchange="SSE", market="CN-A", currency="USD"
    )
    assert identity.currency == "USD"


@pytest.mark.asyncio
async def test_yfinance_rows_are_permanently_marked_research_only(monkeypatch) -> None:
    index = pd.to_datetime(["2026-01-02", "2026-01-05"], utc=True)
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Adj Close": [100.5, 101.5],
            "Volume": [1000, 1200],
            "Dividends": [0.0, 0.0],
            "Stock Splits": [0.0, 0.0],
        },
        index=index,
    )

    class FakeTicker:
        def history(self, **kwargs):
            return frame

    class FakeYFinance:
        @staticmethod
        def Ticker(symbol):
            return FakeTicker()

    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(lambda: FakeYFinance()))
    security = MarketSecurity(
        security_id=uuid.uuid4(),
        canonical_symbol="AAPL",
        provider_symbol="AAPL",
        exchange="NASDAQ",
        market="US",
        native_currency="USD",
        asset_type="equity",
    )
    rows = await YFinanceProvider().fetch_price_bars(
        [security], start=date(2026, 1, 1), end=date(2026, 1, 6)
    )

    assert len(rows) == 2
    assert all(row.source == "yfinance_research" for row in rows)
    assert all(row.raw_payload["research_only"] is True for row in rows)
    assert all(row.native_currency == "USD" for row in rows)

