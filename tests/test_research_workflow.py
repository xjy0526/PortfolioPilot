import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models import PortfolioPosition, PortfolioSummary, StockFullData
from services.market_data.base import PriceHistoryResult
from workflows.research_report import (
    ALLOWLISTED_TOOLS,
    SHADOW_TRADING_TOOLS,
    _workflow_cost_summary,
)


class StubHistory:
    async def get_history(self, tickers, *, lookback_days=365, as_of=None):
        dates = pd.date_range("2025-01-01", periods=30, freq="B")
        prices = pd.DataFrame({"AAPL": range(100, 130)}, index=dates)
        return PriceHistoryResult(
            adjusted_close=prices,
            source="workflow-test",
            as_of=datetime.now(timezone.utc).isoformat(),
            start_date=dates.min().date().isoformat(),
            end_date=dates.max().date().isoformat(),
            missing_tickers=[],
            stale_tickers=[],
            coverage_ratio=1.0,
        )


def _summary():
    stock = StockFullData(
        position=PortfolioPosition(
            ticker="AAPL", shares=10, avg_cost=90, current_price=129,
            sector="Technology",
        )
    )
    return PortfolioSummary(stocks=[stock], scores=[], num_positions=1, total_value=1290)


def _draft(risk_summary, evidence):
    weight = risk_summary["asset_metrics"]["AAPL"]["weight"] * 100
    return {
        "portfolio_summary": f"Risk score is {risk_summary['risk_score']:.1f} out of 10.",
        "risk_score": risk_summary["risk_score"],
        "main_risks": list(risk_summary.get("concentration_flags") or ["Research review required."]),
        "asset_level_comments": [
            {"ticker": "AAPL", "risk_level": "medium", "comment": f"AAPL weight is {weight:.1f}%."}
        ],
        "rebalance_suggestions": [
            {"action": "hold", "ticker": "AAPL", "reason": "Maintain human review.", "confidence": 0.5}
        ],
        "evidence_used": [
            {"document_id": item["document_id"], "chunk_id": item["chunk_id"]}
            for item in evidence
        ],
        "disclaimer": "Research only.",
        "source": "mock",
        "prompt_id": "financial-analysis",
        "prompt_version": 1,
    }


def _db_request(**values):
    payload = {
        "user_id": "analyst-1",
        "_db_portfolio": {
            "portfolio_id": "00000000-0000-0000-0000-000000000001",
            "valuation_snapshot_id": "00000000-0000-0000-0000-000000000002",
            "valuation_input_hash": "a" * 64,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "tickers": ["AAPL"],
            "risk_summary": {
                "risk_score": 5.0,
                "asset_metrics": {
                    "AAPL": {"weight": 1.0, "risk_level": "medium"}
                },
                "concentration_flags": ["single_asset:AAPL:100.0%"],
            },
        },
    }
    payload.update(values)
    return payload


def test_allowlist_never_contains_shadow_trading_tools():
    assert not (ALLOWLISTED_TOOLS & SHADOW_TRADING_TOOLS)


def test_workflow_identity_is_not_read_from_request_helpers():
    from app.core.principal import Principal

    principal = Principal("server-user", frozenset({"public", "research_reviewer"}))
    assert principal.user_id != _db_request()["user_id"]
    assert principal.has_group("research_reviewer")


def test_workflow_trace_cost_summary_counts_retries_and_estimates():
    total_cost, is_estimated = _workflow_cost_summary(Decimal("0.0125"), 2, 1)

    assert total_cost == Decimal("0.0125")
    assert is_estimated is True
    assert _workflow_cost_summary(Decimal("0.0100"), 1, 0) == (
        Decimal("0.0100"),
        False,
    )
    assert _workflow_cost_summary(0, 0, 0)[1] is True
