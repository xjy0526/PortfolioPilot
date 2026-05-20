import pandas as pd
import pytest

from portfolio_optimizer import (
    equal_weight_baseline,
    llm_risk_adjusted_weighting,
    mean_variance_portfolio,
    minimum_variance_portfolio,
    risk_parity_simple,
)


def test_equal_weight_baseline_outputs_explainable_rows():
    result = equal_weight_baseline({"AAPL": 0.7, "MSFT": 0.3})

    assert len(result) == 2
    assert sum(row["target_weight"] for row in result) == 1.0
    assert all(row["reason"] for row in result)


def test_risk_parity_gives_lower_vol_asset_more_weight():
    result = risk_parity_simple(
        {"LOW": 0.5, "HIGH": 0.5},
        {
            "LOW": {"annual_volatility": 0.1},
            "HIGH": {"annual_volatility": 0.4},
        },
    )
    weights = {row["ticker"]: row["target_weight"] for row in result}

    assert weights["LOW"] > weights["HIGH"]


def test_llm_risk_adjusted_weighting_reduces_high_risk_asset():
    result = llm_risk_adjusted_weighting(
        current_weights={"AAPL": 0.6, "MSFT": 0.4},
        asset_risk_metrics={
            "AAPL": {"annual_volatility": 0.45, "risk_level": "high"},
            "MSFT": {"annual_volatility": 0.15, "risk_level": "low"},
        },
        llm_risk_score=8,
        asset_level_comments=[
            {"ticker": "AAPL", "risk_level": "high", "comment": "过度集中"},
            {"ticker": "MSFT", "risk_level": "low", "comment": "较稳健"},
        ],
        sector_exposure={"Technology": {"weight": 1.0}},
    )
    rows = {row["ticker"]: row for row in result["suggestions"]}

    assert rows["AAPL"]["target_weight"] < rows["AAPL"]["current_weight"]
    assert rows["AAPL"]["reason"]
    assert result["sector_warnings"]


def test_minimum_variance_portfolio_prefers_lower_variance_asset():
    covariance = pd.DataFrame(
        [
            [0.04, 0.00, 0.00],
            [0.00, 0.09, 0.00],
            [0.00, 0.00, 0.36],
        ],
        index=["LOW", "MED", "HIGH"],
        columns=["LOW", "MED", "HIGH"],
    )

    result = minimum_variance_portfolio(
        current_weights={"LOW": 0.34, "MED": 0.33, "HIGH": 0.33},
        expected_returns={"LOW": 0.04, "MED": 0.05, "HIGH": 0.06},
        covariance=covariance,
        max_weight=0.7,
    )
    weights = {row["ticker"]: row["target_weight"] for row in result}

    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    assert weights["LOW"] > weights["MED"] > weights["HIGH"]
    assert all(row["target_weight"] >= 0 for row in result)
    assert all(row["target_weight"] <= 0.7 for row in result)
    assert {"target_weight", "weight_change", "expected_return", "expected_volatility", "reason"} <= set(result[0])


def test_mean_variance_portfolio_tilts_toward_expected_return():
    covariance = pd.DataFrame(
        0.04,
        index=["HIGH_RETURN", "MID_RETURN", "LOW_RETURN"],
        columns=["HIGH_RETURN", "MID_RETURN", "LOW_RETURN"],
    )
    covariance.values[[0, 1, 2], [0, 1, 2]] = 0.08

    result = mean_variance_portfolio(
        current_weights={"HIGH_RETURN": 1 / 3, "MID_RETURN": 1 / 3, "LOW_RETURN": 1 / 3},
        expected_returns={"HIGH_RETURN": 0.18, "MID_RETURN": 0.08, "LOW_RETURN": 0.02},
        covariance=covariance,
        risk_aversion=1.0,
        max_weight=0.8,
    )
    weights = {row["ticker"]: row["target_weight"] for row in result}

    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    assert weights["HIGH_RETURN"] > weights["LOW_RETURN"]
    assert all("not investment advice" in row["reason"] for row in result)


def test_mean_variance_portfolio_respects_sector_cap():
    covariance = pd.DataFrame(
        [
            [0.05, 0.01, 0.00, 0.00],
            [0.01, 0.05, 0.00, 0.00],
            [0.00, 0.00, 0.05, 0.01],
            [0.00, 0.00, 0.01, 0.05],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C", "D"],
    )
    result = mean_variance_portfolio(
        current_weights={"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
        expected_returns={"A": 0.30, "B": 0.25, "C": 0.02, "D": 0.02},
        covariance=covariance,
        risk_aversion=0.5,
        max_weight=0.4,
        sector_map={"A": "Tech", "B": "Tech", "C": "CashLike", "D": "CashLike"},
        sector_max_weight=0.55,
    )
    weights = {row["ticker"]: row["target_weight"] for row in result}

    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    assert weights["A"] + weights["B"] <= 0.55001
    assert all(0 <= row["target_weight"] <= 0.4 for row in result)
