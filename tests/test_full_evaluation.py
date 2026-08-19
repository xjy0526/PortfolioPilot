import json
import pytest

from evaluation.full_eval import (
    BADCASE_LABELS,
    _empty_workflow_metrics,
    evaluation_dashboard,
    run_full_evaluation,
)


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
    assert report["evaluation_mode"] == "synthetic_smoke"
    assert report["dataset_version"] == "v1"
    assert len(report["git_commit_sha"]) == 40
    assert report["layers"]["generation"]["metric_disclosures"][
        "hallucination_flag_rate"
    ]["sample_size"] == report["layers"]["generation"]["case_count"]
    assert all("expected_evidence" in case and "expected_decision" in case for case in report["layers"]["retrieval"]["cases"])


def test_synthetic_workflow_metrics_do_not_read_production_storage():
    result = _empty_workflow_metrics()
    assert result["run_count"] == 0
    assert result["metrics"]["failure_rate"] == 0.0
    assert result["data_source"] == "synthetic_static_only"


@pytest.mark.asyncio
async def test_dashboard_exposes_only_traceable_evaluation_report(tmp_path, monkeypatch):
    import evaluation.full_eval as module

    output = tmp_path / "full_evaluation_report.json"
    generated = {
        "evaluation_mode": "synthetic_smoke",
        "generated_at": "2026-08-19T00:00:00+00:00",
        "git_commit_sha": "a" * 40,
        "mock_response_used": True,
        "synthetic_data_used": True,
        "model_provider": "deterministic_mock",
        "model_name": "portfolio-risk-mock-v1",
        "dataset_name": "portfolio_risk_and_retrieval_gold",
        "dataset_version": "v1",
        "human_label_used": False,
        "production_data_used": False,
        "real_model_used": False,
        "layers": {
            "generation": {
                "case_count": 20,
                "valid_response_case_count": 20,
                "metrics": {"hallucination_rate": 0.0},
                "metric_disclosures": {
                    "hallucination_flag_rate": {
                        "evaluation_mode": "synthetic_smoke",
                        "sample_size": 20,
                    }
                },
            }
        },
        "badcase_distribution": {"model_hallucination": 0},
    }
    output.write_text(json.dumps(generated), encoding="utf-8")

    class FakeRegistry:
        def __init__(self, _session):
            pass

        async def list_traces(self, _limit):
            return []

    async def fake_workflow_metrics(_session):
        return _empty_workflow_metrics()

    monkeypatch.setattr(module, "PromptRegistry", FakeRegistry)
    monkeypatch.setattr(module, "_workflow_metrics", fake_workflow_metrics)
    dashboard = await evaluation_dashboard(object(), report_path=output)

    summary = dashboard["evaluation_report"]
    assert summary["status"] == "available"
    assert summary["evaluation_mode"] == "synthetic_smoke"
    assert summary["mock_response_used"] is True
    assert summary["case_count"] == generated["layers"]["generation"]["case_count"]

    output.write_text(json.dumps({"evaluation_mode": "synthetic_smoke"}), encoding="utf-8")
    unavailable = await evaluation_dashboard(object(), report_path=output)
    assert unavailable["evaluation_report"]["status"] == "unavailable"
    assert unavailable["badcase_distribution"] == {}
