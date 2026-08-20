"""CSV adjusted-close price provider."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class CsvPriceHistoryProvider:
    """Read adjusted-close history from a wide or long CSV file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.source = f"csv:{self.path}"

    async def fetch_adjusted_close(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        frame = pd.read_csv(self.path)
        prices = _parse_csv(frame)
        if prices.empty:
            return prices
        index = pd.to_datetime(prices.index, errors="coerce")
        prices.index = index
        return prices.loc[(prices.index.date >= start_date) & (prices.index.date <= end_date)]


def _parse_csv(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data.columns = [str(column).strip() for column in data.columns]
    columns = {column.lower().replace(" ", "_"): column for column in data.columns}
    value_key = next(
        (key for key in ("adjusted_close", "adj_close", "adjclose", "close") if key in columns),
        None,
    )
    if "date" in columns and "ticker" in columns and value_key:
        slim = data[[columns["date"], columns["ticker"], columns[value_key]]].copy()
        slim.columns = ["date", "ticker", "adjusted_close"]
        slim["date"] = pd.to_datetime(slim["date"], errors="coerce")
        slim["ticker"] = slim["ticker"].astype(str).str.strip().str.upper()
        slim["adjusted_close"] = pd.to_numeric(slim["adjusted_close"], errors="coerce")
        slim = slim.dropna(subset=["date", "ticker", "adjusted_close"])
        return slim.pivot_table(index="date", columns="ticker", values="adjusted_close", aggfunc="last")

    if "date" in columns:
        data = data.set_index(columns["date"])
    data.index = pd.to_datetime(data.index, errors="coerce")
    data.columns = [str(column).strip().upper() for column in data.columns]
    return data[~data.index.isna()]
