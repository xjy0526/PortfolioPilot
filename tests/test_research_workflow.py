import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models import PortfolioPosition, PortfolioSummary, StockFullData
from services.market_data.base import PriceHistoryResult
from state import portfolio_data
from prompts.registry import PromptRegistry
from routes import workflows as workflow_routes
from workflows import research_report as workflow_module
from workflows.research_report import ALLOWLISTED_TOOLS, SHADOW_TRADING_TOOLS, ResearchReportWorkflow


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


@pytest.fixture
def workflow(monkeypatch):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    service = ResearchReportWorkflow(connection)
    monkeypatch.setitem(portfolio_data, "summary", _summary())
    monkeypatch.setattr(workflow_module, "get_price_history_service", lambda: StubHistory())
    monkeypatch.setattr(
        workflow_module,
        "retrieve_evidence_with_status",
        lambda *args, **kwargs: {
            "citations": [{
                "document_id": "public-doc", "chunk_id": "public-chunk",
                "permission_level": "public", "quote": "Public risk policy.",
            }],
            "evidence_insufficient": False,
        },
    )

    async def analyze(risk_summary, evidence, language="zh", **kwargs):
        return _draft(risk_summary, evidence)

    monkeypatch.setattr(workflow_module, "analyze_portfolio_with_llm", analyze)
    yield service
    connection.close()


@pytest.mark.asyncio
async def test_workflow_requires_human_review_then_publishes(workflow):
    result = await workflow.start({"user_id": "analyst-1", "idempotency_key": "run-once"})

    assert result["status"] == "PENDING_REVIEW"
    assert len(result["steps"]) == 9
    assert all(step["status"] == "COMPLETED" for step in result["steps"])
    assert result["report_id"] is None
    review_id = result["review_tasks"][0]["review_id"]
    PromptRegistry(workflow.conn).record_llm_call(
        trace_id="workflow-trace", run_id=result["run_id"], prompt_id="financial-analysis",
        prompt_version=1, provider="mock", model="mock", status="success",
    )

    approved = await workflow.decide(
        review_id, "approve", reviewer_id="compliance-1", feedback="Approved after review",
    )
    assert approved["status"] == "PUBLISHED"
    assert approved["report_id"]
    report = workflow.get_report(approved["report_id"])
    assert report["report"]["prompt_id"] == "financial-analysis"
    decision = workflow.conn.execute("SELECT * FROM review_decisions").fetchone()
    assert decision["feedback"] == "Approved after review"
    trace = workflow.conn.execute("SELECT review_decision, review_feedback FROM llm_call_traces").fetchone()
    assert tuple(trace) == ("approve", "Approved after review")


@pytest.mark.asyncio
async def test_workflow_is_idempotent_and_reject_does_not_publish(workflow):
    first = await workflow.start({"user_id": "analyst-1", "idempotency_key": "same"})
    second = await workflow.start({"user_id": "analyst-1", "idempotency_key": "same"})
    assert first["run_id"] == second["run_id"]

    rejected = await workflow.decide(
        first["review_tasks"][0]["review_id"], "reject",
        reviewer_id="reviewer", feedback="Citation needs improvement",
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["report_id"] is None


@pytest.mark.asyncio
async def test_request_changes_creates_new_review_iteration(workflow):
    first = await workflow.start({"user_id": "analyst-1"})
    revised = await workflow.decide(
        first["review_tasks"][0]["review_id"], "request_changes",
        reviewer_id="reviewer", feedback="Clarify concentration risk",
    )
    assert revised["status"] == "PENDING_REVIEW"
    assert revised["iteration"] == 2
    assert len(revised["review_tasks"]) == 2
    assert any(step["iteration"] == 2 for step in revised["steps"])


@pytest.mark.asyncio
async def test_workflow_limits_fail_closed(workflow):
    result = await workflow.start({"user_id": "analyst-1", "max_steps": 2})
    assert result["status"] == "FAILED"
    assert result["error_type"] == "WorkflowLimitError"
    assert result["report_id"] is None


def test_allowlist_never_contains_shadow_trading_tools():
    assert not (ALLOWLISTED_TOOLS & SHADOW_TRADING_TOOLS)


@pytest.mark.asyncio
async def test_forbidden_expression_fails_before_human_review(workflow, monkeypatch):
    async def unsafe(risk_summary, evidence, language="zh", **kwargs):
        draft = _draft(risk_summary, evidence)
        draft["portfolio_summary"] = "立即买入；仅用于研究。"
        draft["risk_score"] = risk_summary["risk_score"]
        return draft

    monkeypatch.setattr(workflow_module, "analyze_portfolio_with_llm", unsafe)
    result = await workflow.start({"user_id": "analyst-1"})
    assert result["status"] == "FAILED"
    assert result["current_step"] == "run_compliance_rules"
    assert result["review_tasks"] == []


def test_workflow_api_lifecycle(monkeypatch, workflow):
    app = FastAPI()
    app.include_router(workflow_routes.router)
    monkeypatch.setattr(workflow_routes, "get_workflow_service", lambda: workflow)
    client = TestClient(app)

    created = client.post("/api/workflows/research-report", json={"user_id": "api-analyst"})
    assert created.status_code == 201
    run = created.json()
    review_id = run["review_tasks"][0]["review_id"]
    published = client.post(
        f"/api/reviews/{review_id}/approve",
        json={"reviewer_id": "api-reviewer", "feedback": "approved"},
    ).json()
    assert published["status"] == "PUBLISHED"
    report = client.get(f"/api/reports/{published['report_id']}")
    assert report.status_code == 200
