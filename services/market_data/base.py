"""Interfaces and result types for historical adjusted-close prices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


class PriceHistoryProvider(Protocol):
    """Provider contract for adjusted-close price history."""

    source: str

    async def fetch_adjusted_close(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return a date-indexed adjusted-close frame with ticker columns."""


@dataclass(frozen=True, slots=True)
class PriceHistoryResult:
    """Normalized adjusted-close data plus data-quality metadata."""

    adjusted_close: pd.DataFrame
    source: str
    as_of: str
    start_date: str | None
    end_date: str | None
    missing_tickers: list[str]
    stale_tickers: list[str]
    coverage_ratio: float

    def data_quality(self) -> dict[str, object]:
        """Return JSON-safe metadata for risk API responses."""
        return {
            "source": self.source,
            "as_of": self.as_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "missing_tickers": list(self.missing_tickers),
            "stale_tickers": list(self.stale_tickers),
            "coverage_ratio": self.coverage_ratio,
        }
