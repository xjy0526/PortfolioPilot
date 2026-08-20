"""Provider-neutral historical adjusted-close service."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import BASE_DIR, settings
from services.market_data.base import PriceHistoryProvider, PriceHistoryResult

logger = logging.getLogger(__name__)


class PriceHistoryService:
    """Load and normalize adjusted-close history with quality metadata."""

    def __init__(self, provider: PriceHistoryProvider, stale_after_days: int = 5):
        self.provider = provider
        self.stale_after_days = max(0, int(stale_after_days))

    async def get_history(
        self,
        tickers: list[str],
        *,
        lookback_days: int = 365,
        as_of: datetime | None = None,
    ) -> PriceHistoryResult:
        requested = _normalize_tickers(tickers)
        requested_as_of = as_of or datetime.now(timezone.utc)
        if requested_as_of.tzinfo is None:
            requested_as_of = requested_as_of.replace(tzinfo=timezone.utc)
        requested_as_of = requested_as_of.astimezone(timezone.utc)
        end_date = requested_as_of.date()
        start_date = end_date - timedelta(days=max(1, int(lookback_days)))

        if not requested:
            return _empty_result(self.provider.source, requested_as_of, [])

        try:
            raw = await self.provider.fetch_adjusted_close(requested, start_date, end_date)
        except Exception as exc:
            logger.warning("Price history provider %s failed: %s", self.provider.source, exc)
            raw = pd.DataFrame()

        prices = _normalize_price_frame(raw, requested)
        present = [ticker for ticker in requested if ticker in prices and prices[ticker].notna().any()]
        missing = [ticker for ticker in requested if ticker not in present]
        stale_cutoff = end_date - timedelta(days=self.stale_after_days)
        stale = []
        for ticker in present:
            last_valid = prices[ticker].last_valid_index()
            if last_valid is None or pd.Timestamp(last_valid).date() < stale_cutoff:
                stale.append(ticker)

        populated = prices[present].dropna(axis=0, how="all") if present else pd.DataFrame()
        actual_start, actual_end = _date_bounds(populated)
        return PriceHistoryResult(
            adjusted_close=populated,
            source=self.provider.source,
            as_of=requested_as_of.isoformat(),
            start_date=actual_start,
            end_date=actual_end,
            missing_tickers=missing,
            stale_tickers=stale,
            coverage_ratio=round(len(present) / len(requested), 6),
        )


def get_price_history_service() -> PriceHistoryService:
    """Build the configured production price-history service."""
    provider_name = str(settings.PRICE_HISTORY_PROVIDER or "yfinance").lower()
    if provider_name == "csv":
        from services.market_data.csv_provider import CsvPriceHistoryProvider

        raw_path = str(settings.PRICE_HISTORY_CSV or "").strip()
        path = Path(raw_path).expanduser() if raw_path else BASE_DIR / "data" / "prices" / "example_historical_prices.csv"
        if not path.is_absolute():
            path = BASE_DIR / path
        provider: PriceHistoryProvider = CsvPriceHistoryProvider(path)
    else:
        from services.market_data.yfinance_provider import YFinancePriceHistoryProvider

        provider = YFinancePriceHistoryProvider()
    return PriceHistoryService(provider, stale_after_days=settings.PRICE_HISTORY_STALE_AFTER_DAYS)


def _normalize_tickers(tickers: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = str(raw or "").strip().upper()
        if not ticker or ticker == "CASH" or ticker in seen:
            continue
        normalized.append(ticker)
        seen.add(ticker)
    return normalized


def _normalize_price_frame(frame: pd.DataFrame | None, requested: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = pd.DataFrame(frame).copy()
    result.columns = [str(column).strip().upper() for column in result.columns]
    result = result[[ticker for ticker in requested if ticker in result.columns]]
    result = result.apply(pd.to_numeric, errors="coerce")
    result = result.replace([np.inf, -np.inf], np.nan).where(lambda values: values > 0)
    result.index = pd.to_datetime(result.index, errors="coerce", utc=True).tz_convert(None)
    result = result[~result.index.isna()]
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result.dropna(axis=0, how="all").dropna(axis=1, how="all")


def _date_bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if frame.empty:
        return None, None
    return frame.index.min().date().isoformat(), frame.index.max().date().isoformat()


def _empty_result(source: str, as_of: datetime, missing: list[str]) -> PriceHistoryResult:
    return PriceHistoryResult(
        adjusted_close=pd.DataFrame(),
        source=source,
        as_of=as_of.isoformat(),
        start_date=None,
        end_date=None,
        missing_tickers=missing,
        stale_tickers=[],
        coverage_ratio=0.0,
    )
