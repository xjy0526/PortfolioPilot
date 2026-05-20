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
    """Adjust target weights with LLM risk signals and concentration controls."""
    tickers = _valid_tickers(current_weights)
    if not tickers:
        return {"suggestions": [], "sector_warnings": [], "risk_score": llm_risk_score}
    effective_single_cap = max(max_single_weight, 1.0 / len(tickers))

    comment_map = {
        str(item.get("ticker", "")).upper(): str(item.get("risk_level", "medium")).lower()
        for item in asset_level_comments
        if isinstance(item, dict)
    }

    raw_targets: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}
    portfolio_risk = float(llm_risk_score or 5.0)
    for ticker in tickers:
        current = max(0.0, float(current_weights.get(ticker, 0.0) or 0.0))
        metrics = asset_risk_metrics.get(ticker, {}) or {}
        level = comment_map.get(ticker) or str(metrics.get("risk_level", "medium")).lower()
        multiplier = 1.0
        ticker_reasons = []

        if level == "high":
            multiplier *= 0.65
            ticker_reasons.append("High asset-level risk signal reduces target weight.")
        elif level == "medium":
            multiplier *= 0.88
            ticker_reasons.append("Medium risk signal applies a moderate weight haircut.")
        else:
            multiplier *= 1.05
            ticker_reasons.append("Low risk signal allows a small relative overweight.")

        vol = abs(float(metrics.get("annual_volatility", 0.0) or 0.0))
        if vol > 0.35:
            multiplier *= 0.80
            ticker_reasons.append("Annualized volatility is elevated.")

        if portfolio_risk >= 7 and level in {"medium", "high"}:
            multiplier *= 0.90
            ticker_reasons.append("Portfolio-level LLM risk score is high.")

        target = current * multiplier
        if current > effective_single_cap:
            target = min(target, effective_single_cap)
            ticker_reasons.append(f"Single-asset concentration is above {effective_single_cap:.0%}.")

        raw_targets[ticker] = max(0.0, target)
        reasons[ticker] = ticker_reasons

    normalized = _normalize(raw_targets)
    if not normalized:
        equal = 1.0 / len(tickers)
        normalized = {ticker: equal for ticker in tickers}

    # Apply single-name cap once more and redistribute any excess.
    capped = {ticker: min(weight, effective_single_cap) for ticker, weight in normalized.items()}
    excess = max(0.0, 1.0 - sum(capped.values()))
    if excess > 0:
        room = {ticker: max(0.0, effective_single_cap - weight) for ticker, weight in capped.items()}
        room_total = sum(room.values())
        if room_total > 0:
            for ticker in tickers:
                capped[ticker] += excess * room[ticker] / room_total
    targets = _normalize(capped)

    sector_warnings = []
    for sector, data in (sector_exposure or {}).items():
        weight = float(data.get("weight", 0.0) or 0.0)
        if weight > 0.40:
            sector_warnings.append(
                f"Sector concentration warning: {sector} is {weight:.1%}, above the 40% research threshold."
            )

    suggestions = []
    for ticker in tickers:
        current = float(current_weights.get(ticker, 0.0) or 0.0)
        target = targets.get(ticker, 0.0)
        change = target - current
        reason = " ".join(reasons.get(ticker) or ["No material risk adjustment."])
        suggestions.append({
            "ticker": ticker,
            "current_weight": round(current, 6),
            "target_weight": round(target, 6),
            "weight_change": round(change, 6),
            "reason": reason,
        })

    suggestions.sort(key=lambda item: abs(float(item["weight_change"])), reverse=True)
    return {
        "risk_score": round(portfolio_risk, 2),
        "suggestions": suggestions,
        "sector_warnings": sector_warnings,
        "method": "llm_risk_adjusted_weighting",
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
