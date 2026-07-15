from pathlib import Path

import pandas as pd
import pytest

from backtest.strategy_backtester import (
    BacktestConfig,
    generate_mock_price_data,
    run_strategy_backtest,
)


def test_generate_mock_price_data_reproducible():
    a = generate_mock_price_data(["AAPL", "MSFT"], periods=10, seed=7)
    b = generate_mock_price_data(["AAPL", "MSFT"], periods=10, seed=7)

    assert a.equals(b)
    assert list(a.columns) == ["AAPL", "MSFT"]


def test_run_strategy_backtest_writes_report(tmp_path):
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country\n"
        "AAPL,10,100,120,2024-01-01,USD,Technology,Apple,equity,US,NASDAQ,US\n"
        "POLY-TEST,100,0.4,0.5,2024-01-01,USD,Prediction Markets,Test,prediction_market,Polymarket,Polymarket,WEB3\n",
        encoding="utf-8",
    )
    output = tmp_path / "backtest_report.json"

    report = run_strategy_backtest(BacktestConfig(
        portfolio_csv=portfolio,
        prices_csv=None,
        output_path=output,
        periods=60,
        seed=3,
    ))

    assert output.exists()
    assert report["mock_price_data_used"] is True
    assert report["data_source"] == "mock_price_data"
    assert report["start_date"] is not None
    assert report["end_date"] is not None
    assert report["asset_count"] == 2
    assert {row["strategy"] for row in report["strategies"]} == {
        "original_portfolio",
        "equal_weight",
        "risk_parity",
        "minimum_variance",
        "mean_variance",
        "rule_risk_adjusted",
    }
    assert report["minimum_variance_explanations"]
    assert report["mean_variance_explanations"]


def test_run_strategy_backtest_uses_real_historical_csv(tmp_path):
    portfolio = tmp_path / "portfolio.csv"
    portfolio.write_text(
        "ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country\n"
        "AAPL,10,100,120,2024-01-01,USD,Technology,Apple,equity,US,NASDAQ,US\n"
        "MSFT,5,200,220,2024-01-01,USD,Technology,Microsoft,equity,US,NASDAQ,US\n",
        encoding="utf-8",
    )
    prices = tmp_path / "prices.csv"
    prices.write_text(
        "date,ticker,close\n"
        "2026-01-01,AAPL,100\n"
        "2026-01-02,AAPL,101\n"
        "2026-01-05,AAPL,103\n"
        "2026-01-01,MSFT,200\n"
        "2026-01-02,MSFT,198\n"
        "2026-01-05,MSFT,202\n",
        encoding="utf-8",
    )
    output = tmp_path / "backtest_report.json"

    report = run_strategy_backtest(BacktestConfig(
        portfolio_csv=portfolio,
        prices_csv=prices,
        output_path=output,
    ))

    assert report["mock_price_data_used"] is False
    assert report["data_source"] == "historical_csv"
    assert report["prices_csv"] == str(prices)
    assert report["start_date"] == "2026-01-01"
    assert report["end_date"] == "2026-01-05"
    assert report["asset_count"] == 2
    assert report["missing_price_assets"] == []


def _walk_forward_files(tmp_path):
    portfolio = tmp_path / "wf_portfolio.csv"
    portfolio.write_text(
        "ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country\n"
        "AAPL,80,100,100,2024-01-01,USD,Technology,Apple,equity,US,NASDAQ,US\n"
        "MSFT,10,200,200,2024-01-01,USD,Technology,Microsoft,equity,US,NASDAQ,US\n",
        encoding="utf-8",
    )
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    prices = pd.DataFrame({
        "date": dates,
        "AAPL": [100 * (1.001 ** index) for index in range(len(dates))],
        "MSFT": [200 * (1.0003 ** index) for index in range(len(dates))],
    })
    prices_path = tmp_path / "wf_prices.csv"
    prices.to_csv(prices_path, index=False)
    benchmark = pd.DataFrame({"date": dates, "BENCH": [100 * (1.0005 ** index) for index in range(len(dates))]})
    benchmark_path = tmp_path / "benchmark.csv"
    benchmark.to_csv(benchmark_path, index=False)
    return portfolio, prices_path, benchmark_path


def test_walk_forward_uses_only_pre_rebalance_data_and_saves_snapshots(tmp_path):
    portfolio, prices, benchmark = _walk_forward_files(tmp_path)
    report = run_strategy_backtest(BacktestConfig(
        portfolio_csv=portfolio, prices_csv=prices, output_path=tmp_path / "wf.json",
        train_window=40, holding_window=20, rebalance_frequency=20,
        benchmark_price_csv=benchmark,
    ))

    assert report["out_of_sample"] is True
    assert report["rebalance_snapshots"]
    assert all(item["train_end"] < item["rebalance_date"] for item in report["rebalance_snapshots"])
    assert report["data_leakage_checks"]["status"] == "passed"
    assert report["data_leakage_checks"]["future_prices_used_for_estimation"] is False
    strategy = next(item for item in report["strategies"] if item["strategy"] == "equal_weight")
    assert strategy["benchmark_return"] is not None
    assert strategy["tracking_error"] is not None
    assert strategy["information_ratio"] is not None
    assert "downside_volatility" in strategy
    assert "historical_cvar" in strategy
    assert set(strategy["stress_test"]) == {
        "equity_market_shock", "technology_sector_shock", "interest_rate_shock", "currency_shock",
    }


def test_transaction_cost_is_deducted_from_out_of_sample_return(tmp_path):
    portfolio, prices, _ = _walk_forward_files(tmp_path)
    no_cost = run_strategy_backtest(BacktestConfig(
        portfolio_csv=portfolio, prices_csv=prices, output_path=tmp_path / "no_cost.json",
        train_window=40, transaction_cost_bps=0, slippage_bps=0,
    ))
    costly = run_strategy_backtest(BacktestConfig(
        portfolio_csv=portfolio, prices_csv=prices, output_path=tmp_path / "costly.json",
        train_window=40, transaction_cost_bps=100, slippage_bps=50,
    ))
    first = next(item for item in no_cost["strategies"] if item["strategy"] == "equal_weight")
    second = next(item for item in costly["strategies"] if item["strategy"] == "equal_weight")
    assert second["annual_return"] < first["annual_return"]


def test_true_llm_strategy_requires_historical_snapshots(tmp_path):
    portfolio, prices, _ = _walk_forward_files(tmp_path)
    with pytest.raises(ValueError, match="requires historical prompt/evidence/model snapshots"):
        run_strategy_backtest(BacktestConfig(
            portfolio_csv=portfolio, prices_csv=prices, output_path=tmp_path / "llm.json",
            run_llm_historical_adjusted=True,
        ))
