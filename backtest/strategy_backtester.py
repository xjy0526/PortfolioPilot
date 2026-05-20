"""Backtest strategy comparison for PortfolioPilot."""
from __future__ import annotations

import json
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


@dataclass
class PriceDataBundle:
    prices: pd.DataFrame
    mock_used: bool
    data_source: str
    source_path: str | None = None


DEFAULT_PRICE_CSV = Path(__file__).resolve().parent.parent / "data" / "prices" / "example_historical_prices.csv"


def run_strategy_backtest(config: BacktestConfig) -> dict[str, Any]:
    """Run a reproducible strategy comparison backtest."""
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
    expected_returns = returns.mean().fillna(0.0) * 252 if not returns.empty else pd.Series(dtype=float)
    covariance = returns.cov().fillna(0.0) * 252 if not returns.empty else pd.DataFrame()
    sector_map = _sector_map_from_rows(portfolio_positions)

    asset_risk_metrics = {
        ticker: {
            "annual_volatility": calculate_annualized_volatility(returns[ticker]) if ticker in returns else 0.0,
            "max_drawdown": calculate_max_drawdown(returns[ticker]) if ticker in returns else 0.0,
            "risk_level": _risk_level_from_returns(returns[ticker]) if ticker in returns else "medium",
        }
        for ticker in tickers
    }

    equal_weights = _targets_to_weight_map(equal_weight_baseline(current_weights))
    risk_parity_weights = _targets_to_weight_map(risk_parity_simple(current_weights, asset_risk_metrics))
    minimum_variance_rows = minimum_variance_portfolio(
        current_weights=current_weights,
        expected_returns=expected_returns,
        covariance=covariance,
        max_weight=0.35,
        sector_map=sector_map,
        sector_max_weight=0.55,
    )
    minimum_variance_weights = _targets_to_weight_map(minimum_variance_rows)
    mean_variance_rows = mean_variance_portfolio(
        current_weights=current_weights,
        expected_returns=expected_returns,
        covariance=covariance,
        risk_aversion=5.0,
        max_weight=0.35,
        sector_map=sector_map,
        sector_max_weight=0.55,
    )
    mean_variance_weights = _targets_to_weight_map(mean_variance_rows)
    llm_adjusted = llm_risk_adjusted_weighting(
        current_weights=current_weights,
        asset_risk_metrics=asset_risk_metrics,
        llm_risk_score=6.5,
        asset_level_comments=[
            {"ticker": ticker, "risk_level": metrics["risk_level"], "comment": "Backtest-derived risk proxy"}
            for ticker, metrics in asset_risk_metrics.items()
        ],
        sector_exposure=_sector_exposure_from_rows(portfolio_positions),
    )
    llm_weights = {
        item["ticker"]: float(item["target_weight"])
        for item in llm_adjusted.get("suggestions", [])
    }

    strategies = {
        "original_portfolio": current_weights,
        "equal_weight": equal_weights,
        "risk_parity": risk_parity_weights,
        "minimum_variance": minimum_variance_weights,
        "mean_variance": mean_variance_weights,
        "llm_risk_adjusted": llm_weights,
    }

    results = []
    for name, weights in strategies.items():
        normalized = _normalize_weights({ticker: weights.get(ticker, 0.0) for ticker in tickers})
        portfolio_returns = _portfolio_returns(returns, normalized)
        results.append({
            "strategy": name,
            "annual_return": round(calculate_annual_return(portfolio_returns), 6),
            "annual_volatility": round(calculate_annualized_volatility(portfolio_returns), 6),
            "max_drawdown": round(calculate_max_drawdown(portfolio_returns), 6),
            "sharpe_ratio": round(calculate_sharpe_ratio(portfolio_returns), 4),
            "turnover": round(_turnover(current_weights, normalized), 6),
            "weights": {ticker: round(weight, 6) for ticker, weight in normalized.items()},
        })

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
        "strategies": results,
        "minimum_variance_explanations": minimum_variance_rows,
        "mean_variance_explanations": mean_variance_rows,
        "llm_adjusted_explanations": llm_adjusted.get("suggestions", []),
    }
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


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
