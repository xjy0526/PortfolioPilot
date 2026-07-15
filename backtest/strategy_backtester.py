"""Backtest strategy comparison for PortfolioPilot."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analytics.risk_metrics import (
    calculate_annual_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_returns,
    calculate_sharpe_ratio,
)
from fetchers.csv_reader import csv_positions_to_portfolio_format, parse_csv_file
from portfolio_optimizer import (
    equal_weight_baseline,
    llm_risk_adjusted_weighting,
    mean_variance_portfolio,
    minimum_variance_portfolio,
    risk_parity_simple,
)


@dataclass
class BacktestConfig:
    portfolio_csv: Path
    prices_csv: Path | None
    output_path: Path
    seed: int = 42
    periods: int = 252
    train_window: int = 60
    holding_window: int = 20
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


@dataclass
class PriceDataBundle:
    prices: pd.DataFrame
    mock_used: bool
    data_source: str
    source_path: str | None = None


DEFAULT_PRICE_CSV = Path(__file__).resolve().parent.parent / "data" / "prices" / "example_historical_prices.csv"


def run_strategy_backtest(config: BacktestConfig) -> dict[str, Any]:
    """Run a point-in-time walk-forward strategy comparison."""
    raw_positions = parse_csv_file(str(config.portfolio_csv))
    if not raw_positions:
        raise ValueError(f"No valid positions found in {config.portfolio_csv}")

    portfolio_positions = csv_positions_to_portfolio_format(raw_positions, prices={})
    tickers = [pos["ticker"] for pos in portfolio_positions]
    current_weights = _weights_from_portfolio_rows(portfolio_positions)
    price_bundle = load_or_mock_prices(tickers, config.prices_csv, periods=config.periods, seed=config.seed)
    prices = price_bundle.prices
    returns = calculate_returns(prices)
    start_date, end_date = _date_bounds(prices)
    priced_assets = [ticker for ticker in tickers if ticker in prices.columns]
    sector_map = _sector_map_from_rows(portfolio_positions)
    if config.run_llm_historical_adjusted and not config.llm_historical_snapshots:
        raise ValueError("llm_historical_adjusted requires historical prompt/evidence/model snapshots")

    strategy_names = [
        "original_portfolio", "equal_weight", "risk_parity", "minimum_variance",
        "mean_variance", "rule_risk_adjusted",
    ]
    if config.llm_historical_snapshots:
        strategy_names.append("llm_historical_adjusted")
    strategy_returns = {name: pd.Series(0.0, index=returns.index) for name in strategy_names}
    strategy_weights = {name: dict(current_weights) for name in strategy_names}
    turnovers = {name: 0.0 for name in strategy_names}
    snapshots: list[dict[str, Any]] = []
    min_var_explanations: list[dict[str, Any]] = []
    mean_var_explanations: list[dict[str, Any]] = []
    rule_explanations: list[dict[str, Any]] = []

    effective_train = max(1, min(config.train_window, max(1, len(returns) // 2)))
    frequency = max(1, config.rebalance_frequency)
    holding = max(1, config.holding_window)
    for position in range(effective_train, len(returns), frequency):
        rebalance_date = returns.index[position]
        train = returns.iloc[max(0, position - effective_train):position].copy()
        if not train.empty and train.index.max() >= rebalance_date:
            raise AssertionError("Point-in-time violation: training data reaches rebalance date")
        hold_end = min(len(returns), position + min(holding, frequency))
        expected_returns = train.mean().fillna(0.0) * 252 if not train.empty else pd.Series(0.0, index=tickers)
        covariance = (
            train.cov().fillna(0.0) * 252
            if len(train) >= 2 else pd.DataFrame(0.0, index=tickers, columns=tickers)
        )
        asset_risk_metrics = _asset_risk_metrics(train, tickers)
        targets: dict[str, dict[str, float]] = {
            "original_portfolio": current_weights,
            "equal_weight": _targets_to_weight_map(equal_weight_baseline(current_weights)),
            "risk_parity": _targets_to_weight_map(risk_parity_simple(current_weights, asset_risk_metrics)),
        }
        minimum_rows = minimum_variance_portfolio(
            current_weights=current_weights, expected_returns=expected_returns,
            covariance=covariance, max_weight=0.35, sector_map=sector_map, sector_max_weight=0.55,
        )
        mean_rows = mean_variance_portfolio(
            current_weights=current_weights, expected_returns=expected_returns,
            covariance=covariance, risk_aversion=5.0, max_weight=0.35,
            sector_map=sector_map, sector_max_weight=0.55,
        )
        targets["minimum_variance"] = _targets_to_weight_map(minimum_rows)
        targets["mean_variance"] = _targets_to_weight_map(mean_rows)
        rule_score = _rule_risk_score(asset_risk_metrics)
        rule_adjusted = llm_risk_adjusted_weighting(
            current_weights=current_weights, asset_risk_metrics=asset_risk_metrics,
            llm_risk_score=rule_score,
            asset_level_comments=[
                {"ticker": ticker, "risk_level": values["risk_level"], "comment": "Point-in-time rule label"}
                for ticker, values in asset_risk_metrics.items()
            ],
            sector_exposure=_sector_exposure_from_rows(portfolio_positions),
        )
        targets["rule_risk_adjusted"] = {
            item["ticker"]: float(item["target_weight"]) for item in rule_adjusted.get("suggestions", [])
        }
        if config.llm_historical_snapshots:
            snapshot = config.llm_historical_snapshots.get(pd.Timestamp(rebalance_date).date().isoformat())
            _validate_llm_snapshot(snapshot, rebalance_date)
            adjusted = llm_risk_adjusted_weighting(
                current_weights=current_weights, asset_risk_metrics=asset_risk_metrics,
                llm_risk_score=float(snapshot["risk_score"]),
                asset_level_comments=snapshot.get("asset_level_comments", []),
                sector_exposure=_sector_exposure_from_rows(portfolio_positions),
            )
            targets["llm_historical_adjusted"] = {
                item["ticker"]: float(item["target_weight"]) for item in adjusted.get("suggestions", [])
            }

        snapshot_weights: dict[str, dict[str, float]] = {}
        for name in strategy_names:
            target = _normalize_weights({ticker: targets[name].get(ticker, 0.0) for ticker in tickers})
            target = _apply_trade_constraints(
                strategy_weights[name], target, config.turnover_limit,
                config.minimum_trade_amount, config.initial_capital,
            )
            turnover = _turnover(strategy_weights[name], target)
            cost = turnover * (config.transaction_cost_bps + config.slippage_bps) / 10_000.0
            period_returns = _portfolio_returns(returns.iloc[position:hold_end], target)
            if not period_returns.empty:
                period_returns.iloc[0] -= cost
                strategy_returns[name].loc[period_returns.index] = period_returns
            strategy_weights[name] = target
            turnovers[name] += turnover
            snapshot_weights[name] = {ticker: round(value, 8) for ticker, value in target.items()}
        min_var_explanations = minimum_rows
        mean_var_explanations = mean_rows
        rule_explanations = rule_adjusted.get("suggestions", [])
        snapshots.append({
            "rebalance_date": pd.Timestamp(rebalance_date).date().isoformat(),
            "train_start": pd.Timestamp(train.index.min()).date().isoformat() if not train.empty else None,
            "train_end": pd.Timestamp(train.index.max()).date().isoformat() if not train.empty else None,
            "holding_end": pd.Timestamp(returns.index[hold_end - 1]).date().isoformat() if hold_end > position else None,
            "training_observations": len(train),
            "input_hash": hashlib.sha256(train.to_csv().encode()).hexdigest(),
            "input_snapshot": {
                "expected_returns": {ticker: round(float(expected_returns.get(ticker, 0.0)), 8) for ticker in tickers},
                "covariance": {
                    row: {column: round(float(covariance.loc[row, column]), 10) for column in covariance.columns}
                    for row in covariance.index
                },
                "risk_labels": {ticker: values["risk_level"] for ticker, values in asset_risk_metrics.items()},
            },
            "weights": snapshot_weights,
        })

    benchmark_returns, benchmark_name = _load_benchmark_returns(config.benchmark_price_csv, returns.index)
    results = []
    out_of_sample_index = returns.index[effective_train:]
    for name in strategy_names:
        series = strategy_returns[name].reindex(out_of_sample_index).fillna(0.0)
        active = _benchmark_metrics(series, benchmark_returns)
        final_weights = strategy_weights[name]
        result = {
            "strategy": name,
            **_risk_metrics(series),
            **active,
            "turnover": round(turnovers[name], 6),
            "weights": {ticker: round(weight, 6) for ticker, weight in final_weights.items()},
            "risk_contribution": _risk_contribution(final_weights, returns.loc[series.index]),
            "sector_active_weight": _sector_active_weight(
                final_weights, sector_map, config.benchmark_sector_weights or {},
            ),
            "single_name_active_weight": {
                ticker: round(weight - (config.benchmark_constituent_weights or {}).get(ticker, 0.0), 6)
                for ticker, weight in final_weights.items()
            },
            "confidence_intervals": _confidence_intervals(series),
            "stress_test": _stress_test(final_weights, portfolio_positions, config.stress_scenarios),
        }
        results.append(result)

    report = {
        "portfolio_csv": str(config.portfolio_csv),
        "prices_csv": price_bundle.source_path,
        "data_source": price_bundle.data_source,
        "start_date": start_date,
        "end_date": end_date,
        "asset_count": len(priced_assets),
        "missing_price_assets": [ticker for ticker in tickers if ticker not in prices.columns],
        "mock_price_data_used": price_bundle.mock_used,
        "mock_data_note": (
            "No real historical price CSV was supplied; generated reproducible mock prices."
            if price_bundle.mock_used else ""
        ),
        "periods": len(returns),
        "methodology": "Point-in-time walk-forward; each rebalance uses returns strictly before rebalance_date.",
        "train_window": config.train_window,
        "effective_train_window": effective_train,
        "holding_window": config.holding_window,
        "rebalance_frequency": config.rebalance_frequency,
        "rebalance_dates": [item["rebalance_date"] for item in snapshots],
        "rebalance_snapshots": snapshots,
        "cost_assumptions": {
            "transaction_cost_bps": config.transaction_cost_bps,
            "slippage_bps": config.slippage_bps,
            "minimum_trade_amount": config.minimum_trade_amount,
            "turnover_limit": config.turnover_limit,
        },
        "benchmark": {
            "name": benchmark_name,
            "price_csv": str(config.benchmark_price_csv) if config.benchmark_price_csv else None,
            "sector_weights": config.benchmark_sector_weights or {},
            "constituent_weights": config.benchmark_constituent_weights or {},
        },
        "out_of_sample": True,
        "data_leakage_checks": {
            "status": "passed",
            "training_strictly_before_rebalance": all(
                not item["train_end"] or item["train_end"] < item["rebalance_date"] for item in snapshots
            ),
            "future_prices_used_for_estimation": False,
            "llm_strategy_status": "historical_snapshots_validated" if config.llm_historical_snapshots else "rule_risk_adjusted_only",
        },
        "confidence_intervals": {row["strategy"]: row["confidence_intervals"] for row in results},
        "strategies": results,
        "minimum_variance_explanations": min_var_explanations,
        "mean_variance_explanations": mean_var_explanations,
        "rule_risk_adjusted_explanations": rule_explanations,
    }
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _asset_risk_metrics(train: pd.DataFrame, tickers: list[str]) -> dict[str, dict[str, Any]]:
    return {
        ticker: {
            "annual_volatility": calculate_annualized_volatility(train[ticker]) if ticker in train else 0.0,
            "max_drawdown": calculate_max_drawdown(train[ticker]) if ticker in train else 0.0,
            "risk_level": _risk_level_from_returns(train[ticker]) if ticker in train else "medium",
        }
        for ticker in tickers
    }


def _rule_risk_score(metrics: dict[str, dict[str, Any]]) -> float:
    if not metrics:
        return 5.0
    levels = {"low": 2.5, "medium": 5.0, "high": 8.0}
    return round(sum(levels.get(str(item.get("risk_level")), 5.0) for item in metrics.values()) / len(metrics), 2)


def _apply_trade_constraints(
    current: dict[str, float], target: dict[str, float], turnover_limit: float,
    minimum_trade_amount: float, capital: float,
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
            ticker: current.get(ticker, 0.0) + scale * (constrained.get(ticker, 0.0) - current.get(ticker, 0.0))
            for ticker in tickers
        }
    return _normalize_weights(constrained)


def _validate_llm_snapshot(snapshot: dict[str, Any] | None, rebalance_date: Any) -> None:
    if not snapshot:
        raise ValueError(f"Missing historical LLM snapshot for {pd.Timestamp(rebalance_date).date().isoformat()}")
    required = {"prompt_id", "prompt_version", "model", "evidence_snapshot", "risk_score", "as_of"}
    missing = sorted(required - set(snapshot))
    if missing:
        raise ValueError(f"Historical LLM snapshot missing: {', '.join(missing)}")
    if pd.Timestamp(snapshot["as_of"]) > pd.Timestamp(rebalance_date):
        raise ValueError("Historical LLM snapshot contains future information")


def _risk_metrics(series: pd.Series) -> dict[str, Any]:
    clean = series.dropna()
    annual_return = calculate_annual_return(clean)
    annual_vol = calculate_annualized_volatility(clean)
    downside = clean[clean < 0]
    downside_vol = float(downside.std(ddof=1) * np.sqrt(252)) if len(downside) > 1 else 0.0
    var_cutoff = float(clean.quantile(0.05)) if len(clean) else 0.0
    tail = clean[clean <= var_cutoff]
    return {
        "annual_return": round(annual_return, 6),
        "annual_volatility": round(annual_vol, 6),
        "max_drawdown": round(calculate_max_drawdown(clean), 6),
        "sharpe_ratio": round(calculate_sharpe_ratio(clean), 4),
        "downside_volatility": round(downside_vol, 6),
        "sortino_ratio": round(annual_return / downside_vol, 4) if downside_vol > 0 else None,
        "historical_var": round(max(0.0, -var_cutoff), 6),
        "historical_cvar": round(max(0.0, -float(tail.mean())), 6) if len(tail) else 0.0,
    }


def _load_benchmark_returns(path: Path | None, index: pd.Index) -> tuple[pd.Series, str | None]:
    if not path or not path.exists():
        return pd.Series(dtype=float), None
    frame = pd.read_csv(path)
    columns = {str(column).lower(): column for column in frame.columns}
    if "date" not in columns:
        raise ValueError("Benchmark CSV requires a date column")
    date_col = columns["date"]
    if {"ticker", "close"}.issubset(columns):
        ticker_col, close_col = columns["ticker"], columns["close"]
        first_ticker = str(frame[ticker_col].dropna().iloc[0])
        frame = frame[frame[ticker_col].astype(str) == first_ticker]
        value_col = close_col
        name = first_ticker
    else:
        candidates = [column for column in frame.columns if column != date_col]
        if not candidates:
            raise ValueError("Benchmark CSV requires a price column")
        value_col = candidates[0]
        name = str(value_col)
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    values = pd.to_numeric(frame[value_col], errors="coerce")
    prices = pd.Series(values.values, index=dates).dropna().sort_index()
    returns = prices.pct_change(fill_method=None).dropna()
    return returns.reindex(index).fillna(0.0), name


def _benchmark_metrics(portfolio: pd.Series, benchmark: pd.Series) -> dict[str, Any]:
    if benchmark.empty:
        return {
            "benchmark_return": None, "active_return": None, "tracking_error": None,
            "information_ratio": None, "beta": None, "alpha": None, "active_drawdown": None,
        }
    aligned = pd.concat([portfolio.rename("portfolio"), benchmark.rename("benchmark")], axis=1).fillna(0.0)
    active = aligned["portfolio"] - aligned["benchmark"]
    benchmark_return = calculate_annual_return(aligned["benchmark"])
    portfolio_return = calculate_annual_return(aligned["portfolio"])
    tracking_error = float(active.std(ddof=1) * np.sqrt(252)) if len(active) > 1 else 0.0
    variance = float(aligned["benchmark"].var(ddof=1)) if len(aligned) > 1 else 0.0
    beta = float(aligned.cov().loc["portfolio", "benchmark"] / variance) if variance > 0 else 0.0
    relative_curve = (1.0 + active).cumprod()
    active_drawdown = float((relative_curve / relative_curve.cummax() - 1.0).min()) if len(relative_curve) else 0.0
    return {
        "benchmark_return": round(benchmark_return, 6),
        "active_return": round(portfolio_return - benchmark_return, 6),
        "tracking_error": round(tracking_error, 6),
        "information_ratio": round((portfolio_return - benchmark_return) / tracking_error, 4) if tracking_error > 0 else None,
        "beta": round(beta, 6),
        "alpha": round(portfolio_return - beta * benchmark_return, 6),
        "active_drawdown": round(active_drawdown, 6),
    }


def _risk_contribution(weights: dict[str, float], returns: pd.DataFrame) -> dict[str, float]:
    columns = [ticker for ticker in weights if ticker in returns.columns]
    if not columns or len(returns.index) < 2:
        return {ticker: 0.0 for ticker in weights}
    covariance = returns[columns].cov().fillna(0.0).to_numpy()
    vector = np.array([weights[ticker] for ticker in columns])
    variance = float(vector @ covariance @ vector)
    if variance <= 0:
        return {ticker: 0.0 for ticker in weights}
    contributions = vector * (covariance @ vector) / variance
    return {ticker: round(float(value), 6) for ticker, value in zip(columns, contributions, strict=False)}


def _sector_active_weight(
    weights: dict[str, float], sector_map: dict[str, str], benchmark_weights: dict[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sector_map.get(ticker, "Unknown")
        result[sector] = result.get(sector, 0.0) + weight
    sectors = set(result) | set(benchmark_weights)
    return {
        sector: round(result.get(sector, 0.0) - benchmark_weights.get(sector, 0.0), 6)
        for sector in sectors
    }


def _stress_test(
    weights: dict[str, float], rows: list[dict[str, Any]], scenarios: dict[str, float] | None,
) -> dict[str, Any]:
    shocks = scenarios or {
        "equity_market_shock": -0.20, "technology_sector_shock": -0.30,
        "interest_rate_shock": -0.10, "currency_shock": -0.08,
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
                scenario == "equity_market_shock" and "cash" not in asset_type.lower()
                or scenario == "technology_sector_shock" and "tech" in sector.lower()
                or scenario == "interest_rate_shock" and any(term in (sector + asset_type).lower() for term in ("bond", "financial"))
                or scenario == "currency_shock" and market.upper() not in {"US", "USA"}
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
    """Load historical prices from CSV or generate deterministic mock data."""
    if prices_csv and prices_csv.exists():
        frame = pd.read_csv(prices_csv)
        return PriceDataBundle(
            prices=_normalize_price_csv(frame, tickers),
            mock_used=False,
            data_source="historical_csv",
            source_path=str(prices_csv),
        )
    return PriceDataBundle(
        prices=generate_mock_price_data(tickers, periods=periods, seed=seed),
        mock_used=True,
        data_source="mock_price_data",
        source_path=None,
    )


def resolve_prices_csv(candidate: str | Path | None = None) -> Path | None:
    """Resolve a historical prices CSV, preferring explicit paths then examples."""
    if candidate:
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file():
            return path
        return None
    if DEFAULT_PRICE_CSV.exists():
        return DEFAULT_PRICE_CSV
    return None


def generate_mock_price_data(tickers: list[str], periods: int = 252, seed: int = 42) -> pd.DataFrame:
    """Generate reproducible geometric random-walk price data."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq="B")
    data = {}
    for idx, ticker in enumerate(tickers):
        base = 50.0 + idx * 15.0
        annual_mu = 0.06 + idx * 0.005
        annual_sigma = 0.18 + (idx % 4) * 0.06
        if ticker.upper().startswith("POLY"):
            base = 0.50
            annual_mu = 0.02
            annual_sigma = 0.55
        daily = rng.normal(annual_mu / 252, annual_sigma / np.sqrt(252), size=periods)
        path = base * np.cumprod(1.0 + daily)
        data[ticker] = np.maximum(path, 0.01)
    return pd.DataFrame(data, index=dates)


def _normalize_price_csv(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(col).strip() for col in frame.columns]
    column_map = {col.lower(): col for col in frame.columns}
    ticker_set = {ticker.upper(): ticker for ticker in tickers}

    if {"date", "ticker", "close"}.issubset(column_map):
        date_col = column_map["date"]
        ticker_col = column_map["ticker"]
        close_col = column_map["close"]
        slim = frame[[date_col, ticker_col, close_col]].copy()
        slim.columns = ["date", "ticker", "close"]
        slim["ticker"] = slim["ticker"].astype(str).str.strip().str.upper()
        slim["close"] = pd.to_numeric(slim["close"], errors="coerce")
        slim["date"] = pd.to_datetime(slim["date"], errors="coerce")
        slim = slim.dropna(subset=["date", "ticker", "close"])
        slim = slim[slim["close"] > 0]
        slim["ticker"] = slim["ticker"].map(lambda value: ticker_set.get(value, value))
        wide = slim.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    elif "date" in column_map:
        date_col = column_map["date"]
        wide = frame.set_index(date_col)
    else:
        wide = frame

    wide.columns = [str(col).strip().upper() for col in wide.columns]
    wide = wide.rename(columns={key: value for key, value in ticker_set.items()})
    wide = wide[[col for col in wide.columns if col in tickers]]
    wide = wide.apply(pd.to_numeric, errors="coerce")
    wide = wide.dropna(axis=1, how="all")
    if not isinstance(wide.index, pd.DatetimeIndex):
        wide.index = pd.to_datetime(wide.index, errors="coerce")
    wide = wide[~wide.index.isna()].sort_index()
    if wide.empty:
        raise ValueError("Historical price CSV does not contain portfolio tickers")
    return wide


def _date_bounds(prices: pd.DataFrame) -> tuple[str | None, str | None]:
    if prices.empty:
        return None, None
    index = pd.to_datetime(prices.index, errors="coerce")
    index = index[~index.isna()]
    if len(index) == 0:
        return None, None
    return index.min().date().isoformat(), index.max().date().isoformat()


def _weights_from_portfolio_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    values = {
        row["ticker"]: max(0.0, float(row.get("totalValue", 0.0) or 0.0))
        for row in rows
    }
    return _normalize_weights(values)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value or 0.0)) for value in weights.values())
    if total <= 0:
        return {ticker: 0.0 for ticker in weights}
    return {ticker: max(0.0, float(value or 0.0)) / total for ticker, value in weights.items()}


def _targets_to_weight_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["ticker"]: float(row.get("target_weight", 0.0) or 0.0) for row in rows}


def _sector_map_from_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(row.get("ticker", "")).upper(): str(row.get("sector", "Unknown") or "Unknown")
        for row in rows
        if row.get("ticker")
    }


def _portfolio_returns(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    if returns.empty:
        return pd.Series(dtype=float)
    cols = [ticker for ticker in weights if ticker in returns.columns]
    if not cols:
        return pd.Series(dtype=float)
    weight_series = pd.Series({ticker: weights[ticker] for ticker in cols})
    weight_series = weight_series / weight_series.sum()
    return returns[cols].fillna(0.0).mul(weight_series, axis=1).sum(axis=1)


def _turnover(current: dict[str, float], target: dict[str, float]) -> float:
    tickers = set(current) | set(target)
    return 0.5 * sum(abs(float(target.get(t, 0.0)) - float(current.get(t, 0.0))) for t in tickers)


def _risk_level_from_returns(returns: pd.Series) -> str:
    vol = calculate_annualized_volatility(returns)
    drawdown = abs(calculate_max_drawdown(returns))
    if vol > 0.35 or drawdown > 0.30:
        return "high"
    if vol > 0.18 or drawdown > 0.15:
        return "medium"
    return "low"


def _sector_exposure_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    class _Row:
        def __init__(self, row: dict[str, Any]):
            self.ticker = row.get("ticker", "")
            self.sector = row.get("sector", "Unknown")
            self.asset_type = row.get("asset_type", "equity")
            self.market = row.get("market", "Global")
            self.shares = 1.0
            self.current_price = float(row.get("totalValue", 0.0) or 0.0)

    return _sector_from_weights([_Row(row) for row in rows])


def _sector_from_weights(rows: list[Any]) -> dict[str, dict[str, float | int]]:
    values = {row.ticker: row.current_price for row in rows}
    total = sum(values.values())
    sectors: dict[str, dict[str, float | int]] = {}
    for row in rows:
        bucket = sectors.setdefault(row.sector or "Unknown", {"value": 0.0, "weight": 0.0, "count": 0})
        bucket["value"] = float(bucket["value"]) + row.current_price
        bucket["count"] = int(bucket["count"]) + 1
    for data in sectors.values():
        data["weight"] = float(data["value"]) / total if total > 0 else 0.0
    return sectors
