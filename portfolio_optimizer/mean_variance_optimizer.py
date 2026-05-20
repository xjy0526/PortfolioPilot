"""Mean-variance portfolio optimization utilities.

The optimizers in this module are intentionally lightweight research helpers:
they use numpy/pandas only, enforce long-only concentration constraints, and
return explainable target-weight rows that can be shown in the dashboard.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd


_EPS = 1e-12
_DEFAULT_VARIANCE = 0.20**2


def minimum_variance_portfolio(
    current_weights: Mapping[str, float],
    expected_returns: Mapping[str, float] | pd.Series | None,
    covariance: pd.DataFrame | np.ndarray | Mapping[str, Mapping[str, float]] | None,
    *,
    max_weight: float = 0.35,
    sector_map: Mapping[str, str] | None = None,
    sector_max_weight: float | Mapping[str, float] | None = None,
    max_iterations: int = 750,
) -> list[dict[str, Any]]:
    """Build a constrained minimum-variance portfolio.

    Args:
        current_weights: Current portfolio weights by ticker.
        expected_returns: Annualized expected returns by ticker. Used for
            reporting the portfolio-level expected return.
        covariance: Annualized return covariance matrix.
        max_weight: Maximum target weight for any single asset.
        sector_map: Optional ticker-to-sector mapping.
        sector_max_weight: Optional global sector cap or per-sector cap map.
        max_iterations: Projected-gradient iterations.

    Returns:
        A list of explainable target-weight rows.
    """
    context = _prepare_context(
        current_weights=current_weights,
        expected_returns=expected_returns,
        covariance=covariance,
        max_weight=max_weight,
        sector_map=sector_map,
        sector_max_weight=sector_max_weight,
    )
    if context["n"] == 0:
        return []

    initial = _initial_minimum_variance_weights(context["covariance"])
    weights = _project_weights(
        initial,
        asset_cap=context["asset_cap"],
        sector_labels=context["sector_labels"],
        sector_caps=context["sector_caps"],
        preference=initial,
    )

    covariance_matrix = context["covariance"]
    step = _gradient_step_size(covariance_matrix, risk_aversion=1.0)
    for _ in range(max(1, int(max_iterations))):
        gradient = 2.0 * covariance_matrix @ weights
        next_weights = _project_weights(
            weights - step * gradient,
            asset_cap=context["asset_cap"],
            sector_labels=context["sector_labels"],
            sector_caps=context["sector_caps"],
            preference=initial,
        )
        if np.linalg.norm(next_weights - weights, ord=1) < 1e-10:
            weights = next_weights
            break
        weights = next_weights

    return _format_result_rows(
        method="minimum_variance_portfolio",
        tickers=context["tickers"],
        current_weights=context["current_weights"],
        target_weights=weights,
        expected_returns=context["expected_returns"],
        covariance=covariance_matrix,
        asset_cap=context["asset_cap"],
        requested_asset_cap=context["requested_asset_cap"],
        sector_caps=context["sector_caps"],
        constraint_notes=context["constraint_notes"],
        objective_note="Minimizes portfolio variance under long-only and concentration constraints.",
    )


def mean_variance_portfolio(
    current_weights: Mapping[str, float],
    expected_returns: Mapping[str, float] | pd.Series | None,
    covariance: pd.DataFrame | np.ndarray | Mapping[str, Mapping[str, float]] | None,
    *,
    risk_aversion: float = 5.0,
    max_weight: float = 0.35,
    sector_map: Mapping[str, str] | None = None,
    sector_max_weight: float | Mapping[str, float] | None = None,
    max_iterations: int = 1000,
) -> list[dict[str, Any]]:
    """Build a constrained mean-variance portfolio.

    The objective is a simple research demo:
    ``risk_aversion * portfolio_variance - expected_return``.
    """
    context = _prepare_context(
        current_weights=current_weights,
        expected_returns=expected_returns,
        covariance=covariance,
        max_weight=max_weight,
        sector_map=sector_map,
        sector_max_weight=sector_max_weight,
    )
    if context["n"] == 0:
        return []

    risk_aversion = max(0.01, _safe_float(risk_aversion, 5.0))
    covariance_matrix = context["covariance"]
    expected_return_vector = context["expected_returns"]

    min_var_initial = _initial_minimum_variance_weights(covariance_matrix)
    return_tilt = np.maximum(expected_return_vector - np.nanmin(expected_return_vector), 0.0)
    if float(return_tilt.sum()) > _EPS:
        return_tilt = return_tilt / float(return_tilt.sum())
        initial = 0.65 * min_var_initial + 0.35 * return_tilt
    else:
        initial = min_var_initial

    weights = _project_weights(
        initial,
        asset_cap=context["asset_cap"],
        sector_labels=context["sector_labels"],
        sector_caps=context["sector_caps"],
        preference=initial,
    )

    step = _gradient_step_size(covariance_matrix, risk_aversion=risk_aversion)
    for _ in range(max(1, int(max_iterations))):
        gradient = 2.0 * risk_aversion * covariance_matrix @ weights - expected_return_vector
        next_weights = _project_weights(
            weights - step * gradient,
            asset_cap=context["asset_cap"],
            sector_labels=context["sector_labels"],
            sector_caps=context["sector_caps"],
            preference=initial,
        )
        if np.linalg.norm(next_weights - weights, ord=1) < 1e-10:
            weights = next_weights
            break
        weights = next_weights

    return _format_result_rows(
        method="mean_variance_portfolio",
        tickers=context["tickers"],
        current_weights=context["current_weights"],
        target_weights=weights,
        expected_returns=expected_return_vector,
        covariance=covariance_matrix,
        asset_cap=context["asset_cap"],
        requested_asset_cap=context["requested_asset_cap"],
        sector_caps=context["sector_caps"],
        constraint_notes=context["constraint_notes"],
        objective_note=(
            "Maximizes a mean-variance research objective under long-only and "
            f"concentration constraints with risk_aversion={risk_aversion:.2f}."
        ),
    )


def _prepare_context(
    current_weights: Mapping[str, float],
    expected_returns: Mapping[str, float] | pd.Series | None,
    covariance: pd.DataFrame | np.ndarray | Mapping[str, Mapping[str, float]] | None,
    max_weight: float,
    sector_map: Mapping[str, str] | None,
    sector_max_weight: float | Mapping[str, float] | None,
) -> dict[str, Any]:
    tickers = _valid_tickers(current_weights)
    n_assets = len(tickers)
    if n_assets == 0:
        return {"n": 0}

    normalized_current = _normalize_current_weights(current_weights, tickers)
    expected_return_vector = _expected_return_vector(expected_returns, tickers)
    covariance_matrix = _covariance_matrix(covariance, tickers)
    requested_asset_cap = _safe_float(max_weight, 1.0)
    asset_cap = min(1.0, max(requested_asset_cap, 1.0 / n_assets))

    constraint_notes: list[str] = []
    if requested_asset_cap < 1.0 / n_assets:
        constraint_notes.append(
            f"Requested max_weight {requested_asset_cap:.1%} was relaxed to {asset_cap:.1%} for feasibility."
        )

    sector_labels = _sector_labels(tickers, sector_map)
    sector_caps, sector_notes = _sector_caps(
        sector_labels=sector_labels,
        sector_max_weight=sector_max_weight,
        asset_cap=asset_cap,
    )
    constraint_notes.extend(sector_notes)

    return {
        "n": n_assets,
        "tickers": tickers,
        "current_weights": normalized_current,
        "expected_returns": expected_return_vector,
        "covariance": covariance_matrix,
        "requested_asset_cap": requested_asset_cap,
        "asset_cap": asset_cap,
        "sector_labels": sector_labels,
        "sector_caps": sector_caps,
        "constraint_notes": constraint_notes,
    }


def _valid_tickers(weights: Mapping[str, float]) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for raw_ticker, raw_weight in weights.items():
        ticker = str(raw_ticker).strip().upper()
        if not ticker or ticker == "CASH" or ticker in seen:
            continue
        if _safe_float(raw_weight, 0.0) < 0:
            continue
        tickers.append(ticker)
        seen.add(ticker)
    return tickers


def _normalize_current_weights(weights: Mapping[str, float], tickers: list[str]) -> np.ndarray:
    raw = np.array([max(0.0, _safe_float(weights.get(ticker, 0.0), 0.0)) for ticker in tickers], dtype=float)
    total = float(raw.sum())
    if total <= _EPS:
        return np.full(len(tickers), 1.0 / len(tickers), dtype=float)
    return raw / total


def _expected_return_vector(
    expected_returns: Mapping[str, float] | pd.Series | None,
    tickers: list[str],
) -> np.ndarray:
    if expected_returns is None:
        return np.zeros(len(tickers), dtype=float)

    if isinstance(expected_returns, pd.Series):
        mapping = {
            str(index).strip().upper(): _safe_float(value, 0.0)
            for index, value in expected_returns.items()
        }
    else:
        mapping = {
            str(index).strip().upper(): _safe_float(value, 0.0)
            for index, value in expected_returns.items()
        }
    values = np.array([mapping.get(ticker, 0.0) for ticker in tickers], dtype=float)
    return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)


def _covariance_matrix(
    covariance: pd.DataFrame | np.ndarray | Mapping[str, Mapping[str, float]] | None,
    tickers: list[str],
) -> np.ndarray:
    n_assets = len(tickers)
    if covariance is None:
        matrix = np.eye(n_assets, dtype=float) * _DEFAULT_VARIANCE
    elif isinstance(covariance, pd.DataFrame):
        frame = covariance.copy()
        frame.index = [str(index).strip().upper() for index in frame.index]
        frame.columns = [str(column).strip().upper() for column in frame.columns]
        frame = frame.reindex(index=tickers, columns=tickers)
        matrix = frame.to_numpy(dtype=float)
    elif isinstance(covariance, np.ndarray):
        matrix = np.array(covariance, dtype=float)
        if matrix.shape != (n_assets, n_assets):
            matrix = np.eye(n_assets, dtype=float) * _DEFAULT_VARIANCE
    else:
        frame = pd.DataFrame(covariance, dtype=float)
        frame.index = [str(index).strip().upper() for index in frame.index]
        frame.columns = [str(column).strip().upper() for column in frame.columns]
        frame = frame.reindex(index=tickers, columns=tickers)
        matrix = frame.to_numpy(dtype=float)

    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    matrix = 0.5 * (matrix + matrix.T)
    diagonal = np.diag(matrix).copy()
    positive_diagonal = diagonal[diagonal > _EPS]
    fallback_variance = float(np.median(positive_diagonal)) if len(positive_diagonal) else _DEFAULT_VARIANCE
    for idx, value in enumerate(diagonal):
        if value <= _EPS:
            matrix[idx, idx] = fallback_variance

    try:
        min_eigenvalue = float(np.min(np.linalg.eigvalsh(matrix)))
    except np.linalg.LinAlgError:
        min_eigenvalue = 0.0
    if min_eigenvalue < _EPS:
        matrix = matrix + np.eye(n_assets, dtype=float) * (abs(min_eigenvalue) + 1e-8)
    return matrix


def _sector_labels(tickers: list[str], sector_map: Mapping[str, str] | None) -> list[str]:
    if not sector_map:
        return ["Unknown" for _ in tickers]
    normalized = {
        str(ticker).strip().upper(): str(sector or "Unknown").strip() or "Unknown"
        for ticker, sector in sector_map.items()
    }
    return [normalized.get(ticker, "Unknown") for ticker in tickers]


def _sector_caps(
    sector_labels: list[str],
    sector_max_weight: float | Mapping[str, float] | None,
    asset_cap: float,
) -> tuple[dict[str, float], list[str]]:
    if sector_max_weight is None:
        return {}, []

    sectors = sorted(set(sector_labels))
    counts = {sector: sector_labels.count(sector) for sector in sectors}
    if isinstance(sector_max_weight, Mapping):
        raw_caps = {
            str(sector).strip(): min(1.0, max(0.0, _safe_float(cap, 1.0)))
            for sector, cap in sector_max_weight.items()
        }
        caps = {sector: raw_caps.get(sector, 1.0) for sector in sectors}
    else:
        cap = min(1.0, max(0.0, _safe_float(sector_max_weight, 1.0)))
        caps = {sector: cap for sector in sectors}

    notes: list[str] = []
    max_sector_capacity = {sector: min(1.0, asset_cap * counts[sector]) for sector in sectors}
    caps = {sector: min(caps[sector], max_sector_capacity[sector]) for sector in sectors}

    total_capacity = sum(caps.values())
    if total_capacity < 1.0 - 1e-9:
        remaining_need = 1.0 - total_capacity
        for sector in sectors:
            room = max(0.0, max_sector_capacity[sector] - caps[sector])
            if room <= 0:
                continue
            add = min(room, remaining_need)
            caps[sector] += add
            remaining_need -= add
            if remaining_need <= 1e-9:
                break
        notes.append("Sector cap was relaxed where necessary so the allocation remains feasible.")

    return caps, notes


def _initial_minimum_variance_weights(covariance: np.ndarray) -> np.ndarray:
    n_assets = covariance.shape[0]
    ones = np.ones(n_assets, dtype=float)
    try:
        raw = np.linalg.pinv(covariance) @ ones
        raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
        raw = np.maximum(raw, 0.0)
        if float(raw.sum()) > _EPS:
            return raw / float(raw.sum())
    except np.linalg.LinAlgError:
        pass
    inverse_variance = 1.0 / np.maximum(np.diag(covariance), _EPS)
    return inverse_variance / float(inverse_variance.sum())


def _gradient_step_size(covariance: np.ndarray, risk_aversion: float) -> float:
    try:
        largest = float(np.max(np.linalg.eigvalsh(covariance)))
    except np.linalg.LinAlgError:
        largest = float(np.max(np.diag(covariance)))
    lipschitz = max(2.0 * max(risk_aversion, 0.01) * largest, 1e-6)
    return min(1.0, 1.0 / lipschitz)


def _project_weights(
    raw_weights: np.ndarray,
    *,
    asset_cap: float,
    sector_labels: list[str],
    sector_caps: dict[str, float],
    preference: np.ndarray | None = None,
) -> np.ndarray:
    n_assets = len(raw_weights)
    if n_assets == 0:
        return raw_weights

    weights = np.nan_to_num(np.array(raw_weights, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    weights = np.maximum(weights, 0.0)
    if float(weights.sum()) <= _EPS:
        weights = np.full(n_assets, 1.0 / n_assets, dtype=float)
    else:
        weights = weights / float(weights.sum())

    preference_vector = np.array(preference if preference is not None else weights, dtype=float)
    preference_vector = np.nan_to_num(preference_vector, nan=0.0, posinf=0.0, neginf=0.0)
    preference_vector = np.maximum(preference_vector, _EPS)

    for _ in range(40):
        previous = weights.copy()
        weights = np.clip(weights, 0.0, asset_cap)

        for sector, cap in sector_caps.items():
            indices = [idx for idx, label in enumerate(sector_labels) if label == sector]
            if not indices:
                continue
            sector_total = float(weights[indices].sum())
            if sector_total > cap + 1e-12 and sector_total > _EPS:
                weights[indices] *= cap / sector_total

        deficit = 1.0 - float(weights.sum())
        if deficit > 1e-10:
            weights = _redistribute_deficit(
                weights,
                deficit=deficit,
                asset_cap=asset_cap,
                sector_labels=sector_labels,
                sector_caps=sector_caps,
                preference=preference_vector,
            )
        elif deficit < -1e-10:
            weights = weights / float(weights.sum())

        if abs(float(weights.sum()) - 1.0) < 1e-9 and np.linalg.norm(weights - previous, ord=1) < 1e-9:
            break

    total = float(weights.sum())
    if total <= _EPS:
        weights = np.full(n_assets, 1.0 / n_assets, dtype=float)
    else:
        weights = weights / total

    weights = np.maximum(weights, 0.0)
    weights[weights < 1e-12] = 0.0
    return weights / float(weights.sum())


def _redistribute_deficit(
    weights: np.ndarray,
    *,
    deficit: float,
    asset_cap: float,
    sector_labels: list[str],
    sector_caps: dict[str, float],
    preference: np.ndarray,
) -> np.ndarray:
    result = weights.copy()
    for _ in range(len(result) * 4 + 4):
        capacity = _remaining_capacity(
            result,
            asset_cap=asset_cap,
            sector_labels=sector_labels,
            sector_caps=sector_caps,
        )
        capacity = np.maximum(capacity, 0.0)
        available = float(capacity.sum())
        if deficit <= 1e-10 or available <= 1e-12:
            break

        weighted_preference = preference * (capacity > 1e-12)
        if float(weighted_preference.sum()) <= _EPS:
            share = capacity / available
        else:
            share = weighted_preference / float(weighted_preference.sum())
        addition = np.minimum(capacity, deficit * share)
        added = float(addition.sum())
        if added <= 1e-12:
            addition = np.minimum(capacity, deficit * capacity / available)
            added = float(addition.sum())
        result += addition
        deficit -= added
    return result


def _remaining_capacity(
    weights: np.ndarray,
    *,
    asset_cap: float,
    sector_labels: list[str],
    sector_caps: dict[str, float],
) -> np.ndarray:
    asset_capacity = np.maximum(asset_cap - weights, 0.0)
    if not sector_caps:
        return asset_capacity

    result = np.zeros_like(weights)
    for idx, label in enumerate(sector_labels):
        sector_cap = sector_caps.get(label, 1.0)
        sector_total = float(weights[[i for i, sector in enumerate(sector_labels) if sector == label]].sum())
        sector_capacity = max(0.0, sector_cap - sector_total)
        result[idx] = min(asset_capacity[idx], sector_capacity)
    return result


def _format_result_rows(
    *,
    method: str,
    tickers: list[str],
    current_weights: np.ndarray,
    target_weights: np.ndarray,
    expected_returns: np.ndarray,
    covariance: np.ndarray,
    asset_cap: float,
    requested_asset_cap: float,
    sector_caps: dict[str, float],
    constraint_notes: list[str],
    objective_note: str,
) -> list[dict[str, Any]]:
    portfolio_return = float(target_weights @ expected_returns)
    portfolio_variance = float(target_weights @ covariance @ target_weights)
    portfolio_volatility = float(np.sqrt(max(0.0, portfolio_variance)))
    asset_volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))

    constraint_parts = [
        "weights sum to 1",
        "long-only",
        f"single-asset cap {asset_cap:.1%}",
    ]
    if requested_asset_cap < asset_cap:
        constraint_parts.append(f"requested cap relaxed from {requested_asset_cap:.1%}")
    if sector_caps:
        constraint_parts.append(
            "sector caps "
            + ", ".join(f"{sector}: {cap:.1%}" for sector, cap in sorted(sector_caps.items()))
        )
    if constraint_notes:
        constraint_parts.extend(constraint_notes)

    rows: list[dict[str, Any]] = []
    for idx, ticker in enumerate(tickers):
        current = float(current_weights[idx])
        target = float(target_weights[idx])
        change = target - current
        direction = "increases" if change > 1e-6 else "reduces" if change < -1e-6 else "keeps"
        reason = (
            f"{objective_note} Target {direction} exposure based on annualized return "
            f"{expected_returns[idx]:.1%}, volatility {asset_volatility[idx]:.1%}; "
            + "; ".join(constraint_parts)
            + ". Research demonstration only, not investment advice."
        )
        rows.append({
            "ticker": ticker,
            "current_weight": round(current, 6),
            "target_weight": round(target, 6),
            "weight_change": round(change, 6),
            "expected_return": round(portfolio_return, 6),
            "expected_volatility": round(portfolio_volatility, 6),
            "reason": reason,
            "method": method,
        })
    return rows


def _safe_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(result):
        return default
    return result
