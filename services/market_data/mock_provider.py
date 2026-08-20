"""Deterministic in-memory price provider for tests only."""
from __future__ import annotations

from datetime import date

import pandas as pd


class MockPriceHistoryProvider:
    source = "mock_test_only"

    def __init__(self, adjusted_close: pd.DataFrame | None = None, error: Exception | None = None):
        self.adjusted_close = pd.DataFrame() if adjusted_close is None else adjusted_close.copy()
        self.error = error

    async def fetch_adjusted_close(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        if self.error:
            raise self.error
        return self.adjusted_close.copy()
