"""Three-layer Retrieval, Generation and Workflow evaluation with badcases."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import _get_conn
from evaluation.llm_eval import run_llm_evaluation_sync
from evaluation.retrieval_eval import run_retrieval_evaluation
from prompts.registry import PromptRegistry
from rag.parsers import parse_document, structured_chunks
from workflows.research_report import ALLOWLISTED_TOOLS, ensure_workflow_schema


BADCASE_LABELS = (
    "retrieval_miss", "ranking_error", "stale_evidence", "permission_error",
    "numeric_error", "citation_error", "prompt_error", "tool_error",
    "model_hallucination", "workflow_error",
)


def run_full_evaluation(output_path: str | Path | None = None, *, top_k: int = 5) -> dict[str, Any]:
    retrieval = run_retrieval_evaluation(top_k=top_k)
    generation = run_llm_evaluation_sync(use_mock=True)
    workflow = _workflow_metrics()
    badcases = _build_badcases(retrieval, generation)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
                },
                "case_count": generation["test_case_count"],
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
        },
        "badcases": badcases,
        "badcase_distribution": {label: sum(item["label"] == label for item in badcases) for label in BADCASE_LABELS},
    }
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def evaluation_dashboard(limit: int = 100) -> dict[str, Any]:
    registry = PromptRegistry()
    traces = registry.list_traces(limit)
    conn = registry.conn
    prompt_rows = conn.execute(
        "SELECT * FROM prompt_evaluation_results ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    comparisons = []
    for row in prompt_rows:
        item = dict(row)
        for field in ("metrics_a", "metrics_b", "metric_delta"):
            item[field] = json.loads(item[field])
        comparisons.append(item)
    report_path = Path(__file__).resolve().parent.parent / "cache" / "full_evaluation_report.json"
    full_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    return {
        "metric_trends": _trace_trends(traces),
        "prompt_comparisons": comparisons,
        "badcase_distribution": full_report.get("badcase_distribution", {}),
        "latency_cost": {
            "average_latency_ms": _average(traces, "latency_ms"),
            "total_estimated_cost": round(sum(float(item.get("estimated_cost", 0.0)) for item in traces), 8),
        },
        "human_adoption_rate": _workflow_metrics()["metrics"]["human_adoption_rate"],
        "traces": traces,
    }


def _workflow_metrics() -> dict[str, Any]:
    conn = _get_conn()
    ensure_workflow_schema(conn)
    completed_steps = conn.execute("SELECT COUNT(*) FROM workflow_steps WHERE status='COMPLETED'").fetchone()[0]
    total_steps = conn.execute("SELECT COUNT(*) FROM workflow_steps").fetchone()[0]
    step_rows = conn.execute("SELECT step_name, status FROM workflow_steps").fetchall()
    allowed_steps = sum(row[0] in ALLOWLISTED_TOOLS and row[1] == "COMPLETED" for row in step_rows)
    rule_attempts = sum(row[0] == "run_compliance_rules" for row in step_rows)
    rule_passes = sum(row[0] == "run_compliance_rules" and row[1] == "COMPLETED" for row in step_rows)
    runs = conn.execute("SELECT * FROM workflow_runs").fetchall()
    decisions = conn.execute("SELECT decision FROM review_decisions").fetchall()
    decision_count = len(decisions)
    approved = sum(row[0] == "approve" for row in decisions)
    changes = sum(row[0] == "request_changes" for row in decisions)
    latencies = []
    for row in runs:
        if row["started_at"] and row["completed_at"]:
            start = datetime.fromisoformat(row["started_at"])
            end = datetime.fromisoformat(row["completed_at"])
            latencies.append((end - start).total_seconds() * 1000)
    run_count = len(runs)
    failed = sum(row["status"] == "FAILED" for row in runs)
    return {
        "metrics": {
            "tool_call_accuracy": round(allowed_steps / total_steps, 4) if total_steps else 0.0,
            "rule_validation_pass_rate": round(rule_passes / rule_attempts, 4) if rule_attempts else 0.0,
            "human_adoption_rate": round(approved / decision_count, 4) if decision_count else 0.0,
            "modification_rate": round(changes / decision_count, 4) if decision_count else 0.0,
            "end_to_end_latency_ms": round(sum(latencies) / len(latencies), 4) if latencies else 0.0,
            "cost_per_task": round(sum(float(row["estimated_cost"]) for row in runs) / run_count, 8) if run_count else 0.0,
            "failure_rate": round(failed / run_count, 4) if run_count else 0.0,
        },
        "run_count": run_count,
    }


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
            "created_at": item["created_at"], "prompt_id": item["prompt_id"],
            "prompt_version": item["prompt_version"], "latency_ms": item["latency_ms"],
            "estimated_cost": item["estimated_cost"], "schema_valid": bool(item["output_schema_valid"]),
        }
        for item in reversed(traces)
    ]


def _average(items: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(item.get(field, 0.0)) for item in items) / len(items), 4) if items else 0.0
