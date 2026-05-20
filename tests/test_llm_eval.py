import json

from evaluation.llm_eval import (
    aggregate_metrics,
    build_portfolio_risk_test_cases,
    mock_llm_response,
    run_llm_evaluation_sync,
)
from services.financial_analysis import parse_llm_json_response


def test_build_portfolio_risk_test_cases_has_required_coverage():
    cases = build_portfolio_risk_test_cases()
    tags = {tag for case in cases for tag in case.expected_risk_tags}

    assert len(cases) >= 20
    assert "single_asset_concentration" in tags
    assert "sector_concentration" in tags
    assert "high_volatility" in tags
    assert "high_drawdown" in tags
    assert "multi_asset_diversification" in tags
    assert "low_risk" in tags


def test_mock_llm_response_is_valid_schema_json():
    case = build_portfolio_risk_test_cases()[0]
    parsed = parse_llm_json_response(mock_llm_response(case))

    assert parsed["portfolio_summary"]
    assert parsed["main_risks"]
    assert parsed["evidence_used"]
    assert parsed["rebalance_suggestions"]


def test_run_llm_evaluation_mock_writes_report(tmp_path):
    output = tmp_path / "evaluation_report.json"

    report = run_llm_evaluation_sync(output_path=output, use_mock=True)

    assert output.exists()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["test_case_count"] >= 20
    assert saved["metrics"]["json_valid_rate"] == 1.0
    assert saved["metrics"]["risk_detection_rate"] == 1.0
    assert saved["metrics"]["evidence_usage_rate"] == 1.0
    assert saved["metrics"]["rebalance_explainability_rate"] == 1.0
    assert saved["metrics"]["hallucination_flag_rate"] == 0.0
    assert report["mode"] == "mock"


def test_aggregate_metrics_handles_hallucination_flag_rate():
    metrics = aggregate_metrics([
        {
            "json_valid": True,
            "risk_detected": True,
            "evidence_used": True,
            "rebalance_explainable": True,
            "hallucination_flag": False,
        },
        {
            "json_valid": True,
            "risk_detected": False,
            "evidence_used": False,
            "rebalance_explainable": False,
            "hallucination_flag": True,
        },
    ])

    assert metrics["json_valid_rate"] == 1.0
    assert metrics["risk_detection_rate"] == 0.5
    assert metrics["hallucination_flag_rate"] == 0.5
