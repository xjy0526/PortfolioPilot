"""Three-layer Retrieval, Generation and Workflow evaluation with badcases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReviewDecision, WorkflowRun, WorkflowStep
from evaluation.llm_eval import run_llm_evaluation_sync
from evaluation.reporting import (
    EVALUATION_MODES,
    build_evaluation_metadata,
    report_metadata_view,
    require_automated_runner_mode,
    validate_evaluation_report,
)
from evaluation.retrieval_eval import run_retrieval_evaluation
from evaluation.v2_runner import latest_v2_report_path
from prompts.registry import PromptRegistry
from rag.parsers import parse_document, structured_chunks
from workflows.research_report import ALLOWLISTED_TOOLS


BADCASE_LABELS = (
    "retrieval_miss", "ranking_error", "stale_evidence", "permission_error",
    "numeric_error", "citation_error", "prompt_error", "tool_error",
    "model_hallucination", "workflow_error",
)
DATASET_DIR = Path(__file__).resolve().parent / "datasets"


def run_full_evaluation(
    output_path: str | Path | None = None,
    *,
    top_k: int = 5,
    mode: str = "synthetic_smoke",
) -> dict[str, Any]:
    canonical_mode = require_automated_runner_mode(mode)
    generation = run_llm_evaluation_sync(mode=canonical_mode)
    retrieval = run_retrieval_evaluation(top_k=top_k)
    workflow = _empty_workflow_metrics()
    badcases = _build_badcases(retrieval, generation)
    report = {
        **build_evaluation_metadata(
            evaluation_mode=canonical_mode,
            model_provider=str(generation["model_provider"]),
            model_name=str(generation["model_name"]),
            dataset_name="portfolio_risk_and_retrieval_gold",
            dataset_version="v1",
            mock_response_used=bool(generation["mock_response_used"]),
            synthetic_data_used=True,
        ),
        # Compatibility alias for existing report consumers.
        "mode": canonical_mode,
        "evaluation_mode_taxonomy": list(EVALUATION_MODES),
        "evaluation_status": generation["evaluation_status"],
        "layers": {
            "retrieval": {
                "metrics": retrieval["metrics"],
                "case_count": retrieval["test_case_count"],
                "cases": retrieval["cases"],
            },
            "generation": {
                "metrics": {
                    "json_compliance_rate": generation["metrics"]["json_valid_rate"],
                    "risk_detection_rate": generation["metrics"]["risk_detection_rate"],
                    "numeric_consistency_rate": generation["metrics"]["numeric_consistency_rate"],
                    "groundedness": generation["metrics"]["groundedness_rate"],
                    "citation_precision": generation["metrics"]["citation_precision"],
                    "citation_completeness": generation["metrics"]["citation_completeness"],
                    "hallucination_rate": generation["metrics"]["hallucination_flag_rate"],
                    "refusal_correct_rate": generation["metrics"]["refusal_correct_rate"],
                    "citation_reference_validity": generation["metrics"]["citation_reference_validity"],
                    "claim_support_rate": generation["metrics"]["claim_support_rate"],
                },
                "case_count": generation["test_case_count"],
                "valid_response_case_count": generation["valid_response_case_count"],
                "metric_sample_sizes": generation["metric_sample_sizes"],
                "metric_disclosures": generation["metric_disclosures"],
                "cases": generation["cases"],
            },
            "workflow": workflow,
        },
        "test_sets": {
            "portfolio_risk_case_count": generation["test_case_count"],
            "public_retrieval_golden_count": retrieval["test_case_count"],
            "includes_permission_case": True,
            "includes_expired_case": True,
            "includes_insufficient_evidence_case": True,
            "includes_conflicting_evidence_case": True,
            "chunk_statistics": _chunk_statistics(),
            "versioned_gold_datasets": _gold_dataset_statistics(),
        },
        "badcases": badcases,
        "badcase_distribution": {label: sum(item["label"] == label for item in badcases) for label in BADCASE_LABELS},
    }
    validate_evaluation_report(report)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def evaluation_dashboard(
    session: AsyncSession,
    limit: int = 100,
    *,
    report_path: Path | None = None,
) -> dict[str, Any]:
    traces = await PromptRegistry(session).list_traces(limit)
    workflow_metrics = await _workflow_metrics(session)
    legacy_report_path = (
        Path(__file__).resolve().parent.parent / "cache" / "full_evaluation_report.json"
    )
    resolved_report_path = report_path or latest_v2_report_path() or legacy_report_path
    full_report = (
        json.loads(resolved_report_path.read_text(encoding="utf-8"))
        if resolved_report_path.exists()
        else {}
    )
    metadata = report_metadata_view(full_report)
    generation_layer = full_report.get("layers", {}).get("generation", {}) if metadata else {}
    generation_metrics = generation_layer.get("metrics", {})
    layer_metrics = (
        {
            name: layer.get("metrics", {})
            for name, layer in full_report.get("layers", {}).items()
            if isinstance(layer, dict)
        }
        if metadata
        else {}
    )
    evaluation_report = (
        {
            "status": "available",
            **metadata,
            "case_count": int(
                full_report.get("sample_count", generation_layer.get("case_count", 0))
            ),
            "valid_response_case_count": int(
                generation_layer.get("valid_response_case_count", 0)
            ),
            "data_cutoff": full_report.get("data_cutoff"),
            "human_reviewed_count": int(full_report.get("human_reviewed_count", 0)),
            "confidence_intervals": full_report.get("confidence_intervals", {}),
            "metric_summary": layer_metrics,
            "hallucination_flag_rate": generation_metrics.get("hallucination_rate"),
            "metric_disclosures": generation_layer.get("metric_disclosures", {}),
        }
        if metadata is not None
        else {
            "status": "unavailable",
            "reason": "No evaluation report with complete provenance is available",
        }
    )
    return {
        "metric_trends": _trace_trends(traces),
        "prompt_comparisons": [],
        "badcase_distribution": (
            full_report.get("badcase_distribution", {}) if metadata is not None else {}
        ),
        "evaluation_report": evaluation_report,
        "latency_cost": {
            "average_latency_ms": _average(traces, "duration_ms"),
            "total_cost_amount": round(
                sum(float(item.get("cost_amount") or 0.0) for item in traces), 8
            ),
            "contains_estimated_cost": any(
                item.get("cost_source") != "provider" for item in traces
            ),
        },
        "human_adoption_rate": workflow_metrics["metrics"]["human_adoption_rate"],
        "workflow": workflow_metrics,
        "traces": traces,
    }


async def _workflow_metrics(session: AsyncSession) -> dict[str, Any]:
    steps = list((await session.scalars(select(WorkflowStep))).all())
    runs = list((await session.scalars(select(WorkflowRun))).all())
    decisions = list((await session.scalars(select(ReviewDecision))).all())
    total_steps = len(steps)
    allowed_steps = sum(
        row.step_name in ALLOWLISTED_TOOLS and row.status == "COMPLETED" for row in steps
    )
    rule_attempts = sum(row.step_name == "run_compliance_rules" for row in steps)
    rule_passes = sum(
        row.step_name == "run_compliance_rules" and row.status == "COMPLETED" for row in steps
    )
    decision_count = len(decisions)
    approved = sum(row.decision == "approve" for row in decisions)
    changes = sum(row.decision == "request_changes" for row in decisions)
    latencies = []
    for row in runs:
        if row.started_at and row.completed_at:
            latencies.append((row.completed_at - row.started_at).total_seconds() * 1000)
    run_count = len(runs)
    failed = sum(row.status == "FAILED" for row in runs)
    return {
        "metrics": {
            "workflow_allowlist_completion_rate": (
                round(allowed_steps / total_steps, 4) if total_steps else 0.0
            ),
            **_tool_call_metrics(),
            "rule_validation_pass_rate": round(rule_passes / rule_attempts, 4) if rule_attempts else 0.0,
            "human_adoption_rate": round(approved / decision_count, 4) if decision_count else 0.0,
            "modification_rate": round(changes / decision_count, 4) if decision_count else 0.0,
            "end_to_end_latency_ms": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
            "cost_per_task": round(sum(float(row.cost_amount) for row in runs) / run_count, 8) if run_count else 0.0,
            "estimated_cost_task_rate": (
                round(sum(row.cost_is_estimated for row in runs) / run_count, 4)
                if run_count else 0.0
            ),
            "failure_rate": round(failed / run_count, 4) if run_count else 0.0,
        },
        "run_count": run_count,
        "data_source": "postgresql_workflow_runs",
    }


def _empty_workflow_metrics() -> dict[str, Any]:
    return {
        "metrics": {
            "workflow_allowlist_completion_rate": 0.0,
            **_tool_call_metrics(),
            "rule_validation_pass_rate": 0.0,
            "human_adoption_rate": 0.0,
            "modification_rate": 0.0,
            "end_to_end_latency_ms": 0.0,
            "cost_per_task": 0.0,
            "estimated_cost_task_rate": 0.0,
            "failure_rate": 0.0,
        },
        "run_count": 0,
        "data_source": "synthetic_static_only",
    }


def _tool_call_metrics() -> dict[str, float]:
    cases = _load_jsonl(DATASET_DIR / "tool_call_gold_v1.jsonl")
    if not cases:
        return {
            "tool_selection_accuracy": 0.0,
            "required_argument_accuracy": 0.0,
            "argument_value_accuracy": 0.0,
            "tool_execution_success_rate": 0.0,
            "tool_call_accuracy": 0.0,
        }
    selection = []
    required = []
    values = []
    execution = []
    for case in cases:
        expected = case["expected_tool_call"]
        actual = case["actual_tool_call"]
        selection.append(actual.get("tool") == expected.get("tool"))
        required_names = expected.get("required_arguments", [])
        actual_args = actual.get("arguments", {})
        expected_args = expected.get("arguments", {})
        required.append(all(name in actual_args for name in required_names))
        values.append(all(actual_args.get(name) == value for name, value in expected_args.items()))
        execution.append(bool(case.get("execution_success")))

    def ratio(flags: list[bool]) -> float:
        return round(sum(flags) / len(flags), 4)

    components = [ratio(selection), ratio(required), ratio(values), ratio(execution)]
    return {
        "tool_selection_accuracy": components[0],
        "required_argument_accuracy": components[1],
        "argument_value_accuracy": components[2],
        "tool_execution_success_rate": components[3],
        "tool_call_accuracy": round(sum(components) / len(components), 4),
    }


def _gold_dataset_statistics() -> dict[str, int]:
    return {
        path.name: len(_load_jsonl(path))
        for path in sorted(DATASET_DIR.glob("*_gold_v1.jsonl"))
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _build_badcases(retrieval: dict[str, Any], generation: dict[str, Any]) -> list[dict[str, Any]]:
    badcases = []
    for case in retrieval["cases"]:
        if case["relevant_document_count"] and float(case.get("recall_at_k") or 0) < 1:
            badcases.append({"case_id": case["case_id"], "label": "retrieval_miss"})
        if case["expired_hit_count"]:
            badcases.append({"case_id": case["case_id"], "label": "stale_evidence"})
        if case["unauthorized_hit_count"]:
            badcases.append({"case_id": case["case_id"], "label": "permission_error"})
    for case in generation["cases"]:
        if not case.get("numeric_consistent", True):
            badcases.append({"case_id": case["id"], "label": "numeric_error"})
        if float(case.get("citation_precision", 1.0)) < 1:
            badcases.append({"case_id": case["id"], "label": "citation_error"})
        if case.get("hallucination_flag"):
            badcases.append({"case_id": case["id"], "label": "model_hallucination"})
        if not case.get("json_valid"):
            badcases.append({"case_id": case["id"], "label": "prompt_error"})
    return badcases


def _chunk_statistics() -> dict[str, Any]:
    samples = {
        "zh": "# 公募基金风险\n\n## 集中度\n\n组合需要关注行业集中度和回撤风险。",
        "en": "# Public Fund Risk\n\n## Concentration\n\nThe portfolio requires concentration and drawdown review.",
    }
    result = {}
    for language, text in samples.items():
        _, blocks, _ = parse_document(text.encode(), f"sample_{language}.md", language)
        chunks = structured_chunks(blocks)
        result[language] = {
            "characters": len(text), "blocks": len(blocks), "chunks": len(chunks),
            "average_chunk_characters": round(sum(len(item["text"]) for item in chunks) / len(chunks), 2),
        }
    return result


def _trace_trends(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "created_at": item["created_at"],
            "business_scene": item["business_scene"],
            "duration_ms": item.get("duration_ms", 0),
            "cost_amount": item.get("cost_amount"),
            "cost_source": item.get("cost_source"),
            "output_schema_valid": item.get("output_schema_valid"),
            "status": item["status"],
        }
        for item in reversed(traces)
    ]


def _average(items: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(item.get(field, 0.0)) for item in items) / len(items), 4) if items else 0.0
