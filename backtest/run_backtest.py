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
    parser.add_argument("--train-window", type=int, default=60)
    parser.add_argument("--holding-window", type=int, default=20)
    parser.add_argument("--rebalance-frequency", type=int, default=20)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--minimum-trade-amount", type=float, default=0.0)
    parser.add_argument("--turnover-limit", type=float, default=1.0)
    parser.add_argument("--benchmark", default="", help="Optional benchmark price CSV")
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
            train_window=args.train_window,
            holding_window=args.holding_window,
            rebalance_frequency=args.rebalance_frequency,
            transaction_cost_bps=args.transaction_cost_bps,
            slippage_bps=args.slippage_bps,
            minimum_trade_amount=args.minimum_trade_amount,
            turnover_limit=args.turnover_limit,
            benchmark_price_csv=Path(args.benchmark) if args.benchmark else None,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
