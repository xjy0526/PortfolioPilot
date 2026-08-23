"""Metrics and provenance checks for human-reviewed V3 prediction bundles."""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from config import settings
from evaluation.human_gold_dataset import HumanGoldDataset, file_sha256
from evaluation.reporting import build_evaluation_metadata, validate_evaluation_report


RETRIEVER_VARIANTS = (
    "bm25_fts_only",
    "vector_only",
    "hybrid_rrf",
    "hybrid_rrf_reranker",
)
MANDATORY_RETRIEVER_VARIANTS = RETRIEVER_VARIANTS[:3]
PREDICTION_METADATA_FIELDS = frozenset(
    {
        "evaluation_mode",
        "dataset_name",
        "dataset_version",
        "model_provider",
        "model_name",
        "prompt_key",
        "prompt_version",
        "temperature",
        "git_commit_sha",
        "generated_at",
        "mock_response_used",
        "real_model_used",
        "response_source",
        "raw_output_policy",
    }
)


def validate_prediction_bundle(
    bundle: dict[str, Any],
    *,
    dataset: HumanGoldDataset,
    mode: str,
) -> None:
    """Reject mock, cross-version, incomplete, or mode-confused prediction bundles."""
    metadata = bundle.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("prediction bundle requires metadata")
    missing = sorted(PREDICTION_METADATA_FIELDS.difference(metadata))
    if missing:
        raise ValueError(f"prediction metadata missing fields: {', '.join(missing)}")
    if metadata["evaluation_mode"] != mode:
        raise ValueError("prediction evaluation mode does not match requested mode")
    if metadata["dataset_name"] != dataset.manifest["dataset_name"]:
        raise ValueError("prediction dataset name mismatch")
    if metadata["dataset_version"] != dataset.version:
        raise ValueError("prediction dataset version mismatch")
    if metadata["mock_response_used"] is not False:
        raise RuntimeError(f"{mode} cannot evaluate mock responses")
    if metadata["real_model_used"] is not True:
        raise RuntimeError(f"{mode} requires real_model_used=true")
    if metadata["response_source"] != "live_model":
        raise RuntimeError(f"{mode} requires response_source=live_model")
    if metadata["raw_output_policy"] not in {
        "not_stored",
        "redacted_and_authorized",
    }:
        raise ValueError("raw model output requires an explicit safe storage policy")
    for field in ("model_provider", "model_name", "prompt_key", "prompt_version"):
        if not str(metadata[field]).strip():
            raise ValueError(f"prediction metadata requires non-empty {field}")
    temperature = metadata["temperature"]
    if not isinstance(temperature, (int, float)) or temperature < 0:
        raise ValueError("temperature must be a non-negative number")
    try:
        generated_at = datetime.fromisoformat(
            str(metadata["generated_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("prediction generated_at must be ISO-8601") from exc
    if generated_at.tzinfo is None:
        raise ValueError("prediction generated_at must include a timezone")
    sha = str(metadata["git_commit_sha"])
    if len(sha) != 40 or any(char not in "0123456789abcdefABCDEF" for char in sha):
        raise ValueError("prediction git_commit_sha must be a full commit SHA")

    retrievers = bundle.get("retrievers")
    if not isinstance(retrievers, dict):
        raise ValueError("prediction bundle requires retriever results")
    missing_variants = set(MANDATORY_RETRIEVER_VARIANTS).difference(retrievers)
    if missing_variants:
        raise ValueError(f"prediction bundle missing retrievers: {sorted(missing_variants)}")
    for variant in RETRIEVER_VARIANTS:
        result = retrievers.get(variant)
        if variant == "hybrid_rrf_reranker" and result is None:
            continue
        if not isinstance(result, dict):
            raise ValueError(f"retriever result must be an object: {variant}")
        available = result.get("available")
        if available is False:
            if variant != "hybrid_rrf_reranker":
                raise ValueError(f"mandatory retriever cannot be unavailable: {variant}")
            if not str(result.get("unavailable_reason") or "").strip():
                raise ValueError("unavailable reranker requires a reason")
            continue
        if available is not True or not isinstance(result.get("cases"), list):
            raise ValueError(f"available retriever requires case results: {variant}")
    if not isinstance(bundle.get("generation"), list):
        raise ValueError("prediction bundle requires generation results")
    generation_cases = _index_cases(bundle["generation"], "generation")
    known_case_ids = set(dataset.case_ids)
    unknown_generation = sorted(set(generation_cases).difference(known_case_ids))
    if unknown_generation:
        raise ValueError(f"generation contains unknown cases: {unknown_generation}")
    if not generation_cases:
        raise ValueError("prediction bundle must contain at least one generation case")
    for variant in RETRIEVER_VARIANTS:
        result = retrievers.get(variant)
        if not isinstance(result, dict) or result.get("available") is not True:
            continue
        variant_cases = _index_cases(result["cases"], variant)
        unknown_retrieval = sorted(set(variant_cases).difference(known_case_ids))
        if unknown_retrieval:
            raise ValueError(f"{variant} contains unknown cases: {unknown_retrieval}")
        if set(variant_cases) != set(generation_cases):
            raise ValueError(
                f"{variant} and generation must cover the same captured cases"
            )


def require_live_provider_credential(
    provider: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail closed when a live evaluation provider credential is unavailable."""
    environment = environ if environ is not None else os.environ
    canonical = provider.strip().lower()
    configured = False
    if canonical == "qwen":
        configured = bool(environment.get("QWEN_API_KEY") or settings.QWEN_API_KEY)
    elif canonical in {"openai", "openai_compatible"}:
        configured = bool(
            environment.get("OPENAI_COMPATIBLE_API_KEY")
            or settings.OPENAI_COMPATIBLE_API_KEY
        )
    else:
        key_name = f"{canonical.upper().replace('-', '_')}_API_KEY"
        configured = bool(environment.get(key_name))
    if not configured:
        raise RuntimeError(
            f"live_model_eval requires a real credential for provider {provider}; "
            "mock fallback is forbidden"
        )


def evaluate_retriever_variants(
    *,
    bundle: dict[str, Any],
    approved_labels: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare all configured retrievers against adjudicated graded relevance."""
    label_by_case = {str(label["case_id"]): label for label in approved_labels}
    retrievers = bundle["retrievers"]
    results: dict[str, Any] = {}
    for variant in RETRIEVER_VARIANTS:
        variant_result = retrievers.get(variant)
        if not variant_result or variant_result.get("available") is False:
            results[variant] = {
                "status": "unavailable",
                "reason": (
                    variant_result.get("unavailable_reason")
                    if isinstance(variant_result, dict)
                    else "reranker_not_configured"
                ),
                "metrics": None,
            }
            continue
        case_results = _index_cases(variant_result["cases"], f"retriever {variant}")
        missing = sorted(set(label_by_case).difference(case_results))
        if missing:
            raise ValueError(f"{variant} missing approved cases: {missing}")
        results[variant] = {
            "status": "evaluated",
            "metrics": _retrieval_metrics(case_results, label_by_case),
        }
    return results


def evaluate_generation_results(
    *,
    bundle: dict[str, Any],
    approved_labels: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate citation and refusal metrics from adjudicated human judgments."""
    label_by_case = {str(label["case_id"]): label for label in approved_labels}
    category_by_case = {
        str(case["case_id"]): str(case["category"]) for case in candidate_cases
    }
    generation = _index_cases(bundle["generation"], "generation")
    missing = sorted(set(label_by_case).difference(generation))
    if missing:
        raise ValueError(f"generation results missing approved cases: {missing}")

    citation_hits = 0
    citation_total = 0
    citation_support_hits = 0
    citation_support_total = 0
    evidence_accuracy_hits = 0
    evidence_accuracy_total = 0
    refused_total = 0
    refusal_true_positive = 0
    numeric_hits = 0
    numeric_total = 0
    completeness_hits = 0
    unsupported_claims = 0
    latencies: list[float] = []
    for case_id, label in label_by_case.items():
        prediction = generation[case_id]
        relevance = {
            str(document_id): int(grade)
            for document_id, grade in label["retrieval_relevance"].items()
        }
        citations = [str(value) for value in prediction.get("citation_document_ids", [])]
        citation_hits += sum(relevance.get(document_id, 0) > 0 for document_id in citations)
        citation_total += len(citations)
        if citations:
            citation_support_total += 1
            citation_support_hits += label["citation_supported"] is True
        expected_refusal = label["appropriate_refusal"] is True
        refused = prediction.get("refused") is True
        if category_by_case.get(case_id) == "evidence_insufficient":
            evidence_accuracy_total += 1
            evidence_accuracy_hits += refused == expected_refusal
        refused_total += refused
        refusal_true_positive += refused and expected_refusal
        if label["numeric_consistency"] != "not_applicable":
            numeric_total += 1
            numeric_hits += label["numeric_consistency"] == "pass"
        completeness_hits += label["answer_completeness"] == "complete"
        unsupported_claims += label["unsupported_claim"] is True
        latency = prediction.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            latencies.append(float(latency))
    count = len(label_by_case)
    return {
        "citation_precision": _rate(citation_hits, citation_total),
        "citation_support_rate": _rate(citation_support_hits, citation_support_total),
        "citation_case_count": citation_support_total,
        "evidence_insufficient_accuracy": _rate(
            evidence_accuracy_hits, evidence_accuracy_total
        ),
        "evidence_insufficient_sample_count": evidence_accuracy_total,
        "refusal_precision": _rate(refusal_true_positive, refused_total),
        "numeric_consistency_rate": _rate(numeric_hits, numeric_total),
        "answer_completeness_rate": _rate(completeness_hits, count),
        "unsupported_claim_rate": _rate(unsupported_claims, count),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "sample_count": count,
    }


def build_human_gold_report(
    *,
    dataset: HumanGoldDataset,
    bundle: dict[str, Any],
    prediction_path: Path,
) -> dict[str, Any]:
    """Build a formal report only after the full governed review gate succeeds."""
    validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")
    approved = dataset.require_adjudicated_labels()
    review_pair = dataset.review_pair_status()
    conflict_case_ids = set(review_pair.conflict_case_ids)
    approved_adjudicated_count = sum(
        label["review_status"] == "approved"
        and str(label["case_id"]) in conflict_case_ids
        for label in dataset.adjudicated_labels
    )
    prediction_digest = file_sha256(prediction_path)
    mismatched = [
        str(label["case_id"])
        for label in approved
        if label["prediction_sha256"] != prediction_digest
    ]
    if mismatched:
        raise ValueError(f"human labels target a different prediction bundle: {mismatched}")
    metadata = bundle["metadata"]
    report = build_evaluation_metadata(
        evaluation_mode="human_gold_eval",
        model_provider=str(metadata["model_provider"]),
        model_name=str(metadata["model_name"]),
        dataset_name=str(dataset.manifest["dataset_name"]),
        dataset_version=dataset.version,
        mock_response_used=False,
        synthetic_data_used=True,
        human_label_used=True,
        production_data_used=False,
    )
    report["real_model_used"] = True
    report.update(
        {
            "data_cutoff": dataset.manifest["data_cutoff"],
            "sample_count": len(approved),
            "approved_human_gold_count": len(approved),
            "approved_consensus_count": len(approved) - approved_adjudicated_count,
            "approved_adjudicated_count": approved_adjudicated_count,
            "candidate_case_count": len(dataset.candidate_cases),
            "prediction_bundle_sha256": prediction_digest,
            "prediction_git_commit_sha": metadata["git_commit_sha"],
            "prediction_generated_at": metadata["generated_at"],
            "prompt_key": metadata["prompt_key"],
            "prompt_version": metadata["prompt_version"],
            "temperature": metadata["temperature"],
            "response_source": metadata["response_source"],
            "raw_output_policy": metadata["raw_output_policy"],
            "dataset_immutable_checksums": dataset.manifest["checksum"][
                "immutable_files"
            ],
            "review_label_sha256": {
                "reviewer_a": _rows_sha256(dataset.reviewer_a_labels),
                "reviewer_b": _rows_sha256(dataset.reviewer_b_labels),
                "adjudicated": _rows_sha256(dataset.adjudicated_labels),
            },
            "retriever_comparison": evaluate_retriever_variants(
                bundle=bundle,
                approved_labels=approved,
            ),
            "generation_metrics": evaluate_generation_results(
                bundle=bundle,
                approved_labels=approved,
                candidate_cases=dataset.candidate_cases,
            ),
            "mode_isolation": {
                "synthetic_smoke_metrics_included": False,
                "live_execution_provenance_used": True,
                "production_observations_used": False,
            },
        }
    )
    validate_evaluation_report(report)
    return report


def build_live_model_provenance_report(
    *,
    dataset: HumanGoldDataset,
    bundle: dict[str, Any],
    prediction_path: Path,
) -> dict[str, Any]:
    """Validate a captured live run without inventing human-gold metrics."""
    validate_prediction_bundle(bundle, dataset=dataset, mode="live_model_eval")
    metadata = bundle["metadata"]
    require_live_provider_credential(str(metadata["model_provider"]))
    report = build_evaluation_metadata(
        evaluation_mode="live_model_eval",
        model_provider=str(metadata["model_provider"]),
        model_name=str(metadata["model_name"]),
        dataset_name=str(dataset.manifest["dataset_name"]),
        dataset_version=dataset.version,
        mock_response_used=False,
        synthetic_data_used=True,
        human_label_used=False,
        production_data_used=False,
    )
    report.update(
        {
            "sample_count": len(bundle["generation"]),
            "data_cutoff": dataset.manifest["data_cutoff"],
            "prediction_bundle_sha256": file_sha256(prediction_path),
            "prompt_key": metadata["prompt_key"],
            "prompt_version": metadata["prompt_version"],
            "temperature": metadata["temperature"],
            "prediction_git_commit_sha": metadata["git_commit_sha"],
            "prediction_generated_at": metadata["generated_at"],
            "response_source": metadata["response_source"],
            "raw_output_policy": metadata["raw_output_policy"],
            "metrics_status": "awaiting_human_adjudication",
            "human_gold_metrics": None,
        }
    )
    validate_evaluation_report(report)
    return report


def _rows_sha256(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return hashlib.sha256(f"{payload}\n".encode()).hexdigest()


def _index_cases(rows: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id", "")).strip()
        if not case_id or case_id in indexed:
            raise ValueError(f"{source} requires unique, non-empty case IDs")
        indexed[case_id] = row
    return indexed


def _retrieval_metrics(
    cases: dict[str, dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    total_relevant = 0
    total_hits = 0
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    latencies: list[float] = []
    for case_id, label in labels.items():
        relevance = {
            str(document_id): int(grade)
            for document_id, grade in label["retrieval_relevance"].items()
        }
        relevant = {document_id for document_id, grade in relevance.items() if grade > 0}
        retrieved = [
            str(item["document_id"])
            for item in cases[case_id].get("retrieved", [])[:5]
            if isinstance(item, dict) and item.get("document_id")
        ]
        hits = [document_id for document_id in retrieved if document_id in relevant]
        total_relevant += len(relevant)
        total_hits += len(hits)
        first_rank = next(
            (rank for rank, document_id in enumerate(retrieved, 1) if document_id in relevant),
            None,
        )
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        ndcg_values.append(_graded_ndcg(retrieved, relevance, 5))
        latency = cases[case_id].get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            latencies.append(float(latency))
    return {
        "recall_at_5": _rate(total_hits, total_relevant),
        "mrr": _mean(reciprocal_ranks),
        "ndcg_at_5": _mean(ndcg_values),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "sample_count": len(labels),
    }


def _graded_ndcg(retrieved: list[str], relevance: dict[str, int], top_k: int) -> float:
    dcg = sum(
        (2 ** relevance.get(document_id, 0) - 1) / math.log2(rank + 1)
        for rank, document_id in enumerate(retrieved[:top_k], 1)
    )
    ideal = sorted(relevance.values(), reverse=True)[:top_k]
    ideal_dcg = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal, 1)
    )
    return round(dcg / ideal_dcg, 6) if ideal_dcg else 0.0


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )
    return round(interpolated, 6)
