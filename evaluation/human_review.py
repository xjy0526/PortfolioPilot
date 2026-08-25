"""Human review packet preparation and adjudication queue helpers."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from evaluation.human_gold_dataset import (
    LABEL_VALUE_FIELDS,
    HumanGoldDataset,
    file_sha256,
    load_jsonl,
    write_jsonl,
)


def with_review_files(
    dataset: HumanGoldDataset,
    *,
    reviewer_a_path: Path | None = None,
    reviewer_b_path: Path | None = None,
    adjudicated_path: Path | None = None,
) -> HumanGoldDataset:
    """Return a validated dataset using external reviewer working files."""
    updated = replace(
        dataset,
        reviewer_a_labels=(
            load_jsonl(reviewer_a_path)
            if reviewer_a_path is not None
            else dataset.reviewer_a_labels
        ),
        reviewer_b_labels=(
            load_jsonl(reviewer_b_path)
            if reviewer_b_path is not None
            else dataset.reviewer_b_labels
        ),
        adjudicated_labels=(
            load_jsonl(adjudicated_path)
            if adjudicated_path is not None
            else dataset.adjudicated_labels
        ),
    )
    updated.validate()
    return updated


def prepare_review_packets(
    dataset: HumanGoldDataset,
    *,
    reviewer_a_id: str,
    reviewer_b_id: str,
    output_dir: Path,
    prediction_path: Path | None = None,
) -> dict[str, Any]:
    """Assign user-supplied identities while leaving every conclusion pending."""
    reviewer_a = reviewer_a_id.strip()
    reviewer_b = reviewer_b_id.strip()
    if not reviewer_a or not reviewer_b:
        raise ValueError("both reviewer identities are required")
    if reviewer_a == reviewer_b:
        raise ValueError("Reviewer A and Reviewer B must be different identities")
    prediction_digest = file_sha256(prediction_path) if prediction_path else None
    a_rows = [
        _assigned_pending_label(row, reviewer_a, prediction_digest)
        for row in dataset.reviewer_a_labels
    ]
    b_rows = [
        _assigned_pending_label(row, reviewer_b, prediction_digest)
        for row in dataset.reviewer_b_labels
    ]
    write_jsonl(output_dir / "labels_reviewer_a.jsonl", a_rows)
    write_jsonl(output_dir / "labels_reviewer_b.jsonl", b_rows)
    assignment = {
        "dataset_name": dataset.manifest["dataset_name"],
        "dataset_version": dataset.version,
        "candidate_case_count": len(dataset.candidate_cases),
        "reviewer_a_id": reviewer_a,
        "reviewer_b_id": reviewer_b,
        "prediction_sha256": prediction_digest,
        "review_status": "pending",
        "automated_approval_performed": False,
        "instructions": (
            "Each reviewer must independently set automation_generated=false, "
            "label_origin=human_review, reviewed_at, review_status and label values."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_assignment.json").write_text(
        json.dumps(assignment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return assignment


def build_adjudication_queue(
    dataset: HumanGoldDataset,
    *,
    output_path: Path,
) -> dict[str, Any]:
    """Create a pending queue after two real reviewers complete their files."""
    pair = dataset.require_completed_review_pair()
    reviewer_a = {str(row["case_id"]): row for row in dataset.reviewer_a_labels}
    reviewer_b = {str(row["case_id"]): row for row in dataset.reviewer_b_labels}
    rows: list[dict[str, Any]] = []
    for case_id in dataset.case_ids:
        left = reviewer_a[case_id]
        right = reviewer_b[case_id]
        conflicts = list(pair.conflict_fields.get(case_id, ()))
        agreed_approved = (
            not conflicts
            and left["review_status"] == right["review_status"] == "approved"
        )
        row: dict[str, Any] = {
            "case_id": case_id,
            "reviewer_id": None,
            "review_status": "pending",
            "retrieval_relevance": (
                left["retrieval_relevance"] if agreed_approved else None
            ),
            "citation_supported": (
                left["citation_supported"] if agreed_approved else None
            ),
            "numeric_consistency": (
                left["numeric_consistency"] if agreed_approved else None
            ),
            "answer_completeness": (
                left["answer_completeness"] if agreed_approved else None
            ),
            "unsupported_claim": (
                left["unsupported_claim"] if agreed_approved else None
            ),
            "appropriate_refusal": (
                left["appropriate_refusal"] if agreed_approved else None
            ),
            "severity": left["severity"] if agreed_approved else None,
            "notes": "Awaiting independent human adjudication.",
            "reviewed_at": None,
            "source_version": dataset.version,
            "prediction_sha256": (
                left["prediction_sha256"] if agreed_approved else None
            ),
            "label_origin": "automated_adjudication_template",
            "automation_generated": True,
            "reviewer_a_id": pair.reviewer_a_id,
            "reviewer_b_id": pair.reviewer_b_id,
            "conflict_fields": conflicts,
            "reviewer_inputs": {
                "reviewer_a": _reviewer_input(left),
                "reviewer_b": _reviewer_input(right),
            },
        }
        rows.append(row)
    write_jsonl(output_path, rows)
    return {
        "output": str(output_path),
        "dataset_version": dataset.version,
        "case_count": len(rows),
        "agreement_count": len(pair.agreed_case_ids),
        "conflict_count": len(pair.conflict_case_ids),
        "review_status": "pending",
        "automated_approval_performed": False,
    }


def review_status_summary(dataset: HumanGoldDataset) -> dict[str, Any]:
    pair = dataset.review_pair_status()
    adjudicated_counts = {status: 0 for status in ("pending", "approved", "rejected")}
    for label in dataset.adjudicated_labels:
        adjudicated_counts[str(label["review_status"])] += 1
    readiness_reason: str | None = None
    try:
        dataset.require_adjudicated_labels()
        human_gold_ready = True
    except (RuntimeError, ValueError) as exc:
        human_gold_ready = False
        readiness_reason = str(exc)
    return {
        "dataset_name": dataset.manifest["dataset_name"],
        "dataset_version": dataset.version,
        "candidate_case_count": len(dataset.candidate_cases),
        "review_pair": pair.as_dict(),
        "adjudicated_status_counts": adjudicated_counts,
        "required_adjudication_case_count": len(pair.conflict_case_ids),
        "unresolved_required_adjudication_count": sum(
            label["review_status"] == "pending"
            and str(label["case_id"]) in pair.conflict_case_ids
            for label in dataset.adjudicated_labels
        ),
        "human_gold_ready": human_gold_ready,
        "human_gold_block_reason": readiness_reason,
        "automated_approval_performed": False,
    }


def _assigned_pending_label(
    template: dict[str, Any], reviewer_id: str, prediction_digest: str | None
) -> dict[str, Any]:
    return {
        **template,
        "reviewer_id": reviewer_id,
        "review_status": "pending",
        "reviewed_at": None,
        "prediction_sha256": prediction_digest,
        "label_origin": "generated_review_packet",
        "automation_generated": True,
    }


def _reviewer_input(label: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_status": label["review_status"],
        **{field: label[field] for field in LABEL_VALUE_FIELDS},
        "notes": label["notes"],
        "reviewed_at": label["reviewed_at"],
        "prediction_sha256": label["prediction_sha256"],
    }
