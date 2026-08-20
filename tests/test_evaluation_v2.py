import json
import shutil
import sys
from pathlib import Path

import pytest

from evaluation.reporting import validate_evaluation_report
from evaluation.full_eval import _empty_workflow_metrics, evaluation_dashboard
from evaluation.run_v2_eval import main as run_v2_cli
from evaluation.v2_dataset import COMMON_CASE_FIELDS, DATASET_DIR, EvaluationV2Dataset
from evaluation.v2_runner import (
    _synthetic_predictions,
    latest_v2_report_path,
    report_mode_directories,
    run_v2_evaluation,
    write_v2_report,
)
from evaluation.v2_metrics import evaluate_generation
from scripts.build_evaluation_v2_dataset import build_dataset


EXPECTED_CATEGORY_COUNTS = {
    "holding_concentration": 8,
    "sector_concentration": 6,
    "geography_currency": 5,
    "volatility_drawdown": 6,
    "liquidity_cash_drag": 5,
    "multi_asset_risk": 5,
    "public_research_qa": 8,
    "cross_document_conflict": 4,
    "stale_evidence": 4,
    "permission_isolation": 4,
    "insufficient_evidence_refusal": 5,
}


def test_v2_manifest_schema_distribution_and_synthetic_portfolios():
    dataset = EvaluationV2Dataset.load()

    assert dataset.manifest["version"] == "2.0.0"
    assert dataset.manifest["case_count"] == 60
    assert dataset.manifest["category_distribution"] == EXPECTED_CATEGORY_COUNTS
    assert dataset.manifest["human_review_status"]["approved_case_count"] == 0
    assert all(
        portfolio["classification"] == "synthetic_portfolio"
        and portfolio["is_real_user_portfolio"] is False
        for portfolio in dataset.portfolios
    )
    assert all(COMMON_CASE_FIELDS.issubset(case) for case in dataset.generation_cases)


def test_v2_split_has_no_overlap_and_matches_manifest():
    dataset = EvaluationV2Dataset.load()
    splits = dataset.split_case_ids()

    assert {name: len(values) for name, values in splits.items()} == {
        "train": 36,
        "dev": 12,
        "test": 12,
    }
    assert splits["train"].isdisjoint(splits["dev"])
    assert splits["train"].isdisjoint(splits["test"])
    assert splits["dev"].isdisjoint(splits["test"])


def test_v2_checksum_detects_tampering(tmp_path):
    copied = tmp_path / "v2"
    shutil.copytree(DATASET_DIR, copied)
    target = copied / "documents.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        EvaluationV2Dataset.load(copied)


def test_v2_builder_is_deterministic_and_reproducible(tmp_path):
    generated_root = tmp_path / "generated-v2"
    generated_manifest = build_dataset(generated_root)
    committed = EvaluationV2Dataset.load()
    generated = EvaluationV2Dataset.load(generated_root)

    assert generated_manifest["checksum"] == committed.manifest["checksum"]
    assert generated.manifest["category_distribution"] == EXPECTED_CATEGORY_COUNTS
    assert len(generated.generation_cases) == 60


def test_human_gold_requires_independent_approved_labels():
    dataset = EvaluationV2Dataset.load()

    with pytest.raises(RuntimeError, match="independently approved"):
        dataset.require_human_gold_case_ids()
    with pytest.raises(RuntimeError, match="independently approved"):
        run_v2_evaluation(mode="human_gold_eval", predictions_path=Path("unused.json"))


def test_human_citation_support_label_drives_claim_support_metric():
    case = EvaluationV2Dataset.load().generation_cases[0]
    prediction = {
        "case_id": case["case_id"],
        "json_valid": True,
        "decision": case["expected_decision"],
        "facts": case["expected_facts"],
        "citations": case["required_citations"],
        "claims": [{"text": "reviewed claim", "supported": False, "supported_by": []}],
        "numeric_consistent": True,
    }
    labels = {
        case["case_id"]: {
            "factuality_label": "pass",
            "citation_support_label": "pass",
            "numeric_consistency_label": "pass",
            "refusal_label": "not_applicable",
        }
    }

    result = evaluate_generation([case], [prediction], human_labels=labels)

    assert result["metrics"]["claim_support_rate"] == 1.0
    assert result["cases"][0]["citation_support_pass"] is True


def test_live_mode_requires_real_non_mock_prediction_bundle(tmp_path):
    dataset = EvaluationV2Dataset.load()
    bundle = _synthetic_predictions(dataset)
    path = tmp_path / "mock_predictions.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot evaluate mock or fixture"):
        run_v2_evaluation(mode="live_model_eval", predictions_path=path)
    with pytest.raises(RuntimeError, match="explicit captured prediction bundle"):
        run_v2_evaluation(mode="live_model_eval")


def test_production_mode_never_synthesizes_observations():
    with pytest.raises(RuntimeError, match="production observations"):
        run_v2_evaluation(mode="production_monitoring")


def test_synthetic_report_exposes_unauthorized_stale_and_badcase_metrics():
    report = run_v2_evaluation(mode="synthetic_smoke")

    assert report["layers"]["retrieval"]["metrics"]["unauthorized_hit_count"] == 1
    assert report["layers"]["retrieval"]["metrics"]["stale_hit_rate"] > 0
    assert report["badcase_distribution"]["unauthorized_hit"] == 1
    assert report["badcase_distribution"]["stale_hit"] == 1
    assert report["badcase_distribution"]["factuality_error"] == 1
    assert report["badcase_distribution"]["publication_safety_error"] == 1
    assert report["layers"]["workflow"]["metrics"]["actual_cost_case_count"] == 0
    assert report["layers"]["workflow"]["metrics"]["estimated_cost_case_count"] == 60


def test_v2_report_contains_provenance_and_required_dashboard_dimensions():
    report = run_v2_evaluation(mode="synthetic_smoke")

    validate_evaluation_report(report)
    assert report["evaluation_mode"] == "synthetic_smoke"
    assert report["mock_response_used"] is True
    assert report["sample_count"] == 60
    assert report["dataset_version"] == "2.0.0"
    assert report["data_cutoff"] == "2025-12-31"
    assert report["human_reviewed_count"] == 0
    assert report["confidence_intervals"]
    assert report["input_provenance"]["real_customer_portfolio_used"] is False
    assert report["evaluation_disclosure"]["synthetic_counterexamples_injected"] is True


def test_v2_metric_names_match_their_actual_definitions():
    report = run_v2_evaluation(mode="synthetic_smoke")
    retrieval = report["layers"]["retrieval"]["metrics"]
    generation = report["layers"]["generation"]["metrics"]
    workflow = report["layers"]["workflow"]["metrics"]

    assert {"recall_at_5", "precision_at_5", "mrr", "ndcg_at_5"}.issubset(retrieval)
    assert {"unauthorized_hit_count", "stale_hit_rate"}.issubset(retrieval)
    assert {
        "json_compliance",
        "factual_correctness",
        "numeric_consistency",
        "claim_support_rate",
        "citation_precision",
        "citation_completeness",
        "unsupported_claim_rate",
        "correct_refusal_rate",
    }.issubset(generation)
    assert {
        "tool_selection",
        "argument_validity",
        "execution_success",
        "rule_validation",
        "review_routing_accuracy",
        "publication_safety",
        "end_to_end_latency_ms",
        "actual_cost_total",
        "estimated_cost_total",
    }.issubset(workflow)


def test_report_paths_are_isolated_by_mode(tmp_path):
    directories = report_mode_directories(tmp_path)

    assert len(set(directories.values())) == 4
    assert set(directories) == {
        "synthetic_smoke",
        "live_model_eval",
        "human_gold_eval",
        "production_monitoring",
    }
    report = run_v2_evaluation(mode="synthetic_smoke")
    path = write_v2_report(report, tmp_path / "synthetic_smoke" / "report.json")
    assert path.parent.name == "synthetic_smoke"


def test_dashboard_report_discovery_accepts_named_reports_and_skips_invalid(tmp_path):
    mode_dir = tmp_path / "synthetic_smoke"
    valid = write_v2_report(
        run_v2_evaluation(mode="synthetic_smoke"),
        mode_dir / "quickstart.json",
    )
    invalid = mode_dir / "newer-invalid.json"
    invalid.write_text("not json", encoding="utf-8")

    assert latest_v2_report_path(tmp_path) == valid


def test_v2_cli_writes_a_traceable_mode_specific_report(tmp_path, monkeypatch, capsys):
    output = tmp_path / "synthetic_smoke" / "cli-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_v2_eval",
            "--mode",
            "synthetic_smoke",
            "--output",
            str(output),
        ],
    )

    run_v2_cli()

    summary = json.loads(capsys.readouterr().out)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert summary["output"] == str(output)
    assert summary["sample_count"] == 60
    assert report["evaluation_mode"] == "synthetic_smoke"
    assert report["mock_response_used"] is True


def test_public_documents_are_metadata_backed_project_paraphrases():
    dataset = EvaluationV2Dataset.load()
    public_documents = [
        document
        for document in dataset.documents
        if document["source_classification"] == "public_source_paraphrase"
    ]

    assert len(public_documents) == 5
    assert all(document["source_url"].startswith("https://") for document in public_documents)
    assert all(
        document["content_origin"] == "project_authored_paraphrase"
        for document in public_documents
    )


@pytest.mark.asyncio
async def test_dashboard_exposes_v2_dimensions_without_changing_api_shape(tmp_path, monkeypatch):
    import evaluation.full_eval as full_eval

    report_path = tmp_path / "v2-report.json"
    write_v2_report(run_v2_evaluation(mode="synthetic_smoke"), report_path)

    class FakeRegistry:
        def __init__(self, _session):
            pass

        async def list_traces(self, _limit):
            return []

    async def fake_workflow_metrics(_session):
        return _empty_workflow_metrics()

    monkeypatch.setattr(full_eval, "PromptRegistry", FakeRegistry)
    monkeypatch.setattr(full_eval, "_workflow_metrics", fake_workflow_metrics)
    dashboard = await evaluation_dashboard(object(), report_path=report_path)
    summary = dashboard["evaluation_report"]

    assert summary["status"] == "available"
    assert summary["case_count"] == 60
    assert summary["dataset_version"] == "2.0.0"
    assert summary["data_cutoff"] == "2025-12-31"
    assert summary["human_reviewed_count"] == 0
    assert summary["git_worktree_dirty"] in {True, False, None}
    assert summary["confidence_intervals"]
    assert set(summary["metric_summary"]) == {"retrieval", "generation", "workflow"}
    assert dashboard["badcase_distribution"]
