"""Point-in-time strategy backtesting with explicit execution and data quality."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analytics.risk_metrics import (
    calculate_annual_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
)
from config import settings
from fetchers.csv_reader import csv_positions_to_portfolio_format, parse_csv_file
from portfolio_optimizer import (
    equal_weight_baseline,
    mean_variance_portfolio,
    minimum_variance_portfolio,
    risk_parity_simple,
)

EXECUTION_CONVENTION = (
    "Weights are estimated after t-1 close using data available through t-1, "
    "orders execute at t close, and executed weights first receive the t+1 close-to-close return."
)
STRATEGY_NAMES = (
    "buy_and_hold",
    "periodic_rebalanced_original",
    "equal_weight",
    "risk_parity",
    "minimum_variance",
    "mean_variance",
)
LEGAL_HOLD_STATUSES = {"exchange_closed", "suspended"}
VALID_PRICE_STATUSES = {"observed", *LEGAL_HOLD_STATUSES}
TRADABLE_PRICE_STATUS = "observed"
DEFAULT_PRICE_CSV = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "prices"
    / "example_historical_prices.csv"
)


@dataclass
class BacktestConfig:
    portfolio_csv: Path
    prices_csv: Path | None
    output_path: Path
    seed: int = 42
    periods: int = 252
    train_window: int = 60
    holding_window: int = 20  # Deprecated compatibility field; returns are continuous.
    rebalance_frequency: int = 20
    transaction_cost_bps: float = 5.0
    slippage_bps: float = 2.0
    minimum_trade_amount: float = 0.0
    turnover_limit: float = 1.0
    initial_capital: float = 1_000_000.0
    benchmark_price_csv: Path | None = None
    llm_historical_snapshots: dict[str, dict[str, Any]] | None = None
    run_llm_historical_adjusted: bool = False
    stress_scenarios: dict[str, float] | None = None
    benchmark_sector_weights: dict[str, float] | None = None
    benchmark_constituent_weights: dict[str, float] | None = None
    minimum_asset_coverage: float = 0.80
    minimum_covariance_observations: int = 20
    portfolio_snapshot_id: str | None = None
    code_version: str = ""
    use_cache: bool = True


@dataclass
class PriceDataBundle:
    prices: pd.DataFrame
    availability: pd.DataFrame
    mock_used: bool
    data_source: str
    source_path: str | None = None


@dataclass
class _StrategyState:
    weights: dict[str, float]
    nav: float
    turnover: float = 0.0
    executed_turnover: float = 0.0
    costs: float = 0.0


def run_strategy_backtest(config: BacktestConfig) -> dict[str, Any]:
    """Run the six deterministic strategies with close-to-close execution integrity."""
    if config.run_llm_historical_adjusted:
        raise ValueError(
            "LLM-adjusted target weights are disabled; deterministic optimizers own allocation"
        )
    raw_positions = parse_csv_file(str(config.portfolio_csv))
    if not raw_positions:
        raise ValueError(f"No valid positions found in {config.portfolio_csv}")
    portfolio_positions = csv_positions_to_portfolio_format(raw_positions, prices={})
    tickers = list(dict.fromkeys(str(row["ticker"]).upper() for row in portfolio_positions))
    initial_weights = _weights_from_portfolio_rows(portfolio_positions)
    price_bundle = load_or_mock_prices(
        tickers,
        config.prices_csv,
        periods=config.periods,
        seed=config.seed,
    )
    prices = price_bundle.prices.reindex(columns=tickers)
    availability = price_bundle.availability.reindex(
        index=prices.index, columns=tickers, fill_value="data_missing"
    )
    returns = _returns_from_prices(prices, availability)
    if returns.empty:
        raise ValueError("At least two dated price observations are required")

    effective_train = max(1, min(config.train_window, max(1, len(returns) // 2)))
    frequency = max(1, config.rebalance_frequency)
    out_of_sample_index = returns.index[effective_train:]
    if out_of_sample_index.empty:
        raise ValueError("No out-of-sample dates remain after the training window")

    config_hash = _config_hash(config)
    portfolio_hash = _file_hash(config.portfolio_csv)
    price_data_hash = _price_data_hash(price_bundle)
    code_version = config.code_version or settings.CODE_VERSION
    cache_key = _hash_payload(
        {
            "portfolio_snapshot": config.portfolio_snapshot_id or portfolio_hash,
            "price_data_hash": price_data_hash,
            "config_hash": config_hash,
            "code_version": code_version,
        }
    )
    cached = _load_cached_report(config.output_path, cache_key) if config.use_cache else None
    if cached is not None:
        cached["cache_hit"] = True
        return cached

    sector_map = _sector_map_from_rows(portfolio_positions)
    states = {
        name: _StrategyState(dict(initial_weights), float(config.initial_capital))
        for name in STRATEGY_NAMES
    }
    strategy_returns = {
        name: pd.Series(np.nan, index=out_of_sample_index, dtype=float)
        for name in STRATEGY_NAMES
    }
    nav_history: dict[str, list[dict[str, Any]]] = {name: [] for name in STRATEGY_NAMES}
    snapshots: list[dict[str, Any]] = []
    pending_post_return: dict[str, list[dict[str, Any]]] = {
        name: [] for name in STRATEGY_NAMES
    }
    latest_explanations: dict[str, list[dict[str, Any]]] = {
        "minimum_variance": [],
        "mean_variance": [],
    }
    rebalance_positions = set(range(effective_train, len(returns), frequency))

    for position in range(effective_train, len(returns)):
        current_date = returns.index[position]
        day_returns = returns.iloc[position]
        daily_pre_trade: dict[str, dict[str, float]] = {}
        daily_gross: dict[str, float | None] = {}

        for name, state in states.items():
            gross_return, drifted = _apply_daily_return(state.weights, day_returns)
            daily_pre_trade[name] = drifted
            daily_gross[name] = gross_return
            if gross_return is not None:
                state.nav *= 1.0 + gross_return
                state.weights = drifted
            if gross_return is not None and pending_post_return[name]:
                for previous in pending_post_return[name]:
                    previous["post_return_weights"] = _rounded_weights(state.weights)
                    previous["post_return_date"] = _date_string(current_date)
                pending_post_return[name].clear()

        is_rebalance = position in rebalance_positions
        snapshot: dict[str, Any] | None = None
        costs_today = {name: 0.0 for name in STRATEGY_NAMES}
        if is_rebalance:
            train = returns.iloc[max(0, position - effective_train) : position].copy()
            if not train.empty and train.index.max() >= current_date:
                raise AssertionError("Point-in-time violation: training data reaches execution date")
            required_observations = max(
                2,
                min(
                    max(2, config.minimum_covariance_observations),
                    max(2, len(train)),
                ),
            )
            execution_prices = prices.loc[current_date]
            execution_availability = availability.loc[current_date]
            eligibility = _eligible_universe(
                train,
                execution_prices,
                execution_availability,
                tickers,
                minimum_coverage=config.minimum_asset_coverage,
                minimum_observations=required_observations,
            )
            eligible = eligibility["eligible"]
            covariance_eligible, covariance_excluded = _covariance_universe(
                train,
                eligible,
                minimum_observations=required_observations,
            )
            exclusions = dict(eligibility["excluded"])
            exclusions.update(covariance_excluded)
            targets, explanations, input_snapshot = _strategy_targets(
                initial_weights=initial_weights,
                pre_trade_weights=daily_pre_trade,
                train=train,
                eligible=eligible,
                covariance_eligible=covariance_eligible,
                sector_map=sector_map,
            )
            latest_explanations.update(explanations)
            strategy_audit: dict[str, dict[str, Any]] = {}
            for name, state in states.items():
                pre_trade = daily_pre_trade[name]
                if name == "buy_and_hold":
                    target = dict(pre_trade)
                    executed = dict(pre_trade)
                    turnover = 0.0
                    executed_turnover = 0.0
                    execution_status = "not_applicable_buy_and_hold"
                elif daily_gross[name] is None:
                    target = _target_with_unavailable_holdings(
                        pre_trade,
                        targets[name],
                        eligible,
                        execution_availability,
                    )
                    executed = dict(pre_trade)
                    turnover = _turnover(pre_trade, target)
                    executed_turnover = 0.0
                    execution_status = "skipped_unavailable_pre_trade_valuation"
                else:
                    target = _target_with_unavailable_holdings(
                        pre_trade,
                        targets[name],
                        eligible,
                        execution_availability,
                    )
                    executed = _apply_trade_constraints(
                        pre_trade,
                        target,
                        config.turnover_limit,
                        config.minimum_trade_amount,
                        state.nav,
                    )
                    turnover = _turnover(pre_trade, target)
                    executed_turnover = _turnover(pre_trade, executed)
                    execution_status = "executed_at_close"
                cost_rate = executed_turnover * (
                    config.transaction_cost_bps + config.slippage_bps
                ) / 10_000.0
                cost_amount = state.nav * cost_rate
                state.nav -= cost_amount
                state.weights = executed
                state.turnover += turnover
                state.executed_turnover += executed_turnover
                state.costs += cost_amount
                costs_today[name] = cost_rate
                audit = {
                    "pre_trade_weights": _rounded_weights(pre_trade),
                    "target_weights": _rounded_weights(target),
                    "executed_weights": _rounded_weights(executed),
                    "post_return_weights": None,
                    "post_return_date": None,
                    "turnover": round(turnover, 8),
                    "executed_turnover": round(executed_turnover, 8),
                    "costs": {
                        "rate": round(cost_rate, 10),
                        "amount": round(cost_amount, 6),
                        "transaction_cost_bps": config.transaction_cost_bps,
                        "slippage_bps": config.slippage_bps,
                    },
                    "execution_status": execution_status,
                }
                strategy_audit[name] = audit
                if execution_status != "skipped_unavailable_pre_trade_valuation":
                    pending_post_return[name].append(audit)
            snapshot = {
                "rebalance_date": _date_string(current_date),
                "execution_date": _date_string(current_date),
                "effective_from": (
                    _date_string(returns.index[position + 1])
                    if position + 1 < len(returns)
                    else None
                ),
                "train_start": _date_string(train.index.min()) if not train.empty else None,
                "train_end": _date_string(train.index.max()) if not train.empty else None,
                "training_observations": len(train),
                "eligible_universe": eligible,
                "covariance_universe": covariance_eligible,
                "excluded_assets": exclusions,
                "input_hash": _hash_frame(train),
                "input_snapshot": input_snapshot,
                "strategies": strategy_audit,
                # Compatibility alias. These are executed, not static target, weights.
                "weights": {
                    name: audit["executed_weights"] for name, audit in strategy_audit.items()
                },
            }
            snapshots.append(snapshot)

        for name, state in states.items():
            gross = daily_gross[name]
            if gross is None:
                net_return = None
            else:
                net_return = (1.0 + gross) * (1.0 - costs_today[name]) - 1.0
                strategy_returns[name].loc[current_date] = net_return
            nav_history[name].append(
                {
                    "date": _date_string(current_date),
                    "return": round(net_return, 10) if net_return is not None else None,
                    "nav": round(state.nav, 6),
                    "weights": _rounded_weights(state.weights),
                    "data_status": "valid" if gross is not None else "unavailable",
                }
            )

    benchmark_returns, benchmark_name, benchmark_data = _load_benchmark_returns(
        config.benchmark_price_csv, out_of_sample_index
    )
    results: list[dict[str, Any]] = []
    for name in STRATEGY_NAMES:
        series = strategy_returns[name]
        final_weights = states[name].weights
        result = {
            "strategy": name,
            **_risk_metrics(series),
            **_benchmark_metrics(series, benchmark_returns),
            "turnover": round(states[name].turnover, 8),
            "executed_turnover": round(states[name].executed_turnover, 8),
            "total_costs": round(states[name].costs, 6),
            "weights": _rounded_weights(final_weights),
            "return_observations": int(series.notna().sum()),
            "return_coverage": round(float(series.notna().mean()), 6),
            "risk_contribution": _risk_contribution(final_weights, returns.loc[series.index]),
            "sector_active_weight": _sector_active_weight(
                final_weights,
                sector_map,
                config.benchmark_sector_weights or {},
            ),
            "single_name_active_weight": {
                ticker: round(
                    weight
                    - (config.benchmark_constituent_weights or {}).get(ticker, 0.0),
                    6,
                )
                for ticker, weight in final_weights.items()
            },
            "confidence_intervals": _confidence_intervals(series),
            "stress_test": _stress_test(
                final_weights, portfolio_positions, config.stress_scenarios
            ),
            "nav_series": nav_history[name],
        }
        results.append(result)

    start_date, end_date = _date_bounds(prices)
    data_as_of = (
        datetime.combine(pd.Timestamp(prices.index.max()).date(), datetime.max.time(), tzinfo=UTC)
        .isoformat()
    )
    report = {
        "backtest_run_id": str(uuid.uuid4()),
        "status": "completed",
        "error": None,
        "portfolio_csv": str(config.portfolio_csv),
        "portfolio_snapshot_id": config.portfolio_snapshot_id,
        "prices_csv": price_bundle.source_path,
        "price_source": price_bundle.data_source,
        "data_source": price_bundle.data_source,
        "data_as_of": data_as_of,
        "code_version": code_version,
        "config_hash": config_hash,
        "input_hash": cache_key,
        "cache_key": cache_key,
        "cache_hit": False,
        "price_data_hash": price_data_hash,
        "portfolio_input_hash": portfolio_hash,
        "execution_convention": EXECUTION_CONVENTION,
        "start_date": start_date,
        "end_date": end_date,
        "asset_count": sum(prices[ticker].notna().any() for ticker in tickers),
        "missing_price_assets": [ticker for ticker in tickers if prices[ticker].notna().sum() == 0],
        "mock_price_data_used": price_bundle.mock_used,
        "run_mode": "synthetic_smoke" if price_bundle.mock_used else "historical_data",
        "mock_data_note": (
            "No usable historical price CSV was supplied; reproducible mock prices were generated."
            if price_bundle.mock_used
            else ""
        ),
        "periods": len(returns),
        "methodology": EXECUTION_CONVENTION,
        "train_window": config.train_window,
        "effective_train_window": effective_train,
        "holding_window": config.holding_window,
        "holding_window_deprecated": True,
        "rebalance_frequency": frequency,
        "minimum_asset_coverage": config.minimum_asset_coverage,
        "minimum_covariance_observations": config.minimum_covariance_observations,
        "rebalance_dates": [item["rebalance_date"] for item in snapshots],
        "rebalance_snapshots": snapshots,
        "cost_assumptions": {
            "transaction_cost_bps": config.transaction_cost_bps,
            "slippage_bps": config.slippage_bps,
            "minimum_trade_amount": config.minimum_trade_amount,
            "turnover_limit": config.turnover_limit,
        },
        "config": _config_payload(config),
        "benchmark": {
            "name": benchmark_name,
            "price_csv": str(config.benchmark_price_csv) if config.benchmark_price_csv else None,
            **benchmark_data,
            "sector_weights": config.benchmark_sector_weights or {},
            "constituent_weights": config.benchmark_constituent_weights or {},
        },
        "availability_summary": _availability_summary(availability),
        "out_of_sample": True,
        "data_leakage_checks": {
            "status": "passed",
            "training_strictly_before_execution": all(
                not item["train_end"] or item["train_end"] < item["execution_date"]
                for item in snapshots
            ),
            "training_strictly_before_rebalance": all(
                not item["train_end"] or item["train_end"] < item["rebalance_date"]
                for item in snapshots
            ),
            "new_weights_receive_execution_day_return": False,
            "future_prices_used_for_estimation": False,
            "llm_controls_target_weights": False,
        },
        "deprecated_strategy_aliases": {
            "original_portfolio": "periodic_rebalanced_original",
            "rule_risk_adjusted": None,
            "llm_historical_adjusted": None,
        },
        "confidence_intervals": {
            row["strategy"]: row["confidence_intervals"] for row in results
        },
        "strategies": results,
        "minimum_variance_explanations": latest_explanations["minimum_variance"],
        "mean_variance_explanations": latest_explanations["mean_variance"],
    }
    clean_report = _json_ready(report)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(
        json.dumps(clean_report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return clean_report


def _strategy_targets(
    *,
    initial_weights: dict[str, float],
    pre_trade_weights: dict[str, dict[str, float]],
    train: pd.DataFrame,
    eligible: list[str],
    covariance_eligible: list[str],
    sector_map: dict[str, str],
) -> tuple[dict[str, dict[str, float]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    eligible_current = _normalize_weights(
        {ticker: initial_weights.get(ticker, 0.0) for ticker in eligible}
    )
    expected_returns = train[covariance_eligible].mean(skipna=True) * 252
    covariance = train[covariance_eligible].cov(min_periods=2) * 252
    asset_risk_metrics = _asset_risk_metrics(train, eligible)
    minimum_rows: list[dict[str, Any]] = []
    mean_rows: list[dict[str, Any]] = []
    targets: dict[str, dict[str, float]] = {
        "buy_and_hold": dict(pre_trade_weights["buy_and_hold"]),
        "periodic_rebalanced_original": dict(eligible_current),
        "equal_weight": _targets_to_weight_map(equal_weight_baseline(eligible_current)),
        "risk_parity": _targets_to_weight_map(
            risk_parity_simple(eligible_current, asset_risk_metrics)
        ),
    }
    if covariance_eligible and np.isfinite(covariance.to_numpy(dtype=float)).all():
        optimizer_current = _normalize_weights(
            {ticker: eligible_current.get(ticker, 0.0) for ticker in covariance_eligible}
        )
        minimum_rows = minimum_variance_portfolio(
            current_weights=optimizer_current,
            expected_returns=expected_returns,
            covariance=covariance,
            max_weight=0.35,
            sector_map=sector_map,
            sector_max_weight=0.55,
        )
        mean_rows = mean_variance_portfolio(
            current_weights=optimizer_current,
            expected_returns=expected_returns,
            covariance=covariance,
            risk_aversion=5.0,
            max_weight=0.35,
            sector_map=sector_map,
            sector_max_weight=0.55,
        )
    targets["minimum_variance"] = (
        _targets_to_weight_map(minimum_rows) if minimum_rows else dict(eligible_current)
    )
    targets["mean_variance"] = (
        _targets_to_weight_map(mean_rows) if mean_rows else dict(eligible_current)
    )
    return (
        targets,
        {"minimum_variance": minimum_rows, "mean_variance": mean_rows},
        {
            "expected_returns": {
                ticker: round(float(value), 8)
                for ticker, value in expected_returns.items()
                if pd.notna(value)
            },
            "covariance": {
                row: {
                    column: round(float(covariance.loc[row, column]), 10)
                    for column in covariance.columns
                    if pd.notna(covariance.loc[row, column])
                }
                for row in covariance.index
            },
            "risk_labels": {
                ticker: values["risk_level"] for ticker, values in asset_risk_metrics.items()
            },
        },
    )


def _eligible_universe(
    train: pd.DataFrame,
    execution_prices: pd.Series,
    execution_status: pd.Series,
    tickers: list[str],
    *,
    minimum_coverage: float,
    minimum_observations: int,
) -> dict[str, Any]:
    eligible: list[str] = []
    excluded: dict[str, str] = {}
    threshold = min(1.0, max(0.0, minimum_coverage))
    for ticker in tickers:
        status = str(execution_status.get(ticker, "data_missing"))
        if status != TRADABLE_PRICE_STATUS or pd.isna(execution_prices.get(ticker)):
            excluded[ticker] = f"execution_price_unavailable:{status}"
            continue
        observations = int(train[ticker].notna().sum()) if ticker in train else 0
        coverage = observations / len(train) if len(train) else 0.0
        if observations < minimum_observations:
            excluded[ticker] = (
                f"insufficient_observations:{observations}<{minimum_observations}"
            )
            continue
        if coverage < threshold:
            excluded[ticker] = f"insufficient_coverage:{coverage:.4f}<{threshold:.4f}"
            continue
        eligible.append(ticker)
    return {"eligible": eligible, "excluded": excluded}


def _covariance_universe(
    train: pd.DataFrame,
    eligible: list[str],
    *,
    minimum_observations: int,
) -> tuple[list[str], dict[str, str]]:
    universe = list(eligible)
    excluded: dict[str, str] = {}
    while universe:
        covariance = train[universe].cov(min_periods=minimum_observations)
        if np.isfinite(covariance.to_numpy(dtype=float)).all():
            return universe, excluded
        missing_counts = covariance.isna().sum(axis=0)
        remove = str(missing_counts.sort_values(ascending=False).index[0])
        universe.remove(remove)
        excluded[remove] = "insufficient_covariance_overlap"
    return [], excluded


def _target_with_unavailable_holdings(
    pre_trade: dict[str, float],
    target_eligible: dict[str, float],
    eligible: list[str],
    execution_status: pd.Series,
) -> dict[str, float]:
    target_universe = set(target_eligible)
    unavailable = {
        ticker: weight
        for ticker, weight in pre_trade.items()
        if (
            str(execution_status.get(ticker, "data_missing")) != TRADABLE_PRICE_STATUS
            or ticker not in target_universe
        )
    }
    locked = min(1.0, sum(unavailable.values()))
    residual = max(0.0, 1.0 - locked)
    normalized_target = _normalize_weights(
        {
            ticker: target_eligible.get(ticker, 0.0)
            for ticker in eligible
            if ticker in target_universe
        }
    )
    output = {ticker: weight for ticker, weight in unavailable.items()}
    for ticker in pre_trade:
        output.setdefault(ticker, residual * normalized_target.get(ticker, 0.0))
    return _normalize_weights(output)


def _apply_daily_return(
    weights: dict[str, float], day_returns: pd.Series
) -> tuple[float | None, dict[str, float]]:
    active = {ticker: weight for ticker, weight in weights.items() if weight > 1e-15}
    if not active:
        return None, dict(weights)
    values: dict[str, float] = {}
    for ticker, weight in active.items():
        value = day_returns.get(ticker, np.nan)
        if pd.isna(value):
            return None, dict(weights)
        values[ticker] = float(value)
    portfolio_return = sum(active[ticker] * values[ticker] for ticker in active)
    denominator = 1.0 + portfolio_return
    if denominator <= 0:
        return portfolio_return, {ticker: 0.0 for ticker in weights}
    drifted = {
        ticker: weight * (1.0 + values[ticker]) / denominator
        for ticker, weight in active.items()
    }
    for ticker in weights:
        drifted.setdefault(ticker, 0.0)
    return portfolio_return, _normalize_weights(drifted)


def _returns_from_prices(prices: pd.DataFrame, availability: pd.DataFrame) -> pd.DataFrame:
    valued = prices.copy()
    for ticker in valued.columns:
        prior: float | None = None
        for index in valued.index:
            status = str(availability.at[index, ticker])
            value = valued.at[index, ticker]
            if pd.notna(value) and float(value) > 0:
                prior = float(value)
                continue
            if status in LEGAL_HOLD_STATUSES and prior is not None:
                valued.at[index, ticker] = prior
            else:
                valued.at[index, ticker] = np.nan
    return valued.pct_change(fill_method=None).iloc[1:]


def _asset_risk_metrics(
    train: pd.DataFrame, tickers: list[str]
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        clean = train[ticker].dropna() if ticker in train else pd.Series(dtype=float)
        output[ticker] = {
            "annual_volatility": (
                calculate_annualized_volatility(clean) if len(clean) >= 2 else None
            ),
            "max_drawdown": calculate_max_drawdown(clean) if len(clean) else None,
            "risk_level": _risk_level_from_returns(clean),
            "observations": len(clean),
        }
    return output


def _apply_trade_constraints(
    current: dict[str, float],
    target: dict[str, float],
    turnover_limit: float,
    minimum_trade_amount: float,
    capital: float,
) -> dict[str, float]:
    tickers = set(current) | set(target)
    constrained = dict(target)
    minimum_weight = max(0.0, minimum_trade_amount) / max(1.0, capital)
    for ticker in tickers:
        if abs(target.get(ticker, 0.0) - current.get(ticker, 0.0)) < minimum_weight:
            constrained[ticker] = current.get(ticker, 0.0)
    constrained = _normalize_weights(constrained)
    turnover = _turnover(current, constrained)
    limit = max(0.0, float(turnover_limit))
    if turnover > limit and turnover > 0:
        scale = limit / turnover
        constrained = {
            ticker: current.get(ticker, 0.0)
            + scale * (constrained.get(ticker, 0.0) - current.get(ticker, 0.0))
            for ticker in tickers
        }
    return _normalize_weights(constrained)


def _risk_metrics(series: pd.Series) -> dict[str, Any]:
    clean = series.dropna()
    if clean.empty:
        return {
            key: None
            for key in (
                "annual_return",
                "annual_volatility",
                "max_drawdown",
                "sharpe_ratio",
                "downside_volatility",
                "sortino_ratio",
                "historical_var",
                "historical_cvar",
            )
        }
    annual_return = calculate_annual_return(clean)
    annual_vol = calculate_annualized_volatility(clean)
    downside = clean[clean < 0]
    downside_vol = (
        float(downside.std(ddof=1) * np.sqrt(252)) if len(downside) > 1 else None
    )
    var_cutoff = float(clean.quantile(0.05))
    tail = clean[clean <= var_cutoff]
    return {
        "annual_return": round(annual_return, 6),
        "annual_volatility": round(annual_vol, 6),
        "max_drawdown": round(calculate_max_drawdown(clean), 6),
        "sharpe_ratio": round(calculate_sharpe_ratio(clean), 4),
        "downside_volatility": (
            round(downside_vol, 6) if downside_vol is not None else None
        ),
        "sortino_ratio": (
            round(annual_return / downside_vol, 4)
            if downside_vol is not None and downside_vol > 0
            else None
        ),
        "historical_var": round(max(0.0, -var_cutoff), 6),
        "historical_cvar": (
            round(max(0.0, -float(tail.mean())), 6) if len(tail) else None
        ),
    }


def _load_benchmark_returns(
    path: Path | None, index: pd.Index
) -> tuple[pd.Series, str | None, dict[str, Any]]:
    empty = pd.Series(np.nan, index=index, dtype=float)
    if not path or not path.exists():
        return empty, None, {"aligned_sample_count": 0, "benchmark_coverage": 0.0}
    frame = pd.read_csv(path)
    columns = {str(column).lower(): column for column in frame.columns}
    if "date" not in columns:
        raise ValueError("Benchmark CSV requires a date column")
    date_col = columns["date"]
    if {"ticker", "close"}.issubset(columns):
        ticker_col, close_col = columns["ticker"], columns["close"]
        available_names = frame[ticker_col].dropna()
        if available_names.empty:
            return empty, None, {"aligned_sample_count": 0, "benchmark_coverage": 0.0}
        name = str(available_names.iloc[0])
        frame = frame[frame[ticker_col].astype(str) == name]
        value_col = close_col
    else:
        candidates = [column for column in frame.columns if column != date_col]
        if not candidates:
            raise ValueError("Benchmark CSV requires a price column")
        value_col = candidates[0]
        name = str(value_col)
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    values = pd.to_numeric(frame[value_col], errors="coerce")
    prices = pd.Series(values.to_numpy(), index=dates).sort_index()
    prices = prices.where(prices > 0)
    returns = prices.pct_change(fill_method=None).iloc[1:].reindex(index)
    count = int(returns.notna().sum())
    return returns, name, {
        "aligned_sample_count": count,
        "benchmark_coverage": round(count / len(index), 6) if len(index) else 0.0,
    }


def _benchmark_metrics(portfolio: pd.Series, benchmark: pd.Series) -> dict[str, Any]:
    aligned = pd.concat(
        [portfolio.rename("portfolio"), benchmark.rename("benchmark")], axis=1
    ).dropna()
    null_metrics = {
        "benchmark_return": None,
        "active_return": None,
        "tracking_error": None,
        "information_ratio": None,
        "beta": None,
        "alpha": None,
        "active_drawdown": None,
        "benchmark_aligned_sample_count": len(aligned),
        "benchmark_coverage": (
            round(len(aligned) / int(portfolio.notna().sum()), 6)
            if portfolio.notna().sum()
            else 0.0
        ),
    }
    if len(aligned) < 2:
        return null_metrics
    active = aligned["portfolio"] - aligned["benchmark"]
    benchmark_return = calculate_annual_return(aligned["benchmark"])
    portfolio_return = calculate_annual_return(aligned["portfolio"])
    tracking_error = float(active.std(ddof=1) * np.sqrt(252))
    variance = float(aligned["benchmark"].var(ddof=1))
    beta = (
        float(aligned.cov().loc["portfolio", "benchmark"] / variance)
        if variance > 0
        else None
    )
    relative_curve = (1.0 + active).cumprod()
    active_drawdown = float((relative_curve / relative_curve.cummax() - 1.0).min())
    return {
        "benchmark_return": round(benchmark_return, 6),
        "active_return": round(portfolio_return - benchmark_return, 6),
        "tracking_error": round(tracking_error, 6),
        "information_ratio": (
            round((portfolio_return - benchmark_return) / tracking_error, 4)
            if tracking_error > 0
            else None
        ),
        "beta": round(beta, 6) if beta is not None else None,
        "alpha": (
            round(portfolio_return - beta * benchmark_return, 6)
            if beta is not None
            else None
        ),
        "active_drawdown": round(active_drawdown, 6),
        "benchmark_aligned_sample_count": len(aligned),
        "benchmark_coverage": round(
            len(aligned) / int(portfolio.notna().sum()), 6
        ),
    }


def _risk_contribution(
    weights: dict[str, float], returns: pd.DataFrame
) -> dict[str, float | None]:
    columns = [ticker for ticker in weights if ticker in returns.columns]
    if not columns:
        return {ticker: None for ticker in weights}
    complete = returns[columns].dropna(how="any")
    if len(complete) < 2:
        return {ticker: None for ticker in weights}
    covariance = complete.cov().to_numpy()
    if not np.isfinite(covariance).all():
        return {ticker: None for ticker in weights}
    vector = np.array([weights[ticker] for ticker in columns])
    variance = float(vector @ covariance @ vector)
    if variance <= 0:
        return {ticker: None for ticker in weights}
    contributions = vector * (covariance @ vector) / variance
    return {
        ticker: round(float(value), 6)
        for ticker, value in zip(columns, contributions, strict=False)
    }


def _sector_active_weight(
    weights: dict[str, float],
    sector_map: dict[str, str],
    benchmark_weights: dict[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sector_map.get(ticker, "Unknown")
        result[sector] = result.get(sector, 0.0) + weight
    return {
        sector: round(result.get(sector, 0.0) - benchmark_weights.get(sector, 0.0), 6)
        for sector in set(result) | set(benchmark_weights)
    }


def _stress_test(
    weights: dict[str, float],
    rows: list[dict[str, Any]],
    scenarios: dict[str, float] | None,
) -> dict[str, Any]:
    shocks = scenarios or {
        "equity_market_shock": -0.20,
        "technology_sector_shock": -0.30,
        "interest_rate_shock": -0.10,
        "currency_shock": -0.08,
    }
    by_ticker = {str(row.get("ticker")): row for row in rows}
    result = {}
    for scenario, shock in shocks.items():
        contributions = {}
        for ticker, weight in weights.items():
            row = by_ticker.get(ticker, {})
            sector = str(row.get("sector", ""))
            asset_type = str(row.get("asset_type", "equity"))
            market = str(row.get("market", "Global"))
            applies = (
                (scenario == "equity_market_shock" and "cash" not in asset_type.lower())
                or (scenario == "technology_sector_shock" and "tech" in sector.lower())
                or (
                    scenario == "interest_rate_shock"
                    and any(
                        term in (sector + asset_type).lower()
                        for term in ("bond", "financial")
                    )
                )
                or (scenario == "currency_shock" and market.upper() not in {"US", "USA"})
            )
            contributions[ticker] = round(weight * float(shock), 6) if applies else 0.0
        result[scenario] = {
            "portfolio_loss": round(sum(contributions.values()), 6),
            "asset_loss_contribution": contributions,
        }
    return result


def _confidence_intervals(series: pd.Series) -> dict[str, float | None]:
    clean = series.dropna()
    if len(clean) < 2:
        return {"annual_return_lower_95": None, "annual_return_upper_95": None}
    annual = float(clean.mean() * 252)
    error = float(1.96 * clean.std(ddof=1) / np.sqrt(len(clean)) * 252)
    return {
        "annual_return_lower_95": round(annual - error, 6),
        "annual_return_upper_95": round(annual + error, 6),
    }


def load_or_mock_prices(
    tickers: list[str],
    prices_csv: Path | None,
    periods: int = 252,
    seed: int = 42,
) -> PriceDataBundle:
    """Load historical prices with availability metadata or explicit mock data."""
    if prices_csv and prices_csv.exists():
        frame = pd.read_csv(prices_csv)
        prices, availability = _normalize_price_input(frame, tickers)
        return PriceDataBundle(
            prices=prices,
            availability=availability,
            mock_used=False,
            data_source="historical_csv",
            source_path=str(prices_csv),
        )
    prices = generate_mock_price_data(tickers, periods=periods, seed=seed)
    availability = pd.DataFrame("observed", index=prices.index, columns=prices.columns)
    return PriceDataBundle(
        prices=prices,
        availability=availability,
        mock_used=True,
        data_source="mock_price_data",
        source_path=None,
    )


def resolve_prices_csv(candidate: str | Path | None = None) -> Path | None:
    if candidate:
        path = Path(candidate).expanduser()
        return path if path.exists() and path.is_file() else None
    return DEFAULT_PRICE_CSV if DEFAULT_PRICE_CSV.exists() else None


def generate_mock_price_data(
    tickers: list[str], periods: int = 252, seed: int = 42
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq="B")
    data = {}
    for idx, ticker in enumerate(tickers):
        base = 50.0 + idx * 15.0
        annual_mu = 0.06 + idx * 0.005
        annual_sigma = 0.18 + (idx % 4) * 0.06
        if ticker.upper().startswith("POLY"):
            base, annual_mu, annual_sigma = 0.50, 0.02, 0.55
        daily = rng.normal(annual_mu / 252, annual_sigma / np.sqrt(252), size=periods)
        data[ticker] = np.maximum(base * np.cumprod(1.0 + daily), 0.01)
    return pd.DataFrame(data, index=dates)


def _normalize_price_csv(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Compatibility helper returning only the normalized raw price matrix."""
    return _normalize_price_input(frame, tickers)[0]


def _normalize_price_input(
    frame: pd.DataFrame, tickers: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    columns = {column.lower(): column for column in frame.columns}
    canonical = {ticker.upper(): ticker for ticker in tickers}
    explicit_status: pd.DataFrame | None = None
    if {"date", "ticker", "close"}.issubset(columns):
        date_col, ticker_col, close_col = (
            columns["date"],
            columns["ticker"],
            columns["close"],
        )
        slim = frame.copy()
        slim[date_col] = pd.to_datetime(slim[date_col], errors="coerce")
        slim[ticker_col] = slim[ticker_col].astype(str).str.strip().str.upper()
        slim[ticker_col] = slim[ticker_col].map(lambda value: canonical.get(value, value))
        slim[close_col] = pd.to_numeric(slim[close_col], errors="coerce")
        slim = slim.dropna(subset=[date_col, ticker_col])
        wide = slim.pivot_table(
            index=date_col, columns=ticker_col, values=close_col, aggfunc="last", dropna=False
        )
        status_col = columns.get("availability_status") or columns.get("status")
        if status_col:
            normalized_status = slim[status_col].map(_normalize_availability_status)
            explicit_status = slim.assign(_status=normalized_status).pivot_table(
                index=date_col,
                columns=ticker_col,
                values="_status",
                aggfunc="last",
                dropna=False,
            )
        elif "is_trading_day" in columns:
            status_col = columns["is_trading_day"]
            statuses = slim[status_col].map(
                lambda value: "observed" if _truthy(value) else "exchange_closed"
            )
            explicit_status = slim.assign(_status=statuses).pivot_table(
                index=date_col,
                columns=ticker_col,
                values="_status",
                aggfunc="last",
                dropna=False,
            )
    elif "date" in columns:
        wide = frame.set_index(columns["date"])
    else:
        wide = frame
    wide.columns = [str(column).strip().upper() for column in wide.columns]
    wide = wide.rename(columns={key: value for key, value in canonical.items()})
    wide = wide.reindex(columns=tickers).apply(pd.to_numeric, errors="coerce")
    if not isinstance(wide.index, pd.DatetimeIndex):
        wide.index = pd.to_datetime(wide.index, errors="coerce")
    wide = wide[~wide.index.isna()].sort_index()
    wide = wide[~wide.index.duplicated(keep="last")]
    wide = wide.where(wide > 0)
    if wide.dropna(axis=1, how="all").empty:
        raise ValueError("Historical price CSV does not contain portfolio tickers")

    availability = pd.DataFrame("data_missing", index=wide.index, columns=tickers)
    for ticker in tickers:
        valid = wide[ticker].notna()
        availability.loc[valid, ticker] = "observed"
        observed_dates = wide.index[valid]
        if len(observed_dates):
            availability.loc[wide.index < observed_dates.min(), ticker] = "not_listed"
        else:
            availability.loc[:, ticker] = "not_listed"
    if explicit_status is not None:
        explicit_status.columns = [str(column).strip().upper() for column in explicit_status.columns]
        explicit_status = explicit_status.rename(
            columns={key: value for key, value in canonical.items()}
        ).reindex(index=wide.index, columns=tickers)
        availability = explicit_status.combine_first(availability)
        availability = availability.where(wide.isna(), "observed")
    return wide, availability


def _normalize_availability_status(value: object) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "trading": "observed",
        "valid": "observed",
        "market_closed": "exchange_closed",
        "holiday": "exchange_closed",
        "halted": "suspended",
        "data_missing": "data_missing",
        "missing": "data_missing",
        "pre_listing": "not_listed",
    }
    return aliases.get(normalized, normalized or "data_missing")


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _availability_summary(availability: pd.DataFrame) -> dict[str, int]:
    values = availability.stack(future_stack=True).value_counts()
    return {str(status): int(count) for status, count in values.items()}


def _date_bounds(prices: pd.DataFrame) -> tuple[str | None, str | None]:
    if prices.empty:
        return None, None
    return _date_string(prices.index.min()), _date_string(prices.index.max())


def _weights_from_portfolio_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    values = {
        str(row["ticker"]).upper(): max(0.0, float(row.get("totalValue", 0.0) or 0.0))
        for row in rows
    }
    return _normalize_weights(values)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value or 0.0)) for value in weights.values())
    if total <= 0:
        return {ticker: 0.0 for ticker in weights}
    return {
        ticker: max(0.0, float(value or 0.0)) / total for ticker, value in weights.items()
    }


def _rounded_weights(weights: dict[str, float]) -> dict[str, float]:
    return {ticker: round(float(weight), 8) for ticker, weight in sorted(weights.items())}


def _targets_to_weight_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(row["ticker"]).upper(): float(row.get("target_weight", 0.0) or 0.0)
        for row in rows
    }


def _sector_map_from_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("ticker", "")).upper(): str(row.get("sector", "Unknown") or "Unknown")
        for row in rows
        if row.get("ticker")
    }


def _turnover(current: dict[str, float], target: dict[str, float]) -> float:
    return 0.5 * sum(
        abs(float(target.get(ticker, 0.0)) - float(current.get(ticker, 0.0)))
        for ticker in set(current) | set(target)
    )


def _risk_level_from_returns(returns: pd.Series) -> str:
    clean = returns.dropna()
    if len(clean) < 2:
        return "unavailable"
    volatility = calculate_annualized_volatility(clean)
    drawdown = abs(calculate_max_drawdown(clean))
    if volatility > 0.35 or drawdown > 0.30:
        return "high"
    if volatility > 0.18 or drawdown > 0.15:
        return "medium"
    return "low"


def _config_hash(config: BacktestConfig) -> str:
    excluded = {"portfolio_csv", "prices_csv", "output_path", "benchmark_price_csv", "use_cache"}
    payload = {
        key: value
        for key, value in asdict(config).items()
        if key not in excluded and key not in {"portfolio_snapshot_id", "code_version"}
    }
    payload["benchmark_price_hash"] = (
        _file_hash(config.benchmark_price_csv)
        if config.benchmark_price_csv and config.benchmark_price_csv.exists()
        else None
    )
    payload["execution_convention"] = EXECUTION_CONVENTION
    return _hash_payload(payload)


def _config_payload(config: BacktestConfig) -> dict[str, Any]:
    payload = asdict(config)
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in payload.items()
        if key != "output_path"
    }


def _price_data_hash(bundle: PriceDataBundle) -> str:
    return _hash_payload(
        {
            "prices": bundle.prices.to_csv(date_format="%Y-%m-%d"),
            "availability": bundle.availability.to_csv(date_format="%Y-%m-%d"),
            "source": bundle.data_source,
            "mock_used": bundle.mock_used,
        }
    )


def _hash_frame(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(date_format="%Y-%m-%d").encode()).hexdigest()


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cached_report(path: Path, cache_key: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("cache_key") == cache_key else None


def _date_string(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value
