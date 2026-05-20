"""CLI entrypoint for strategy backtesting.

Usage:
    python -m backtest.run_backtest
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import BASE_DIR, settings
from backtest.strategy_backtester import BacktestConfig, resolve_prices_csv, run_strategy_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PortfolioPilot strategy backtest")
    parser.add_argument("--portfolio", default=str(BASE_DIR / "example_portfolio.csv"))
    parser.add_argument("--prices", default="", help="Optional historical prices CSV")
    parser.add_argument("--output", default=str(settings.CACHE_DIR / "backtest_report.json"))
    parser.add_argument("--periods", type=int, default=252)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    price_candidate = args.prices or settings.BACKTEST_PRICE_CSV or None
    prices_csv = resolve_prices_csv(price_candidate) if price_candidate else resolve_prices_csv()

    report = run_strategy_backtest(
        BacktestConfig(
            portfolio_csv=Path(args.portfolio),
            prices_csv=prices_csv,
            output_path=Path(args.output),
            periods=args.periods,
            seed=args.seed,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
