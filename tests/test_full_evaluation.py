import json
import sqlite3

import pytest

import evaluation.full_eval as full_eval_module
from evaluation.full_eval import BADCASE_LABELS, run_full_evaluation
from prompts.registry import PromptRegistry
from services.financial_analysis import analyze_portfolio_with_llm
from services.llm import MockProvider


def test_full_evaluation_has_three_layers_golden_sets_and_badcase_taxonomy(tmp_path):
    output = tmp_path / "full_evaluation_report.json"
    report = run_full_evaluation(output)

    assert output.exists()
    assert set(report["layers"]) == {"retrieval", "generation", "workflow"}
    assert report["test_sets"]["portfolio_risk_case_count"] >= 20
    assert report["test_sets"]["public_retrieval_golden_count"] >= 8
    assert report["test_sets"]["includes_permission_case"] is True
    assert report["test_sets"]["includes_expired_case"] is True
    assert report["test_sets"]["includes_insufficient_evidence_case"] is True
    assert report["test_sets"]["includes_conflicting_evidence_case"] is True
    assert set(report["test_sets"]["chunk_statistics"]) == {"zh", "en"}
    assert set(report["badcase_distribution"]) == set(BADCASE_LABELS)
    assert all("expected_evidence" in case and "expected_decision" in case for case in report["layers"]["retrieval"]["cases"])


def test_workflow_metrics_initialize_a_fresh_legacy_sqlite_schema(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(full_eval_module, "_get_conn", lambda: connection)

    result = full_eval_module._workflow_metrics()

    assert result["run_count"] == 0
    assert result["metrics"]["failure_rate"] == 0.0
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"workflow_runs", "workflow_steps", "review_tasks"}.issubset(tables)
    connection.close()


@pytest.mark.asyncio
async def test_complete_trace_fields_are_recorded():
    connection = sqlite3.connect(":memory:")
    registry = PromptRegistry(connection)
    summary = {
        "risk_score": 5.0,
        "asset_metrics": {"AAPL": {"weight": 0.2, "risk_level": "medium"}},
        "concentration_flags": [],
    }
    evidence = [{"document_id": "doc-1", "chunk_id": "chunk-1", "quote": "Public evidence"}]
    response_payload = {
        "portfolio_summary": "Risk score is 5.0 out of 10.",
        "risk_score": 5.0,
        "main_risks": ["AAPL weight is 20%."],
        "asset_level_comments": [{"ticker": "AAPL", "risk_level": "medium", "comment": "AAPL weight is 20%."}],
        "rebalance_suggestions": [{"action": "hold", "ticker": "AAPL", "reason": "Human review.", "confidence": 0.5}],
        "evidence_used": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        "disclaimer": "Research only.",
    }
    await analyze_portfolio_with_llm(
        summary,
        evidence,
        language="en",
        provider=MockProvider(json.dumps(response_payload)),
        registry=registry,
        run_id="run-1",
        user_id="user-1",
        business_scene="test_scene",
    )
    trace = registry.list_traces(1)[0]
    required = {
        "run_id", "user_id", "business_scene", "prompt_id", "prompt_version", "model",
        "model_parameters", "input_hash", "retrieved_document_ids", "retrieved_chunk_ids",
        "tool_calls", "latency_ms", "input_tokens", "output_tokens", "estimated_cost",
        "output_schema_valid", "fallback_used", "error_type", "review_decision", "review_feedback",
    }
    assert required.issubset(trace)
    assert trace["run_id"] == "run-1"
    assert trace["retrieved_document_ids"] == ["doc-1"]
    assert trace["retrieved_chunk_ids"] == ["chunk-1"]
    assert trace["output_schema_valid"] == 1
    assert trace["fallback_used"] == 1
    assert trace["input_hash"]
    assert trace["input_tokens"] > 0
    assert trace["output_tokens"] > 0
    connection.close()
