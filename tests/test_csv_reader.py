"""Tests for fetchers/csv_reader.py — CSV Portfolio Import."""

import os
import tempfile
from pathlib import Path
import pytest
from fetchers.csv_reader import (
    parse_csv_file,
    parse_csv_json,
    csv_positions_to_portfolio_format,
    delete_csv_position,
    load_saved_csv_positions,
    resolve_csv_read_path,
    save_csv_positions,
    saved_csv_portfolio_exists,
    upsert_csv_position,
    _parse_date,
    _normalize_rows,
)


class TestParseCsvJson:
    """Test JSON-based CSV parsing (from frontend upload)."""

    def test_basic_positions(self):
        data = [
            {"ticker": "AAPL", "shares": "10", "buy_price": "150.00"},
            {"ticker": "MSFT", "shares": "5", "buy_price": "280.00"},
        ]
        result = parse_csv_json(data)
        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["shares"] == 10.0
        assert result[0]["buy_price"] == 150.0
        assert result[0]["source"] == "csv"

    def test_ticker_normalized_to_upper(self):
        data = [{"ticker": "aapl", "shares": "10", "buy_price": "150"}]
        result = parse_csv_json(data)
        assert result[0]["ticker"] == "AAPL"

    def test_skips_empty_ticker(self):
        data = [
            {"ticker": "", "shares": "10", "buy_price": "100"},
            {"ticker": "AAPL", "shares": "10", "buy_price": "150"},
        ]
        result = parse_csv_json(data)
        assert len(result) == 1

    def test_skips_cash_rows(self):
        data = [
            {"ticker": "CASH", "shares": "1", "buy_price": "5000"},
            {"ticker": "AAPL", "shares": "10", "buy_price": "150"},
        ]
        result = parse_csv_json(data)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_skips_zero_shares(self):
        data = [{"ticker": "AAPL", "shares": "0", "buy_price": "150"}]
        result = parse_csv_json(data)
        assert len(result) == 0

    def test_skips_invalid_numeric(self):
        data = [{"ticker": "AAPL", "shares": "abc", "buy_price": "150"}]
        result = parse_csv_json(data)
        assert len(result) == 0

    def test_default_currency(self):
        data = [{"ticker": "AAPL", "shares": "10", "buy_price": "150"}]
        result = parse_csv_json(data)
        assert result[0]["currency"] == "USD"

    def test_valid_currencies(self):
        for curr in ["USD", "EUR", "GBP", "CHF", "CNY"]:
            data = [{"ticker": "AAPL", "shares": "10", "buy_price": "150", "currency": curr}]
            result = parse_csv_json(data)
            assert result[0]["currency"] == curr

    def test_invalid_currency_defaults_to_usd(self):
        data = [{"ticker": "AAPL", "shares": "10", "buy_price": "150", "currency": "XYZ"}]
        result = parse_csv_json(data)
        assert result[0]["currency"] == "USD"

    def test_sample_portfolios_parse_with_current_prices(self):
        root = Path(__file__).resolve().parent.parent / "data" / "portfolios"
        sample_files = sorted(root.glob("test_*.csv"))

        assert len(sample_files) >= 5
        for path in sample_files:
            result = parse_csv_file(str(path))
            assert result, f"{path.name} should contain valid positions"
            assert all(item["current_price"] is not None for item in result), path.name

    def test_optional_fields(self):
        data = [{
            "ticker": "AAPL",
            "shares": "10",
            "buy_price": "150",
            "current_price": "175",
            "buy_date": "2024-03-15",
            "sector": "Technology",
            "name": "Apple Inc.",
            "asset_type": "equity",
            "market": "US",
        }]
        result = parse_csv_json(data)
        assert result[0]["buy_date"] == "2024-03-15"
        assert result[0]["sector"] == "Technology"
        assert result[0]["name"] == "Apple Inc."
        assert result[0]["current_price"] == 175.0
        assert result[0]["asset_type"] == "equity"
        assert result[0]["market"] == "US"

    def test_detects_china_a_share(self):
        data = [{"ticker": "600519.SS", "shares": "2", "buy_price": "1600"}]
        result = parse_csv_json(data)
        assert result[0]["asset_type"] == "cn_equity"
        assert result[0]["currency"] == "CNY"
        assert result[0]["market"] == "CN-A"

    def test_polymarket_position(self):
        data = [{
            "ticker": "POLY-BTC-150K-2026",
            "shares": "50",
            "buy_price": "0.31",
            "current_price": "0.36",
            "asset_type": "prediction_market",
            "market": "Polymarket",
        }]
        result = parse_csv_json(data)
        assert result[0]["asset_type"] == "prediction_market"
        assert result[0]["market"] == "Polymarket"
        assert result[0]["current_price"] == 0.36

    def test_name_defaults_to_ticker(self):
        data = [{"ticker": "AAPL", "shares": "10", "buy_price": "150"}]
        result = parse_csv_json(data)
        assert result[0]["name"] == "AAPL"

    def test_empty_list(self):
        result = parse_csv_json([])
        assert result == []


class TestParseCsvFile:
    """Test file-based CSV parsing."""

    def test_valid_csv_file(self):
        content = "ticker,shares,buy_price\nAAPL,10,150\nMSFT,5,280\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(content)
            f.flush()
            result = parse_csv_file(f.name)
        os.unlink(f.name)
        assert len(result) == 2

    def test_nonexistent_file(self):
        result = parse_csv_file("/nonexistent/path.csv")
        assert result == []

    def test_csv_with_bom(self):
        content = "ticker,shares,buy_price\nAAPL,10,150\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig') as f:
            f.write(content)
            f.flush()
            result = parse_csv_file(f.name)
        os.unlink(f.name)
        assert len(result) == 1

    def test_save_csv_positions_roundtrip(self, tmp_path):
        path = tmp_path / "portfolio.csv"
        positions = parse_csv_json([{
            "ticker": "aapl",
            "shares": "10",
            "buy_price": "150",
            "current_price": "175",
            "buy_date": "2024-03-15",
            "currency": "USD",
            "sector": "Technology",
            "name": "Apple Inc.",
            "asset_type": "equity",
            "market": "US",
            "exchange": "NASDAQ",
            "country": "US",
        }])

        saved = save_csv_positions(positions, path)
        result = parse_csv_file(str(saved))

        assert saved_csv_portfolio_exists(path)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["shares"] == 10.0
        assert result[0]["current_price"] == 175.0
        assert result[0]["name"] == "Apple Inc."

    def test_upsert_csv_position_creates_and_updates(self, tmp_path):
        path = tmp_path / "portfolio.csv"

        positions, saved, replaced = upsert_csv_position({
            "ticker": "AAPL",
            "shares": "10",
            "buy_price": "150",
        }, path)
        assert replaced is False
        assert saved["ticker"] == "AAPL"
        assert len(positions) == 1

        positions, saved, replaced = upsert_csv_position({
            "ticker": "AAPL",
            "shares": "12",
            "buy_price": "140",
        }, path)
        assert replaced is True
        assert saved["shares"] == 12.0
        assert len(positions) == 1

        result = parse_csv_file(str(path))
        assert result[0]["shares"] == 12.0
        assert result[0]["buy_price"] == 140.0

    def test_upsert_csv_position_can_rename_ticker(self, tmp_path):
        path = tmp_path / "portfolio.csv"
        upsert_csv_position({"ticker": "AAPL", "shares": "10", "buy_price": "150"}, path)

        positions, saved, replaced = upsert_csv_position({
            "ticker": "MSFT",
            "shares": "5",
            "buy_price": "280",
        }, path, original_ticker="AAPL")

        assert replaced is True
        assert saved["ticker"] == "MSFT"
        assert [p["ticker"] for p in positions] == ["MSFT"]

    def test_delete_csv_position(self, tmp_path):
        path = tmp_path / "portfolio.csv"
        upsert_csv_position({"ticker": "AAPL", "shares": "10", "buy_price": "150"}, path)
        upsert_csv_position({"ticker": "MSFT", "shares": "5", "buy_price": "280"}, path)

        positions, deleted = delete_csv_position("AAPL", path)

        assert deleted is True
        assert [p["ticker"] for p in positions] == ["MSFT"]
        assert [p["ticker"] for p in parse_csv_file(str(path))] == ["MSFT"]

    def test_demo_mode_reads_bundled_sample_when_local_csv_missing(self, tmp_path, monkeypatch):
        from config import settings

        monkeypatch.setattr(settings, "PARQET_PORTFOLIO_CSV", str(tmp_path / "missing.csv"))
        monkeypatch.setattr(settings, "FMP_API_KEY", "")

        path, sample_fallback = resolve_csv_read_path()
        positions = load_saved_csv_positions()

        assert sample_fallback is True
        assert path.name == "ai_hardware_portfolio.csv"
        assert len(positions) >= 5
        assert any(pos["ticker"] == "NVDA" for pos in positions)


class TestParseDate:
    """Test date parsing helper."""

    def test_iso_format(self):
        assert _parse_date("2024-03-15") == "2024-03-15"

    def test_german_format(self):
        assert _parse_date("15.03.2024") == "2024-03-15"

    def test_us_format(self):
        assert _parse_date("03/15/2024") == "2024-03-15"

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_none(self):
        assert _parse_date(None) is None

    def test_invalid_date(self):
        assert _parse_date("not-a-date") is None


class TestCsvToPortfolioFormat:
    """Test conversion to internal portfolio format."""

    def test_basic_conversion(self):
        positions = [{
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "shares": 10,
            "buy_price": 150.0,
            "currency": "USD",
            "sector": "Technology",
            "buy_date": "2024-01-01",
        }]
        result = csv_positions_to_portfolio_format(positions)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["shares"] == 10
        assert result[0]["currentPrice"] == 150.0  # No live prices, falls back to buy_price
        assert result[0]["totalValue"] == 1500.0
        assert result[0]["source"] == "csv"

    def test_with_live_prices(self):
        positions = [{"ticker": "AAPL", "name": "Apple", "shares": 10, "buy_price": 150.0, "currency": "USD"}]
        prices = {"AAPL": 200.0}
        result = csv_positions_to_portfolio_format(positions, prices)
        assert result[0]["currentPrice"] == 200.0
        assert result[0]["totalValue"] == 2000.0
        assert result[0]["pnl"] == 500.0

    def test_uses_manual_current_price_for_polymarket(self):
        positions = [{
            "ticker": "POLY-BTC-150K-2026",
            "name": "BTC > 150k",
            "shares": 10,
            "buy_price": 0.30,
            "current_price": 0.42,
            "currency": "USD",
            "asset_type": "prediction_market",
            "market": "Polymarket",
        }]
        result = csv_positions_to_portfolio_format(positions, prices={})
        assert result[0]["currentPrice"] == 0.42
        assert result[0]["asset_type"] == "prediction_market"

    def test_pnl_calculation(self):
        positions = [{"ticker": "AAPL", "name": "Apple", "shares": 10, "buy_price": 100.0, "currency": "USD"}]
        prices = {"AAPL": 120.0}
        result = csv_positions_to_portfolio_format(positions, prices)
        assert result[0]["pnl"] == 200.0  # (120-100) * 10
        assert abs(result[0]["pnlPercent"] - 20.0) < 0.01
