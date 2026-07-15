"""Yahoo Finance adjusted-close provider."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pandas as pd


class YFinancePriceHistoryProvider:
    source = "yfinance_adjusted_close"

    async def fetch_adjusted_close(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        return await asyncio.to_thread(self._download, tickers, start_date, end_date)

    @staticmethod
    def _download(tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        import yfinance as yf

        raw = yf.download(
            tickers=tickers,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            # With auto_adjust=True, yfinance's Close field is adjusted for
            # splits and distributions and is the canonical adjusted-close series.
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
            timeout=15,
        )
        if raw is None or raw.empty:
            return pd.DataFrame()

        if isinstance(raw.columns, pd.MultiIndex):
            first_level = raw.columns.get_level_values(0)
            price_field = "Close"
            prices = raw[price_field].copy()
            if isinstance(prices, pd.Series):
                prices = prices.to_frame(name=tickers[0])
        else:
            price_field = "Close"
            prices = raw[[price_field]].rename(columns={price_field: tickers[0]})

        prices.columns = [str(column).strip().upper() for column in prices.columns]
        return prices
