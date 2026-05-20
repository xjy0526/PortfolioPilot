"""Risk and allocation metrics for mixed-asset portfolios.

The functions in this module accept either project Pydantic models or plain
dict-like objects, which keeps the analytics layer reusable in API endpoints,
backtests and unit tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DEFAULT_RISK_FREE_RATE = 0.02


def sanitize_price_frame(price_data: pd.DataFrame | dict[str, list[float]] | None) -> pd.DataFrame:
    """Return a numeric, positive-only price frame with forward-filled gaps."""
    if price_data is None:
        return pd.DataFrame()

    frame = pd.DataFrame(price_data).copy()
    if frame.empty:
        return pd.DataFrame()

    if "date" in frame.columns:
        frame = frame.set_index("date")

    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame.where(frame > 0)
    frame = frame.ffill().bfill()
    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return frame


def calculate_returns(price_data: pd.DataFrame | dict[str, list[float]] | None) -> pd.DataFrame:
    """Calculate daily percentage returns from historical prices."""
    prices = sanitize_price_frame(price_data)
    if prices.shape[0] < 2:
        return pd.DataFrame(index=prices.index)
    returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    return returns.dropna(axis=0, how="all")


def calculate_cumulative_return(returns: pd.Series | pd.DataFrame) -> float:
    """Calculate period cumulative return from a return series."""
    if returns is None or len(returns) == 0:
        return 0.0
    clean = pd.Series(returns).dropna()
    if clean.empty:
        return 0.0
    value = float((1.0 + clean).prod() - 1.0)
    return 0.0 if not np.isfinite(value) else value


def calculate_annual_return(returns: pd.Series | pd.DataFrame, trading_days: int = TRADING_DAYS) -> float:
    """Calculate annualized return using geometric compounding."""
    clean = pd.Series(returns).dropna()
    if clean.empty:
        return 0.0
    cumulative = (1.0 + clean).prod()
    if cumulative <= 0:
        return -1.0
    annual = float(cumulative ** (trading_days / len(clean)) - 1.0)
    return 0.0 if not np.isfinite(annual) else annual


def calculate_annualized_volatility(
    returns: pd.Series | pd.DataFrame,
    trading_days: int = TRADING_DAYS,
) -> float:
    """Calculate annualized volatility from daily returns."""
    clean = pd.Series(returns).dropna()
    if len(clean) < 2:
        return 0.0
    vol = float(clean.std(ddof=0) * np.sqrt(trading_days))
    return 0.0 if not np.isfinite(vol) else vol


def calculate_max_drawdown(returns: pd.Series | pd.DataFrame) -> float:
    """Calculate max drawdown as a negative decimal, e.g. -0.18 for -18%."""
    clean = pd.Series(returns).dropna()
    if clean.empty:
        return 0.0
    wealth = (1.0 + clean).cumprod()
    running_peak = wealth.cummax()
    drawdowns = wealth / running_peak - 1.0
    value = float(drawdowns.min())
    return 0.0 if not np.isfinite(value) else value


def calculate_sharpe_ratio(
    returns: pd.Series | pd.DataFrame,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = TRADING_DAYS,
) -> float:
    """Calculate annualized Sharpe ratio."""
    clean = pd.Series(returns).dropna()
    if len(clean) < 2:
        return 0.0
    annual_return = calculate_annual_return(clean, trading_days=trading_days)
    annual_vol = calculate_annualized_volatility(clean, trading_days=trading_days)
    if annual_vol <= 0:
        return 0.0
    sharpe = float((annual_return - risk_free_rate) / annual_vol)
    return 0.0 if not np.isfinite(sharpe) else sharpe


def calculate_asset_weights(positions: list[Any]) -> dict[str, float]:
    """Calculate asset weights from current market value."""
    values = {
        _ticker(item): max(0.0, _current_value(item))
        for item in positions
        if _ticker(item)
    }
    total = sum(values.values())
    if total <= 0:
        return {ticker: 0.0 for ticker in values}
    return {ticker: value / total for ticker, value in values.items()}


def calculate_sector_concentration(positions: list[Any]) -> dict[str, dict[str, float | int]]:
    """Aggregate portfolio value by sector."""
    total = sum(max(0.0, _current_value(item)) for item in positions)
    sectors: dict[str, dict[str, float | int]] = {}
    for item in positions:
        sector = _field(item, "sector", "Unknown") or "Unknown"
        value = max(0.0, _current_value(item))
        bucket = sectors.setdefault(sector, {"value": 0.0, "weight": 0.0, "count": 0})
        bucket["value"] = float(bucket["value"]) + value
        bucket["count"] = int(bucket["count"]) + 1

    for bucket in sectors.values():
        bucket["weight"] = (float(bucket["value"]) / total) if total > 0 else 0.0
        bucket["value"] = round(float(bucket["value"]), 2)
    return dict(sorted(sectors.items(), key=lambda kv: float(kv[1]["weight"]), reverse=True))


def calculate_asset_type_exposure(positions: list[Any]) -> dict[str, dict[str, float | int]]:
    """Aggregate portfolio value by normalized asset type."""
    total = sum(max(0.0, _current_value(item)) for item in positions)
    exposure: dict[str, dict[str, float | int]] = {}
    for item in positions:
        label = _asset_type_label(item)
        value = max(0.0, _current_value(item))
        bucket = exposure.setdefault(label, {"value": 0.0, "weight": 0.0, "count": 0})
        bucket["value"] = float(bucket["value"]) + value
        bucket["count"] = int(bucket["count"]) + 1

    for bucket in exposure.values():
        bucket["weight"] = (float(bucket["value"]) / total) if total > 0 else 0.0
        bucket["value"] = round(float(bucket["value"]), 2)
    return dict(sorted(exposure.items(), key=lambda kv: float(kv[1]["weight"]), reverse=True))


def calculate_portfolio_returns(
    returns: pd.DataFrame,
    weights: dict[str, float],
) -> pd.Series:
    """Calculate weighted portfolio returns from asset returns and weights."""
    if returns.empty or not weights:
        return pd.Series(dtype=float)

    aligned = returns[[col for col in returns.columns if col in weights]].copy()
    if aligned.empty:
        return pd.Series(dtype=float)

    weight_series = pd.Series({col: weights.get(col, 0.0) for col in aligned.columns})
    total_weight = float(weight_series.sum())
    if total_weight <= 0:
        return pd.Series(dtype=float)
    weight_series = weight_series / total_weight
    return aligned.fillna(0.0).mul(weight_series, axis=1).sum(axis=1)


def build_portfolio_risk_summary(
    positions: list[Any],
    price_data: pd.DataFrame | dict[str, list[float]] | None = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> dict[str, Any]:
    """Build a structured risk summary for API responses and LLM prompts."""
    non_empty_positions = [item for item in positions if _ticker(item)]
    weights = calculate_asset_weights(non_empty_positions)
    returns = calculate_returns(price_data)
    portfolio_returns = calculate_portfolio_returns(returns, weights)

    asset_metrics: dict[str, dict[str, float | str]] = {}
    for item in non_empty_positions:
        ticker = _ticker(item)
        if not ticker:
            continue
        asset_returns = returns[ticker].dropna() if ticker in returns.columns else pd.Series(dtype=float)
        realized_return = calculate_cumulative_return(asset_returns)
        if asset_returns.empty:
            realized_return = _position_return(item)
        ann_vol = calculate_annualized_volatility(asset_returns)
        max_dd = calculate_max_drawdown(asset_returns)
        sharpe = calculate_sharpe_ratio(asset_returns, risk_free_rate=risk_free_rate)
        risk_level = _risk_level(ann_vol, max_dd, weights.get(ticker, 0.0), _field(item, "asset_type", "equity"))
        asset_metrics[ticker] = {
            "ticker": ticker,
            "return": round(realized_return, 6),
            "annual_volatility": round(ann_vol, 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe_ratio": round(sharpe, 4),
            "weight": round(weights.get(ticker, 0.0), 6),
            "risk_level": risk_level,
            "sector": _field(item, "sector", "Unknown") or "Unknown",
            "asset_type": _asset_type_label(item),
        }

    sector_concentration = calculate_sector_concentration(non_empty_positions)
    asset_type_exposure = calculate_asset_type_exposure(non_empty_positions)
    portfolio_metrics = {
        "period_return": round(calculate_cumulative_return(portfolio_returns), 6),
        "annual_return": round(calculate_annual_return(portfolio_returns), 6),
        "annual_volatility": round(calculate_annualized_volatility(portfolio_returns), 6),
        "max_drawdown": round(calculate_max_drawdown(portfolio_returns), 6),
        "sharpe_ratio": round(
            calculate_sharpe_ratio(portfolio_returns, risk_free_rate=risk_free_rate),
            4,
        ),
    }

    concentration_flags = _concentration_flags(weights, sector_concentration, asset_type_exposure)
    risk_score = _portfolio_risk_score(portfolio_metrics, concentration_flags)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_value": round(sum(max(0.0, _current_value(item)) for item in non_empty_positions), 2),
        "portfolio_metrics": portfolio_metrics,
        "risk_score": risk_score,
        "risk_level": "low" if risk_score <= 3 else "medium" if risk_score <= 6 else "high",
        "asset_weights": {ticker: round(weight, 6) for ticker, weight in weights.items()},
        "sector_concentration": sector_concentration,
        "asset_type_exposure": asset_type_exposure,
        "asset_metrics": asset_metrics,
        "concentration_flags": concentration_flags,
        "data_quality": {
            "positions": len(non_empty_positions),
            "price_history_assets": int(len(returns.columns)) if not returns.empty else 0,
            "price_history_points": int(len(returns)) if not returns.empty else 0,
            "uses_position_return_fallback": bool(returns.empty),
        },
    }


def _portfolio_risk_score(metrics: dict[str, float], flags: list[str]) -> float:
    score = 2.0
    vol = abs(metrics.get("annual_volatility", 0.0))
    drawdown = abs(metrics.get("max_drawdown", 0.0))
    sharpe = metrics.get("sharpe_ratio", 0.0)

    if vol > 0.35:
        score += 3.0
    elif vol > 0.22:
        score += 2.0
    elif vol > 0.12:
        score += 1.0

    if drawdown > 0.30:
        score += 3.0
    elif drawdown > 0.18:
        score += 2.0
    elif drawdown > 0.08:
        score += 1.0

    if sharpe < 0:
        score += 1.0
    score += min(2.0, len(flags) * 0.7)
    return round(max(1.0, min(10.0, score)), 1)


def _concentration_flags(
    weights: dict[str, float],
    sector_concentration: dict[str, dict[str, float | int]],
    asset_type_exposure: dict[str, dict[str, float | int]],
) -> list[str]:
    flags: list[str] = []
    for ticker, weight in weights.items():
        if weight > 0.25:
            flags.append(f"single_asset:{ticker}:{weight:.1%}")
    for sector, data in sector_concentration.items():
        if float(data["weight"]) > 0.40:
            flags.append(f"sector:{sector}:{float(data['weight']):.1%}")
    for asset_type, data in asset_type_exposure.items():
        if asset_type == "Prediction Market" and float(data["weight"]) > 0.10:
            flags.append(f"prediction_market:{float(data['weight']):.1%}")
    return flags


def _risk_level(vol: float, max_dd: float, weight: float, asset_type: str) -> str:
    if asset_type == "prediction_market" or vol > 0.35 or abs(max_dd) > 0.30 or weight > 0.25:
        return "high"
    if vol > 0.18 or abs(max_dd) > 0.15 or weight > 0.15:
        return "medium"
    return "low"


def _ticker(item: Any) -> str:
    value = _field(item, "ticker", "")
    return str(value or "").upper()


def _current_value(item: Any) -> float:
    if hasattr(item, "position"):
        item = getattr(item, "position")
    if isinstance(item, dict):
        if "current_value" in item:
            return _safe_float(item.get("current_value"))
        shares = _safe_float(item.get("shares"))
        price = _safe_float(item.get("current_price", item.get("currentPrice")))
        return shares * price
    current_value = getattr(item, "current_value", None)
    if current_value is not None:
        return _safe_float(current_value)
    return _safe_float(getattr(item, "shares", 0.0)) * _safe_float(getattr(item, "current_price", 0.0))


def _position_return(item: Any) -> float:
    if hasattr(item, "position"):
        item = getattr(item, "position")
    pnl_percent = getattr(item, "pnl_percent", None)
    if pnl_percent is None and isinstance(item, dict):
        pnl_percent = item.get("pnl_percent", item.get("pnlPercent"))
        if pnl_percent is None:
            buy = _safe_float(item.get("buy_price", item.get("buyPrice")))
            current = _safe_float(item.get("current_price", item.get("currentPrice")))
            if buy > 0 and current > 0:
                pnl_percent = (current / buy - 1.0) * 100
    return _safe_float(pnl_percent) / 100.0


def _asset_type_label(item: Any) -> str:
    ticker = _ticker(item)
    raw = str(_field(item, "asset_type", "") or "").lower()
    market = str(_field(item, "market", "") or "").lower()
    if ticker == "CASH" or raw == "cash":
        return "Cash"
    if raw in {"prediction_market", "polymarket"} or market == "polymarket":
        return "Prediction Market"
    if raw == "etf" or "etf" in str(_field(item, "name", "")).lower():
        return "ETF"
    if raw in {"cn_equity", "china_a", "a_share"} or ticker.endswith((".SS", ".SZ")):
        return "China A-Share"
    if market in {"us", "usa", "nasdaq", "nyse"}:
        return "US Equity"
    return "Global Equity"


def _field(item: Any, name: str, default: Any = None) -> Any:
    if hasattr(item, "position"):
        item = getattr(item, "position")
    if isinstance(item, dict):
        if name in item:
            return item.get(name, default)
        camel = "".join([name.split("_")[0], *[part.title() for part in name.split("_")[1:]]])
        return item.get(camel, default)
    return getattr(item, name, default)


def _safe_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if np.isfinite(numeric) else 0.0
