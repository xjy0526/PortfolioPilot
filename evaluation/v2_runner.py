"""Run the governed V2 offline evaluation without crossing mode boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from evaluation.reporting import (
    EVALUATION_MODES,
    build_evaluation_metadata,
    normalize_evaluation_mode,
    validate_evaluation_report,
)
from evaluation.v2_dataset import DATASET_DIR, EvaluationV2Dataset
from evaluation.v2_metrics import (
    build_confidence_intervals,
    classify_badcases,
    evaluate_generation,
    evaluate_retrieval,
    evaluate_workflow,
)


ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "cache" / "evaluation" / "v2"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected one JSON object in {path}")
    return value


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_predictions(dataset: EvaluationV2Dataset) -> dict[str, Any]:
    retrieval = [
        {
            "case_id": case["case_id"],
            "retrieved_document_ids": list(case["relevant_document_ids"]),
        }
        for case in dataset.retrieval_queries
    ]
    retrieval_by_id = {str(item["case_id"]): item for item in retrieval}
    retrieval_by_id["v2-holding-concentration-08"]["retrieved_document_ids"] = []
    retrieval_by_id["v2-sector-concentration-06"]["retrieved_document_ids"] = [
        "doc-policy-workflow",
        "doc-policy-sector",
    ]
    retrieval_by_id["v2-stale-evidence-01"]["retrieved_document_ids"] = [
        "doc-stale-market-outlook",
        "doc-policy-refusal",
    ]
    retrieval_by_id["v2-permission-isolation-01"]["retrieved_document_ids"] = [
        "doc-permission-restricted",
        "doc-permission-public",
    ]

    generation: list[dict[str, Any]] = []
    for case in dataset.generation_cases:
        relevant = list(case["relevant_document_ids"])
        claims = [
            {
                "text": fact,
                "supported_by": relevant,
                "supported": bool(relevant),
            }
            for fact in case["expected_facts"]
        ]
        generation.append(
            {
                "case_id": case["case_id"],
                "json_valid": True,
                "decision": case["expected_decision"],
                "facts": list(case["expected_facts"]),
                "risk_tags": list(case["expected_risk_tags"]),
                "citations": list(case["required_citations"]),
                "claims": claims,
                "numeric_consistent": True,
                "disclaimer": "Research demonstration only; not investment advice.",
            }
        )
    generation_by_id = {str(item["case_id"]): item for item in generation}
    generation_by_id["v2-public-research-qa-02"]["facts"] = ["USD 90,000 million"]
    generation_by_id["v2-public-research-qa-07"]["citations"] = []
    generation_by_id["v2-volatility-drawdown-04"]["numeric_consistent"] = False
    generation_by_id["v2-holding-concentration-06"]["claims"] = [
        {
            "text": "The overlapping positions guarantee a positive return.",
            "supported_by": [],
            "supported": False,
        }
    ]
    generation_by_id["v2-insufficient-evidence-refusal-02"]["decision"] = (
        "answer_with_citations"
    )
    generation_by_id["v2-public-research-qa-08"]["json_valid"] = False

    workflow: list[dict[str, Any]] = []
    for index, case in enumerate(dataset.workflow_cases):
        workflow.append(
            {
                "case_id": case["case_id"],
                "selected_tool": case["expected_tool"],
                "arguments": dict(case["expected_arguments"]),
                "execution_success": True,
                "rule_validation_passed": case["expected_rule_validation"],
                "review_route": case["expected_review_route"],
                "publication_allowed": case["publication_allowed"],
                "latency_ms": 45 + index * 3,
                "cost_amount": round(0.00005 + index * 0.000001, 8),
                "cost_source": "synthetic_estimate",
            }
        )
    workflow_by_id = {str(item["case_id"]): item for item in workflow}
    workflow_by_id["v2-holding-concentration-02"]["selected_tool"] = "retrieve_evidence"
    workflow_by_id["v2-sector-concentration-03"]["arguments"] = {}
    workflow_by_id["v2-geography-currency-04"]["execution_success"] = False
    workflow_by_id["v2-cross-document-conflict-02"]["review_route"] = "auto_publish"
    workflow_by_id["v2-stale-evidence-03"]["publication_allowed"] = True
    return {
        "metadata": {
            "mock_response_used": True,
            "model_provider": "deterministic_fixture",
            "model_name": "portfolio-eval-v2-counterexamples",
            "response_source": "synthetic_counterexample_fixture",
        },
        "retrieval": retrieval,
        "generation": generation,
        "workflow": workflow,
    }


def _load_prediction_bundle(path: Path) -> dict[str, Any]:
    bundle = _read_json_object(path)
    required = {"metadata", "retrieval", "generation", "workflow"}
    missing = sorted(required.difference(bundle))
    if missing:
        raise ValueError(f"prediction bundle missing fields: {', '.join(missing)}")
    if not isinstance(bundle["metadata"], dict):
        raise ValueError("prediction bundle metadata must be an object")
    for layer in ("retrieval", "generation", "workflow"):
        if not isinstance(bundle[layer], list):
            raise ValueError(f"prediction bundle {layer} must be a list")
    bundle["input_checksum"] = _file_checksum(path)
    return bundle


def _validate_prediction_mode(mode: str, bundle: dict[str, Any]) -> None:
    metadata = bundle["metadata"]
    mock_used = metadata.get("mock_response_used") is True
    provider = str(metadata.get("model_provider", "")).lower()
    if mode in {"live_model_eval", "human_gold_eval"}:
        if mock_used or "mock" in provider or "fixture" in provider:
            raise RuntimeError(f"{mode} cannot evaluate mock or fixture responses")
        if not metadata.get("model_provider") or not metadata.get("model_name"):
            raise RuntimeError(f"{mode} requires explicit model_provider and model_name")
        if metadata.get("response_source") != "live_model":
            raise RuntimeError(f"{mode} requires response_source=live_model")
        if not _SHA_PATTERN.fullmatch(str(metadata.get("git_commit_sha", ""))):
            raise RuntimeError(f"{mode} requires the prediction run git_commit_sha")
        try:
            generated_at = datetime.fromisoformat(
                str(metadata.get("generated_at", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RuntimeError(f"{mode} requires prediction generated_at with timezone") from exc
        if generated_at.tzinfo is None:
            raise RuntimeError(f"{mode} requires prediction generated_at with timezone")
        if not isinstance(metadata.get("model_parameters"), dict):
            raise RuntimeError(f"{mode} requires explicit model_parameters")


def _filter_cases(cases: list[dict[str, Any]], case_ids: set[str]) -> list[dict[str, Any]]:
    return [case for case in cases if str(case["case_id"]) in case_ids]


def _filter_predictions(
    predictions: list[dict[str, Any]], case_ids: set[str]
) -> list[dict[str, Any]]:
    return [item for item in predictions if str(item.get("case_id")) in case_ids]


def _run_production_monitoring(
    dataset: EvaluationV2Dataset,
    observations_path: Path | None,
) -> dict[str, Any]:
    if observations_path is None:
        raise RuntimeError(
            "production_monitoring requires --production-observations; synthetic data cannot "
            "stand in for production observations"
        )
    observations = _read_json_object(observations_path)
    metadata = observations.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("production_data_used") is not True:
        raise RuntimeError("production observations must explicitly set production_data_used=true")
    if metadata.get("mock_response_used") is True:
        raise RuntimeError("production_monitoring cannot use mock responses")
    required_metadata = {
        "model_provider",
        "model_name",
        "dataset_name",
        "dataset_version",
        "data_cutoff",
    }
    missing_metadata = sorted(required_metadata.difference(metadata))
    if missing_metadata:
        raise ValueError(
            "production observation metadata missing fields: " + ", ".join(missing_metadata)
        )
    date.fromisoformat(str(metadata["data_cutoff"]))
    required = {"sample_count", "layers", "badcase_distribution", "confidence_intervals"}
    missing = sorted(required.difference(observations))
    if missing:
        raise ValueError(f"production observations missing fields: {', '.join(missing)}")
    report = {
        **build_evaluation_metadata(
            evaluation_mode="production_monitoring",
            model_provider=str(metadata["model_provider"]),
            model_name=str(metadata["model_name"]),
            dataset_name=str(metadata["dataset_name"]),
            dataset_version=str(metadata["dataset_version"]),
            mock_response_used=False,
            synthetic_data_used=False,
            production_data_used=True,
        ),
        "data_cutoff": str(metadata["data_cutoff"]),
        "sample_count": int(observations["sample_count"]),
        "human_reviewed_count": int(observations.get("human_reviewed_count", 0)),
        "layers": observations["layers"],
        "confidence_intervals": observations["confidence_intervals"],
        "badcases": observations.get("badcases", []),
        "badcase_distribution": observations["badcase_distribution"],
        "input_provenance": {
            "observation_checksum": _file_checksum(observations_path),
            "production_data_used": True,
        },
        "evaluation_disclosure": {
            "model_behavior_observations": True,
            "human_validated_metrics": bool(metadata.get("human_label_used")),
            "production_operational_metrics": True,
            "automatic_cases_self_approved": False,
        },
    }
    report["real_model_used"] = bool(metadata.get("real_model_used"))
    report["human_label_used"] = bool(metadata.get("human_label_used"))
    validate_evaluation_report(report)
    return report


def run_v2_evaluation(
    *,
    mode: str = "synthetic_smoke",
    dataset_root: Path = DATASET_DIR,
    predictions_path: Path | None = None,
    production_observations_path: Path | None = None,
) -> dict[str, Any]:
    """Run one isolated V2 mode and return a traceable report."""
    canonical_mode = normalize_evaluation_mode(mode)
    dataset = EvaluationV2Dataset.load(dataset_root)
    if canonical_mode == "production_monitoring":
        return _run_production_monitoring(dataset, production_observations_path)
    case_ids = {str(case["case_id"]) for case in dataset.generation_cases}
    human_labels: dict[str, dict[str, Any]] | None = None
    if canonical_mode == "human_gold_eval":
        case_ids = dataset.require_human_gold_case_ids()
        human_labels = {
            str(label["case_id"]): label for label in dataset.approved_human_labels()
        }
    if canonical_mode == "synthetic_smoke":
        if predictions_path is not None:
            raise RuntimeError("synthetic_smoke uses only the versioned synthetic fixture")
        bundle = _synthetic_predictions(dataset)
    else:
        if predictions_path is None:
            raise RuntimeError(f"{canonical_mode} requires an explicit captured prediction bundle")
        bundle = _load_prediction_bundle(predictions_path)
        _validate_prediction_mode(canonical_mode, bundle)

    retrieval_cases = _filter_cases(dataset.retrieval_queries, case_ids)
    generation_cases = _filter_cases(dataset.generation_cases, case_ids)
    workflow_cases = _filter_cases(dataset.workflow_cases, case_ids)
    layers = {
        "retrieval": evaluate_retrieval(
            retrieval_cases,
            _filter_predictions(bundle["retrieval"], case_ids),
            top_k=5,
        ),
        "generation": evaluate_generation(
            generation_cases,
            _filter_predictions(bundle["generation"], case_ids),
            human_labels=human_labels,
        ),
        "workflow": evaluate_workflow(
            workflow_cases,
            _filter_predictions(bundle["workflow"], case_ids),
        ),
    }
    badcases, distribution = classify_badcases(layers)
    prediction_metadata = bundle["metadata"]
    mock_used = prediction_metadata.get("mock_response_used") is True
    report = {
        **build_evaluation_metadata(
            evaluation_mode=canonical_mode,
            model_provider=str(prediction_metadata.get("model_provider") or "not_applicable"),
            model_name=str(prediction_metadata.get("model_name") or "not_applicable"),
            dataset_name=str(dataset.manifest["dataset_name"]),
            dataset_version=str(dataset.manifest["version"]),
            mock_response_used=mock_used,
            synthetic_data_used=True,
            human_label_used=canonical_mode == "human_gold_eval",
        ),
        "data_cutoff": dataset.manifest["data_cutoff"],
        "sample_count": len(case_ids),
        "human_reviewed_count": len(dataset.approved_human_labels()),
        "dataset_checksum": dataset.manifest["checksum"]["dataset_digest"],
        "dataset_split_counts": dataset.manifest["split"],
        "category_distribution": dataset.manifest["category_distribution"],
        "layers": layers,
        "confidence_intervals": build_confidence_intervals(layers),
        "badcases": badcases,
        "badcase_distribution": distribution,
        "input_provenance": {
            "prediction_checksum": bundle.get("input_checksum"),
            "response_source": prediction_metadata.get("response_source"),
            "prediction_generated_at": prediction_metadata.get("generated_at"),
            "prediction_git_commit_sha": prediction_metadata.get("git_commit_sha"),
            "model_parameters": prediction_metadata.get("model_parameters"),
            "public_source_count": len(dataset.manifest["public_sources"]),
            "synthetic_portfolio": True,
            "real_customer_portfolio_used": False,
        },
        "evaluation_disclosure": {
            "synthetic_counterexamples_injected": canonical_mode == "synthetic_smoke",
            "model_behavior_observations": canonical_mode in {
                "live_model_eval",
                "human_gold_eval",
            },
            "human_validated_metrics": canonical_mode == "human_gold_eval",
            "production_operational_metrics": False,
            "automatic_cases_self_approved": False,
        },
    }
    report["real_model_used"] = canonical_mode in {"live_model_eval", "human_gold_eval"}
    validate_evaluation_report(report)
    return report


def write_v2_report(report: dict[str, Any], output_path: Path | None = None) -> Path:
    """Write reports under mode-specific paths so modes cannot overwrite each other."""
    mode = normalize_evaluation_mode(str(report["evaluation_mode"]))
    if output_path is None:
        generated_at = str(report["generated_at"]).replace(":", "").replace("+", "_")
        output_path = (
            REPORT_ROOT
            / mode
            / f"report-{generated_at}-{str(report['git_commit_sha'])[:8]}.json"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def latest_v2_report_path(root: Path = REPORT_ROOT) -> Path | None:
    """Return the newest report across isolated mode directories, if present."""
    candidates = list(root.glob("*/*.json")) if root.exists() else []
    for path in sorted(candidates, key=lambda candidate: candidate.stat().st_mtime, reverse=True):
        try:
            report = _read_json_object(path)
            validate_evaluation_report(report)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        return path
    return None


def report_mode_directories(root: Path = REPORT_ROOT) -> dict[str, Path]:
    return {mode: root / mode for mode in EVALUATION_MODES}


def utc_now() -> str:
    """Expose UTC generation time for CLI summaries and tests."""
    return datetime.now(UTC).isoformat()
