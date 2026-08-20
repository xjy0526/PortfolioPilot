import pytest

from fetchers.csv_reader import csv_positions_to_portfolio_format, parse_csv_json
from services.currency_converter import CurrencyConverter, ExchangeRates
from services.portfolio_builder import build_portfolio_from_csv
from state import portfolio_data


@pytest.mark.asyncio
async def test_build_portfolio_from_csv_creates_summary(monkeypatch):
    async def fake_create(eur_usd_override=None):
        return CurrencyConverter(ExchangeRates(eur_usd=1.0, eur_cny=1.0))

    monkeypatch.setattr(CurrencyConverter, "create", fake_create)
    portfolio_data.clear()

    rows = parse_csv_json([
        {
            "ticker": "NVDA",
            "shares": "2",
            "buy_price": "100",
            "current_price": "125",
            "currency": "USD",
            "sector": "Technology",
            "name": "NVIDIA",
        }
    ])
    portfolio_positions = csv_positions_to_portfolio_format(rows)

    result = await build_portfolio_from_csv(portfolio_positions)

    assert result["num_positions"] == 1
    assert portfolio_data["summary"].stocks[0].position.ticker == "NVDA"
    assert portfolio_data["summary"].total_value == 250.0
