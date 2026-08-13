import json
from evaluation.full_eval import BADCASE_LABELS, _empty_workflow_metrics, run_full_evaluation


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
    workflow_metrics = report["layers"]["workflow"]["metrics"]
    assert "workflow_allowlist_completion_rate" in workflow_metrics
    assert {
        "tool_selection_accuracy",
        "required_argument_accuracy",
        "argument_value_accuracy",
        "tool_execution_success_rate",
    }.issubset(workflow_metrics)
    assert "citation_reference_validity" in report["layers"]["retrieval"]["metrics"]
    assert "citation_hit_rate" not in report["layers"]["retrieval"]["metrics"]
    assert report["mock_response_used"] is True
    assert all("expected_evidence" in case and "expected_decision" in case for case in report["layers"]["retrieval"]["cases"])


def test_synthetic_workflow_metrics_do_not_read_production_storage():
    result = _empty_workflow_metrics()
    assert result["run_count"] == 0
    assert result["metrics"]["failure_rate"] == 0.0
    assert result["data_source"] == "synthetic_static_only"
