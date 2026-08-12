from pathlib import Path

import pandas as pd
import pytest

from backtest.strategy_backtester import (
    BacktestConfig,
    EXECUTION_CONVENTION,
    _apply_daily_return,
    _benchmark_metrics,
    _returns_from_prices,
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
        "buy_and_hold",
        "periodic_rebalanced_original",
        "equal_weight",
        "risk_parity",
        "minimum_variance",
        "mean_variance",
    }
    assert report["execution_convention"] == EXECUTION_CONVENTION
    assert report["run_mode"] == "synthetic_smoke"
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
    assert report["run_mode"] == "historical_data"
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
    with pytest.raises(ValueError, match="deterministic optimizers own allocation"):
        run_strategy_backtest(BacktestConfig(
            portfolio_csv=portfolio, prices_csv=prices, output_path=tmp_path / "llm.json",
            run_llm_historical_adjusted=True,
        ))


def test_weight_drift_matches_manual_nav_calculation():
    portfolio_return, weights = _apply_daily_return(
        {"A": 0.8, "B": 0.2},
        pd.Series({"A": 0.10, "B": -0.10}),
    )

    assert portfolio_return == pytest.approx(0.06)
    assert weights["A"] == pytest.approx(0.88 / 1.06)
    assert weights["B"] == pytest.approx(0.18 / 1.06)


def test_rebalance_day_gap_does_not_grant_pre_execution_return(tmp_path):
    portfolio = tmp_path / "gap_portfolio.csv"
    portfolio.write_text(
        "ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country\n"
        "A,8,100,100,2024-01-01,USD,One,A,equity,US,NYSE,US\n"
        "B,2,100,100,2024-01-01,USD,Two,B,equity,US,NYSE,US\n",
        encoding="utf-8",
    )
    dates = pd.date_range("2026-01-01", periods=8, freq="B")
    prices = pd.DataFrame(
        {
            "date": dates,
            "A": [100] * 8,
            "B": [100, 100, 100, 100, 200, 200, 200, 200],
        }
    )
    price_path = tmp_path / "gap_prices.csv"
    prices.to_csv(price_path, index=False)

    report = run_strategy_backtest(
        BacktestConfig(
            portfolio_csv=portfolio,
            prices_csv=price_path,
            output_path=tmp_path / "gap.json",
            train_window=3,
            rebalance_frequency=3,
            transaction_cost_bps=0,
            slippage_bps=0,
        )
    )
    by_name = {item["strategy"]: item for item in report["strategies"]}
    first_equal_nav = by_name["equal_weight"]["nav_series"][0]["nav"]
    first_buy_hold_nav = by_name["buy_and_hold"]["nav_series"][0]["nav"]
    snapshot = report["rebalance_snapshots"][0]

    assert first_equal_nav == pytest.approx(first_buy_hold_nav)
    assert first_equal_nav == pytest.approx(1_200_000)
    assert snapshot["strategies"]["equal_weight"]["target_weights"] == {"A": 0.5, "B": 0.5}
    assert snapshot["effective_from"] > snapshot["execution_date"]
    assert report["data_leakage_checks"]["new_weights_receive_execution_day_return"] is False


def test_buy_and_hold_differs_from_periodic_constant_mix(tmp_path):
    portfolio, prices, _ = _walk_forward_files(tmp_path)
    report = run_strategy_backtest(
        BacktestConfig(
            portfolio_csv=portfolio,
            prices_csv=prices,
            output_path=tmp_path / "semantics.json",
            train_window=20,
            rebalance_frequency=10,
            transaction_cost_bps=0,
            slippage_bps=0,
        )
    )
    by_name = {item["strategy"]: item for item in report["strategies"]}

    assert by_name["buy_and_hold"]["turnover"] == 0
    assert by_name["periodic_rebalanced_original"]["turnover"] > 0
    assert by_name["buy_and_hold"]["weights"] != by_name["periodic_rebalanced_original"]["weights"]


def test_missing_price_is_not_treated_as_zero_return():
    dates = pd.date_range("2026-01-01", periods=3, freq="B")
    prices = pd.DataFrame({"A": [100.0, None, 110.0]}, index=dates)
    missing = pd.DataFrame(
        {"A": ["observed", "data_missing", "observed"]}, index=dates
    )
    closed = pd.DataFrame(
        {"A": ["observed", "exchange_closed", "observed"]}, index=dates
    )

    missing_returns = _returns_from_prices(prices, missing)
    held_returns = _returns_from_prices(prices, closed)

    assert missing_returns["A"].isna().all()
    assert held_returns.iloc[0]["A"] == pytest.approx(0.0)
    assert held_returns.iloc[1]["A"] == pytest.approx(0.1)


def test_cache_key_changes_with_prices_or_config(tmp_path):
    portfolio, prices, _ = _walk_forward_files(tmp_path)
    output = tmp_path / "cache.json"
    base = BacktestConfig(
        portfolio_csv=portfolio,
        prices_csv=prices,
        output_path=output,
        train_window=20,
    )
    first = run_strategy_backtest(base)
    replay = run_strategy_backtest(base)
    changed_config = run_strategy_backtest(
        BacktestConfig(
            portfolio_csv=portfolio,
            prices_csv=prices,
            output_path=output,
            train_window=20,
            transaction_cost_bps=25,
        )
    )
    frame = pd.read_csv(prices)
    frame.loc[len(frame) - 1, "AAPL"] *= 1.01
    frame.to_csv(prices, index=False)
    changed_prices = run_strategy_backtest(base)

    assert replay["cache_hit"] is True
    assert changed_config["cache_hit"] is False
    assert changed_config["input_hash"] != first["input_hash"]
    assert changed_prices["cache_hit"] is False
    assert changed_prices["input_hash"] != first["input_hash"]


def test_turnover_audits_target_and_costs_only_executed_trade(tmp_path):
    portfolio, prices, _ = _walk_forward_files(tmp_path)
    report = run_strategy_backtest(
        BacktestConfig(
            portfolio_csv=portfolio,
            prices_csv=prices,
            output_path=tmp_path / "turnover.json",
            train_window=20,
            rebalance_frequency=10,
            turnover_limit=0.01,
            transaction_cost_bps=100,
            slippage_bps=0,
        )
    )
    audit = report["rebalance_snapshots"][0]["strategies"]["equal_weight"]

    assert audit["turnover"] > audit["executed_turnover"]
    assert audit["executed_turnover"] == pytest.approx(0.01)
    assert audit["costs"]["rate"] == pytest.approx(0.0001)


def test_invalid_benchmark_returns_null_metrics_instead_of_zero_fill():
    index = pd.date_range("2026-01-01", periods=4, freq="B")
    portfolio = pd.Series([0.01, 0.02, -0.01, 0.01], index=index)
    benchmark = pd.Series([None, None, 0.03, None], index=index, dtype=float)

    metrics = _benchmark_metrics(portfolio, benchmark)

    assert metrics["benchmark_aligned_sample_count"] == 1
    assert metrics["benchmark_coverage"] == pytest.approx(0.25)
    assert metrics["benchmark_return"] is None
    assert metrics["beta"] is None
