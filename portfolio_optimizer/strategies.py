"""Explainable portfolio weighting strategies."""
from __future__ import annotations

from typing import Any


def equal_weight_baseline(current_weights: dict[str, float]) -> list[dict[str, Any]]:
    """Return equal target weights for all non-zero assets."""
    tickers = _valid_tickers(current_weights)
    if not tickers:
        return []
    target = 1.0 / len(tickers)
    return [
        {
            "ticker": ticker,
            "current_weight": round(float(current_weights.get(ticker, 0.0)), 6),
            "target_weight": round(target, 6),
            "weight_change": round(target - float(current_weights.get(ticker, 0.0)), 6),
            "reason": "Equal-weight baseline allocates the same target weight to each asset.",
        }
        for ticker in tickers
    ]


def risk_parity_simple(
    current_weights: dict[str, float],
    asset_risk_metrics: dict[str, dict[str, Any]],
    volatility_floor: float = 0.05,
) -> list[dict[str, Any]]:
    """Simple inverse-volatility risk parity weighting."""
    tickers = _valid_tickers(current_weights)
    if not tickers:
        return []

    inv_vol: dict[str, float] = {}
    for ticker in tickers:
        metrics = asset_risk_metrics.get(ticker, {}) or {}
        vol = abs(float(metrics.get("annual_volatility", 0.0) or 0.0))
        if vol <= 0:
            vol = volatility_floor
        inv_vol[ticker] = 1.0 / max(vol, volatility_floor)

    total = sum(inv_vol.values())
    if total <= 0:
        return equal_weight_baseline(current_weights)

    results = []
    for ticker in tickers:
        target = inv_vol[ticker] / total
        current = float(current_weights.get(ticker, 0.0) or 0.0)
        results.append({
            "ticker": ticker,
            "current_weight": round(current, 6),
            "target_weight": round(target, 6),
            "weight_change": round(target - current, 6),
            "reason": "Inverse-volatility risk parity gives lower-volatility assets larger target weights.",
        })
    return results


def llm_risk_adjusted_weighting(
    current_weights: dict[str, float],
    asset_risk_metrics: dict[str, dict[str, Any]],
    llm_risk_score: float,
    asset_level_comments: list[dict[str, Any]],
    sector_exposure: dict[str, dict[str, Any]] | None = None,
    max_single_weight: float = 0.25,
) -> dict[str, Any]:
    """Deprecated wrapper whose allocation is now deterministic risk parity."""
    tickers = _valid_tickers(current_weights)
    if not tickers:
        return {
            "suggestions": [],
            "sector_warnings": [],
            "risk_score": llm_risk_score,
            "method": "deterministic_risk_parity_compat",
            "deprecated": True,
        }
    del asset_level_comments, max_single_weight
    suggestions = risk_parity_simple(current_weights, asset_risk_metrics)
    for item in suggestions:
        item["reason"] += " LLM inputs are explanatory only and do not affect this weight."

    sector_warnings = []
    for sector, data in (sector_exposure or {}).items():
        weight = float(data.get("weight", 0.0) or 0.0)
        if weight > 0.40:
            sector_warnings.append(
                f"Sector concentration warning: {sector} is {weight:.1%}, above the 40% research threshold."
            )

    suggestions.sort(key=lambda item: abs(float(item["weight_change"])), reverse=True)
    return {
        "risk_score": round(float(llm_risk_score or 5.0), 2),
        "suggestions": suggestions,
        "sector_warnings": sector_warnings,
        "method": "deterministic_risk_parity_compat",
        "target_weight_owner": "deterministic_optimizer",
        "deprecated": True,
    }


def _valid_tickers(weights: dict[str, float]) -> list[str]:
    return [
        str(ticker).upper()
        for ticker, weight in weights.items()
        if str(ticker).upper() != "CASH" and float(weight or 0.0) >= 0
    ]


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value or 0.0)) for value in weights.values())
    if total <= 0:
        return {}
    return {ticker: max(0.0, float(value or 0.0)) / total for ticker, value in weights.items()}
