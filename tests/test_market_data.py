from datetime import datetime, timezone

import pandas as pd
import pytest

from services.market_data.mock_provider import MockPriceHistoryProvider
from services.market_data.csv_provider import CsvPriceHistoryProvider
from services.market_data.price_history_service import PriceHistoryService


AS_OF = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_complete_historical_prices_have_full_coverage():
    dates = pd.date_range("2026-06-01", "2026-07-15", freq="B")
    provider = MockPriceHistoryProvider(pd.DataFrame({
        "AAPL": range(100, 100 + len(dates)),
        "MSFT": range(200, 200 + len(dates)),
    }, index=dates))

    result = await PriceHistoryService(provider, stale_after_days=5).get_history(
        ["AAPL", "MSFT"], as_of=AS_OF,
    )

    assert list(result.adjusted_close.columns) == ["AAPL", "MSFT"]
    assert result.coverage_ratio == 1.0
    assert result.missing_tickers == []
    assert result.stale_tickers == []
    assert result.source == "mock_test_only"
    assert result.end_date == "2026-07-15"


@pytest.mark.asyncio
async def test_partial_ticker_history_reports_missing_ticker():
    dates = pd.date_range("2026-06-01", "2026-07-15", freq="B")
    provider = MockPriceHistoryProvider(pd.DataFrame({"AAPL": range(len(dates))}, index=dates) + 100)

    result = await PriceHistoryService(provider).get_history(["AAPL", "MSFT"], as_of=AS_OF)

    assert result.coverage_ratio == 0.5
    assert result.missing_tickers == ["MSFT"]
    assert list(result.adjusted_close.columns) == ["AAPL"]


@pytest.mark.asyncio
async def test_all_history_missing_returns_empty_quality_result():
    result = await PriceHistoryService(MockPriceHistoryProvider()).get_history(
        ["AAPL", "MSFT"], as_of=AS_OF,
    )

    assert result.adjusted_close.empty
    assert result.coverage_ratio == 0.0
    assert result.missing_tickers == ["AAPL", "MSFT"]
    assert result.start_date is None
    assert result.end_date is None


@pytest.mark.asyncio
async def test_stale_price_is_reported():
    dates = pd.date_range("2026-05-01", "2026-06-30", freq="B")
    provider = MockPriceHistoryProvider(pd.DataFrame({"AAPL": range(len(dates))}, index=dates) + 100)

    result = await PriceHistoryService(provider, stale_after_days=5).get_history(
        ["AAPL"], as_of=AS_OF,
    )

    assert result.missing_tickers == []
    assert result.stale_tickers == ["AAPL"]
    assert result.coverage_ratio == 1.0


@pytest.mark.asyncio
async def test_csv_provider_reads_long_adjusted_close_format(tmp_path):
    csv_path = tmp_path / "prices.csv"
    csv_path.write_text(
        "date,ticker,adjusted_close\n"
        "2026-07-13,AAPL,100\n"
        "2026-07-14,AAPL,101\n"
        "2026-07-15,AAPL,102\n",
        encoding="utf-8",
    )
    service = PriceHistoryService(CsvPriceHistoryProvider(csv_path), stale_after_days=5)

    result = await service.get_history(["AAPL"], as_of=AS_OF)

    assert result.adjusted_close.loc[pd.Timestamp("2026-07-15"), "AAPL"] == 102
    assert result.source.startswith("csv:")
    assert result.coverage_ratio == 1.0
