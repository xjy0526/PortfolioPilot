from pathlib import Path

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
        "llm_risk_adjusted",
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
