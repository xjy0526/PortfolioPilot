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
