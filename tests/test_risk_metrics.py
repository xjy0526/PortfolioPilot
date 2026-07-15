import math

import pandas as pd

from analytics.risk_metrics import (
    build_portfolio_risk_summary,
    calculate_annualized_volatility,
    calculate_asset_weights,
    calculate_max_drawdown,
    calculate_returns,
    calculate_sharpe_ratio,
)
from models import PortfolioPosition, StockFullData


def test_calculate_returns_sanitizes_bad_prices():
    prices = pd.DataFrame({
        "AAPL": [100, 101, 0, 103, None],
        "MSFT": [50, float("inf"), 51, 52, 53],
    })

    returns = calculate_returns(prices)

    assert not returns.empty
    assert returns.replace([float("inf"), -float("inf")], pd.NA).notna().any().any()


def test_risk_metric_functions_return_finite_values():
    returns = pd.Series([0.01, -0.02, 0.015, -0.01, 0.005])

    assert calculate_annualized_volatility(returns) > 0
    assert calculate_max_drawdown(returns) <= 0
    assert math.isfinite(calculate_sharpe_ratio(returns))


def test_build_portfolio_risk_summary_includes_exposures():
    stocks = [
        StockFullData(position=PortfolioPosition(
            ticker="AAPL", shares=10, avg_cost=100, current_price=120,
            sector="Technology", asset_type="equity", market="US",
        )),
        StockFullData(position=PortfolioPosition(
            ticker="600519.SS", shares=2, avg_cost=1500, current_price=1600,
            sector="Consumer", asset_type="cn_equity", market="CN-A",
        )),
        StockFullData(position=PortfolioPosition(
            ticker="POLY-TEST", shares=100, avg_cost=0.4, current_price=0.5,
            sector="Prediction Markets", asset_type="prediction_market", market="Polymarket",
        )),
    ]
    prices = pd.DataFrame({
        "AAPL": [100, 102, 101, 105],
        "600519.SS": [1500, 1510, 1490, 1525],
        "POLY-TEST": [0.4, 0.38, 0.43, 0.5],
    })

    summary = build_portfolio_risk_summary(stocks, prices)

    assert summary["portfolio_metrics"]["annual_volatility"] >= 0
    assert "Technology" in summary["sector_concentration"]
    assert "China A-Share" in summary["asset_type_exposure"]
    assert summary["asset_metrics"]["POLY-TEST"]["risk_level"] == "high"


def test_explicit_etf_asset_type_wins_over_cn_suffix():
    stocks = [
        StockFullData(position=PortfolioPosition(
            ticker="159995.SZ", shares=1000, avg_cost=0.9, current_price=1.05,
            sector="AI Hardware - ETF", asset_type="etf", market="CN-A",
            name="China Semiconductor ETF",
        )),
    ]

    summary = build_portfolio_risk_summary(stocks)

    assert "ETF" in summary["asset_type_exposure"]
    assert summary["asset_metrics"]["159995.SZ"]["asset_type"] == "ETF"


def test_asset_weights_empty_safe():
    assert calculate_asset_weights([]) == {}


def test_missing_history_returns_null_metrics_not_zero():
    stocks = [
        StockFullData(position=PortfolioPosition(
            ticker="AAPL", shares=10, avg_cost=100, current_price=120,
            sector="Technology", asset_type="equity", market="US",
        )),
    ]

    summary = build_portfolio_risk_summary(stocks, min_observations=20)

    metrics = summary["portfolio_metrics"]
    assert metrics["annual_volatility"] is None
    assert metrics["max_drawdown"] is None
    assert metrics["sharpe_ratio"] is None
    assert summary["metric_status"]["annual_volatility"] == "insufficient_data"
    assert summary["asset_metrics"]["AAPL"]["return"] is None
    assert summary["asset_metrics"]["AAPL"]["unrealized_return_since_cost"] == 0.2
    assert summary["data_quality"]["status"] == "unavailable"


def test_partial_history_exposes_asset_metric_status():
    stocks = [
        StockFullData(position=PortfolioPosition(ticker="AAPL", shares=1, current_price=120)),
        StockFullData(position=PortfolioPosition(ticker="MSFT", shares=1, current_price=220)),
    ]
    dates = pd.date_range("2026-06-01", periods=25, freq="B")
    prices = pd.DataFrame({"AAPL": range(100, 125)}, index=dates)

    summary = build_portfolio_risk_summary(
        stocks,
        prices,
        min_observations=20,
        market_data_quality={
            "source": "test",
            "missing_tickers": ["MSFT"],
            "stale_tickers": [],
            "coverage_ratio": 0.5,
        },
    )

    assert summary["portfolio_metrics"]["annual_volatility"] is not None
    assert summary["asset_metrics"]["AAPL"]["metric_status"]["annual_volatility"] == "valid"
    assert summary["asset_metrics"]["MSFT"]["annual_volatility"] is None
    assert summary["asset_metrics"]["MSFT"]["metric_status"]["annual_volatility"] == "insufficient_data"
    assert summary["data_quality"]["status"] == "partial"
    assert summary["data_quality"]["coverage_ratio"] == 0.5


def test_stale_history_marks_metric_status_without_discarding_value():
    stock = StockFullData(position=PortfolioPosition(ticker="AAPL", shares=1, current_price=120))
    dates = pd.date_range("2026-05-01", periods=25, freq="B")
    prices = pd.DataFrame({"AAPL": range(100, 125)}, index=dates)

    summary = build_portfolio_risk_summary(
        [stock],
        prices,
        min_observations=20,
        market_data_quality={"missing_tickers": [], "stale_tickers": ["AAPL"], "coverage_ratio": 1.0},
    )

    assert summary["asset_metrics"]["AAPL"]["annual_volatility"] is not None
    assert summary["asset_metrics"]["AAPL"]["metric_status"]["annual_volatility"] == "stale"
    assert summary["metric_status"]["sharpe_ratio"] == "stale"
    assert summary["data_quality"]["status"] == "stale"
