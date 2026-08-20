"""Deterministic metrics for retrieval, generation and workflow V2 evaluation."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _prediction_index(predictions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for prediction in predictions:
        case_id = str(prediction.get("case_id", ""))
        if not case_id or case_id in index:
            raise ValueError("predictions require unique, non-empty case_id values")
        index[case_id] = prediction
    return index


def _safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _ndcg_at_k(retrieved: list[str], relevant: set[str], top_k: int) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(retrieved[:top_k], start=1)
        if document_id in relevant
    )
    ideal_hits = min(len(relevant), top_k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return round(dcg / ideal_dcg, 6) if ideal_dcg else 0.0


def evaluate_retrieval(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Calculate ranking, permission and temporal-validity metrics."""
    prediction_by_id = _prediction_index(predictions)
    results: list[dict[str, Any]] = []
    total_relevant = 0
    total_relevant_hits = 0
    ranking_slots = 0
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    unauthorized_hits = 0
    stale_hits = 0
    retrieved_total = 0
    for case in cases:
        case_id = str(case["case_id"])
        prediction = prediction_by_id.get(case_id, {})
        retrieved = [str(value) for value in prediction.get("retrieved_document_ids", [])][:top_k]
        relevant = {str(value) for value in case["relevant_document_ids"]}
        unauthorized = {str(value) for value in case.get("unauthorized_document_ids", [])}
        stale = {str(value) for value in case.get("stale_document_ids", [])}
        hits = [value for value in retrieved if value in relevant]
        first_rank = next(
            (rank for rank, value in enumerate(retrieved, start=1) if value in relevant),
            None,
        )
        case_unauthorized = sum(value in unauthorized for value in retrieved)
        case_stale = sum(value in stale for value in retrieved)
        if relevant:
            total_relevant += len(relevant)
            total_relevant_hits += len(hits)
            ranking_slots += top_k
            reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
            ndcg_values.append(_ndcg_at_k(retrieved, relevant, top_k))
        unauthorized_hits += case_unauthorized
        stale_hits += case_stale
        retrieved_total += len(retrieved)
        results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "retrieved_document_ids": retrieved,
                "relevant_document_ids": sorted(relevant),
                "recall_at_k": _safe_rate(len(hits), len(relevant)),
                "precision_at_k": _safe_rate(len(hits), top_k) if relevant else None,
                "reciprocal_rank": round(1.0 / first_rank, 6) if first_rank else 0.0,
                "ndcg_at_k": _ndcg_at_k(retrieved, relevant, top_k) if relevant else None,
                "unauthorized_hit_count": case_unauthorized,
                "stale_hit_count": case_stale,
                "missing_prediction": case_id not in prediction_by_id,
            }
        )
    metric_counts = {
        f"recall_at_{top_k}": [total_relevant_hits, total_relevant],
        f"precision_at_{top_k}": [total_relevant_hits, ranking_slots],
        "unauthorized_safe_case_rate": [
            sum(result["unauthorized_hit_count"] == 0 for result in results),
            len(results),
        ],
        "stale_safe_case_rate": [
            sum(result["stale_hit_count"] == 0 for result in results),
            len(results),
        ],
    }
    return {
        "metrics": {
            f"recall_at_{top_k}": _safe_rate(total_relevant_hits, total_relevant),
            f"precision_at_{top_k}": _safe_rate(total_relevant_hits, ranking_slots),
            "mrr": _average(reciprocal_ranks),
            f"ndcg_at_{top_k}": _average(ndcg_values),
            "unauthorized_hit_count": unauthorized_hits,
            "stale_hit_rate": _safe_rate(stale_hits, retrieved_total),
        },
        "metric_counts": metric_counts,
        "case_count": len(cases),
        "cases": results,
    }


def _label_pass(label: dict[str, Any] | None, field: str, fallback: bool) -> bool:
    if not label:
        return fallback
    value = label.get(field)
    if value == "not_applicable":
        return fallback
    return value == "pass"


def evaluate_generation(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    human_labels: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate structure, fact, numeric, citation and refusal metrics."""
    prediction_by_id = _prediction_index(predictions)
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    denominators: Counter[str] = Counter()
    for case in cases:
        case_id = str(case["case_id"])
        prediction = prediction_by_id.get(case_id, {})
        label = (human_labels or {}).get(case_id)
        expected_facts = {str(value) for value in case["expected_facts"]}
        actual_facts = {str(value) for value in prediction.get("facts", [])}
        expected_citations = {str(value) for value in case["required_citations"]}
        citations = {str(value) for value in prediction.get("citations", [])}
        eligible_documents = {str(value) for value in case["relevant_document_ids"]}
        json_valid = prediction.get("json_valid") is True
        fact_hits = len(expected_facts & actual_facts)
        factuality_pass = fact_hits == len(expected_facts)
        factuality_pass = _label_pass(label, "factuality_label", factuality_pass)
        numeric_pass = _label_pass(
            label,
            "numeric_consistency_label",
            prediction.get("numeric_consistent") is True,
        )
        citation_hits = len(citations & eligible_documents)
        citation_required_hits = len(citations & expected_citations)
        citation_support_pass = _label_pass(
            label,
            "citation_support_label",
            citation_required_hits == len(expected_citations),
        )
        claims = prediction.get("claims", []) if isinstance(prediction.get("claims", []), list) else []
        supported_claims = 0
        unsupported_claims = 0
        forbidden_claims = 0
        forbidden_phrases = [str(value).lower() for value in case["forbidden_claims"]]
        for claim in claims:
            if isinstance(claim, dict):
                text = str(claim.get("text", ""))
                support = {str(value) for value in claim.get("supported_by", [])}
                supported = claim.get("supported") is True and bool(support & eligible_documents)
            else:
                text = str(claim)
                supported = False
            forbidden_claims += any(phrase in text.lower() for phrase in forbidden_phrases)
            if supported:
                supported_claims += 1
            else:
                unsupported_claims += 1
        expected_refusal = str(case["expected_decision"]).startswith("refuse_")
        refusal_correct = str(prediction.get("decision", "")) == str(case["expected_decision"])
        refusal_correct = _label_pass(label, "refusal_label", refusal_correct)
        counts["json_compliance"] += json_valid
        denominators["json_compliance"] += 1
        if expected_facts:
            counts["factual_correctness"] += factuality_pass
            denominators["factual_correctness"] += 1
        counts["numeric_consistency"] += numeric_pass
        denominators["numeric_consistency"] += 1
        if label and label.get("citation_support_label") != "not_applicable":
            counts["claim_support"] += citation_support_pass
            denominators["claim_support"] += 1
        else:
            counts["claim_support"] += supported_claims
            denominators["claim_support"] += len(claims)
        counts["citation_precision"] += citation_hits
        denominators["citation_precision"] += len(citations)
        counts["citation_completeness"] += citation_required_hits
        denominators["citation_completeness"] += len(expected_citations)
        counts["unsupported_claim"] += unsupported_claims + forbidden_claims
        denominators["unsupported_claim"] += len(claims) + forbidden_claims
        if expected_refusal:
            counts["correct_refusal"] += refusal_correct
            denominators["correct_refusal"] += 1
        results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "json_valid": json_valid,
                "factuality_pass": factuality_pass,
                "numeric_consistency_pass": numeric_pass,
                "citation_support_pass": citation_support_pass,
                "claim_support_rate": _safe_rate(supported_claims, len(claims)),
                "citation_precision": _safe_rate(citation_hits, len(citations)),
                "citation_completeness": _safe_rate(
                    citation_required_hits, len(expected_citations)
                ),
                "unsupported_claim_count": unsupported_claims + forbidden_claims,
                "expected_refusal": expected_refusal,
                "correct_refusal": refusal_correct if expected_refusal else None,
                "missing_prediction": case_id not in prediction_by_id,
            }
        )
    metric_counts = {
        key: [counts[key], denominators[key]]
        for key in (
            "json_compliance",
            "factual_correctness",
            "numeric_consistency",
            "claim_support",
            "citation_precision",
            "citation_completeness",
            "correct_refusal",
        )
    }
    return {
        "metrics": {
            "json_compliance": _safe_rate(counts["json_compliance"], denominators["json_compliance"]),
            "factual_correctness": _safe_rate(
                counts["factual_correctness"], denominators["factual_correctness"]
            ),
            "numeric_consistency": _safe_rate(
                counts["numeric_consistency"], denominators["numeric_consistency"]
            ),
            "claim_support_rate": _safe_rate(counts["claim_support"], denominators["claim_support"]),
            "citation_precision": _safe_rate(
                counts["citation_precision"], denominators["citation_precision"]
            ),
            "citation_completeness": _safe_rate(
                counts["citation_completeness"], denominators["citation_completeness"]
            ),
            "unsupported_claim_rate": _safe_rate(
                counts["unsupported_claim"], denominators["unsupported_claim"]
            ),
            "correct_refusal_rate": _safe_rate(
                counts["correct_refusal"], denominators["correct_refusal"]
            ),
        },
        "metric_counts": metric_counts,
        "case_count": len(cases),
        "cases": results,
    }


def evaluate_workflow(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate tool, validation, review, publication, latency and cost metrics."""
    prediction_by_id = _prediction_index(predictions)
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    latencies: list[float] = []
    actual_cost = 0.0
    estimated_cost = 0.0
    actual_cost_cases = 0
    estimated_cost_cases = 0
    for case in cases:
        case_id = str(case["case_id"])
        prediction = prediction_by_id.get(case_id, {})
        raw_arguments = prediction.get("arguments")
        arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
        tool_selection = prediction.get("selected_tool") == case["expected_tool"]
        required_arguments = all(name in arguments for name in case["required_arguments"])
        argument_values = all(
            arguments.get(name) == expected
            for name, expected in case["expected_arguments"].items()
        )
        argument_validity = required_arguments and argument_values
        execution_success = prediction.get("execution_success") is True
        rule_validation = (
            prediction.get("rule_validation_passed") is case["expected_rule_validation"]
        )
        review_routing = prediction.get("review_route") == case["expected_review_route"]
        publication_safety = (
            prediction.get("publication_allowed") is case["publication_allowed"]
        )
        metric_flags = {
            "tool_selection": tool_selection,
            "argument_validity": argument_validity,
            "execution_success": execution_success,
            "rule_validation": rule_validation,
            "review_routing_accuracy": review_routing,
            "publication_safety": publication_safety,
        }
        for name, flag in metric_flags.items():
            counts[name] += flag
        latency = prediction.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            latencies.append(float(latency))
        cost = prediction.get("cost_amount")
        cost_source = str(prediction.get("cost_source", "unavailable"))
        if isinstance(cost, (int, float)) and cost >= 0:
            if cost_source == "provider":
                actual_cost += float(cost)
                actual_cost_cases += 1
            else:
                estimated_cost += float(cost)
                estimated_cost_cases += 1
        results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                **metric_flags,
                "latency_ms": latency,
                "cost_amount": cost,
                "cost_source": cost_source,
                "missing_prediction": case_id not in prediction_by_id,
            }
        )
    denominator = len(cases)
    metric_counts = {name: [counts[name], denominator] for name in (
        "tool_selection",
        "argument_validity",
        "execution_success",
        "rule_validation",
        "review_routing_accuracy",
        "publication_safety",
    )}
    return {
        "metrics": {
            name: _safe_rate(counts[name], denominator) for name in metric_counts
        }
        | {
            "end_to_end_latency_ms": _average(latencies),
            "actual_cost_total": round(actual_cost, 8),
            "estimated_cost_total": round(estimated_cost, 8),
            "actual_cost_case_count": actual_cost_cases,
            "estimated_cost_case_count": estimated_cost_cases,
        },
        "metric_counts": metric_counts,
        "case_count": len(cases),
        "cases": results,
    }


def wilson_interval(numerator: int, denominator: int, z: float = 1.959964) -> dict[str, Any] | None:
    """Return a two-sided 95% Wilson interval for a binary proportion."""
    if denominator <= 0:
        return None
    proportion = numerator / denominator
    denominator_adjusted = 1 + z * z / denominator
    center = (proportion + z * z / (2 * denominator)) / denominator_adjusted
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / denominator + z * z / (4 * denominator * denominator)
        )
        / denominator_adjusted
    )
    return {
        "lower": round(max(0.0, center - margin), 6),
        "upper": round(min(1.0, center + margin), 6),
        "confidence_level": 0.95,
        "method": "wilson",
        "sample_size": denominator,
    }


def build_confidence_intervals(layers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    intervals: dict[str, Any] = {}
    for layer_name, layer in layers.items():
        for metric_name, values in layer.get("metric_counts", {}).items():
            numerator, denominator = int(values[0]), int(values[1])
            interval = wilson_interval(numerator, denominator)
            if interval is not None:
                intervals[f"{layer_name}.{metric_name}"] = interval
    return intervals


def classify_badcases(layers: dict[str, dict[str, Any]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Classify layer failures without collapsing them into one accuracy number."""
    badcases: list[dict[str, str]] = []
    for case in layers["retrieval"]["cases"]:
        if case["recall_at_k"] is not None and case["recall_at_k"] < 1:
            badcases.append({"case_id": case["case_id"], "label": "retrieval_miss"})
        if case["reciprocal_rank"] not in {0.0, 1.0}:
            badcases.append({"case_id": case["case_id"], "label": "ranking_error"})
        if case["unauthorized_hit_count"]:
            badcases.append({"case_id": case["case_id"], "label": "unauthorized_hit"})
        if case["stale_hit_count"]:
            badcases.append({"case_id": case["case_id"], "label": "stale_hit"})
    for case in layers["generation"]["cases"]:
        checks = {
            "json_error": not case["json_valid"],
            "factuality_error": not case["factuality_pass"],
            "numeric_error": not case["numeric_consistency_pass"],
            "citation_error": not case["citation_support_pass"],
            "unsupported_claim": bool(case["unsupported_claim_count"]),
            "refusal_error": case["correct_refusal"] is False,
        }
        badcases.extend(
            {"case_id": case["case_id"], "label": label}
            for label, failed in checks.items()
            if failed
        )
    for case in layers["workflow"]["cases"]:
        checks = {
            "tool_selection_error": not case["tool_selection"],
            "argument_error": not case["argument_validity"],
            "execution_error": not case["execution_success"],
            "rule_validation_error": not case["rule_validation"],
            "review_routing_error": not case["review_routing_accuracy"],
            "publication_safety_error": not case["publication_safety"],
        }
        badcases.extend(
            {"case_id": case["case_id"], "label": label}
            for label, failed in checks.items()
            if failed
        )
    distribution = dict(sorted(Counter(item["label"] for item in badcases).items()))
    return badcases, distribution
