"""Governance and metric regression tests for human-gold evaluation V3."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evaluation.human_gold_dataset import (
    DATASET_DIR,
    HumanGoldDataset,
    file_sha256,
    write_jsonl,
)
from evaluation.human_gold_metrics import (
    RETRIEVER_VARIANTS,
    build_human_gold_report,
    build_live_model_provenance_report,
    require_live_provider_credential,
    validate_prediction_bundle,
)
from evaluation.human_review import (
    build_adjudication_queue,
    prepare_review_packets,
    with_review_files,
)
from evaluation.reporting import validate_evaluation_report
from evaluation.adjudicate_human_labels import main as adjudicate_cli
from evaluation.prepare_human_review import main as prepare_cli
from evaluation.run_human_gold_eval import main as run_human_gold_cli
from evaluation.validate_human_labels import main as validate_cli
from scripts.build_human_gold_v3_dataset import build_dataset, main as build_dataset_cli


EXPECTED_CATEGORIES = {
    "citation_entailment": 5,
    "cross_document_comparison": 5,
    "evidence_insufficient": 4,
    "numeric_consistency": 5,
    "permission_filtering": 4,
    "point_in_time_sensitivity": 4,
    "portfolio_risk_explanation": 6,
    "refusal_safety": 3,
    "single_document_fact_qa": 5,
    "workflow_rule_boundary": 4,
}


def _dataset(tmp_path: Path) -> HumanGoldDataset:
    root = tmp_path / "v3"
    build_dataset(root)
    return HumanGoldDataset.load(root)


def _prediction_bundle(
    dataset: HumanGoldDataset,
    *,
    mode: str = "human_gold_eval",
) -> dict[str, Any]:
    retriever_cases: list[dict[str, Any]] = []
    generation: list[dict[str, Any]] = []
    for index, case in enumerate(dataset.candidate_cases):
        document_id = str(case["candidate_document_ids"][0])
        retriever_cases.append(
            {
                "case_id": case["case_id"],
                "retrieved": [{"document_id": document_id, "score": 1.0}],
                "latency_ms": 10 + index,
            }
        )
        expected_refusal = case["category"] in {
            "evidence_insufficient",
            "refusal_safety",
        }
        generation.append(
            {
                "case_id": case["case_id"],
                "citation_document_ids": [document_id],
                "refused": expected_refusal,
                "latency_ms": 20 + index,
            }
        )
    return {
        "metadata": {
            "evaluation_mode": mode,
            "dataset_name": dataset.manifest["dataset_name"],
            "dataset_version": dataset.version,
            "model_provider": "qwen",
            "model_name": "test-live-model",
            "prompt_key": "portfolio-research",
            "prompt_version": "test-v1",
            "temperature": 0.0,
            "git_commit_sha": "a" * 40,
            "generated_at": "2026-08-24T00:00:00Z",
            "mock_response_used": False,
            "real_model_used": True,
            "response_source": "live_model",
            "raw_output_policy": "not_stored",
        },
        "retrievers": {
            "bm25_fts_only": {"available": True, "cases": retriever_cases},
            "vector_only": {"available": True, "cases": retriever_cases},
            "hybrid_rrf": {"available": True, "cases": retriever_cases},
            "hybrid_rrf_reranker": {
                "available": False,
                "unavailable_reason": "reranker_not_configured",
            },
        },
        "generation": generation,
    }


def _write_bundle(dataset: HumanGoldDataset, path: Path, *, mode: str = "human_gold_eval") -> str:
    path.write_text(
        json.dumps(_prediction_bundle(dataset, mode=mode), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return file_sha256(path)


def _human_label(
    template: dict[str, Any],
    *,
    reviewer_id: str,
    prediction_sha256: str,
    candidate_document_id: str,
    citation_supported: bool = True,
    appropriate_refusal: bool = False,
    status: str = "approved",
) -> dict[str, Any]:
    completed = {
        **template,
        "reviewer_id": reviewer_id,
        "review_status": status,
        "notes": "Completed by a real test-fixture reviewer identity.",
        "reviewed_at": "2026-08-24T01:00:00Z",
        "prediction_sha256": prediction_sha256,
        "label_origin": "human_review",
        "automation_generated": False,
    }
    if status == "approved":
        completed.update(
            {
                "retrieval_relevance": {candidate_document_id: 3},
                "citation_supported": citation_supported,
                "numeric_consistency": "pass",
                "answer_completeness": "complete",
                "unsupported_claim": False,
                "appropriate_refusal": appropriate_refusal,
                "severity": "none",
            }
        )
    return completed


def _complete_reviews(
    dataset: HumanGoldDataset,
    *,
    prediction_sha256: str,
    reviewer_a: str = "human-a",
    reviewer_b: str = "human-b",
    adjudicator: str = "human-adjudicator",
    conflict_case_id: str | None = None,
    reviewer_b_pending: bool = False,
    reject_all: bool = False,
    version_override: str | None = None,
) -> None:
    a_rows: list[dict[str, Any]] = []
    b_rows: list[dict[str, Any]] = []
    adjudicated_rows: list[dict[str, Any]] = []
    for case, a_template, b_template, adjudication_template in zip(
        dataset.candidate_cases,
        dataset.reviewer_a_labels,
        dataset.reviewer_b_labels,
        dataset.adjudicated_labels,
        strict=True,
    ):
        case_id = str(case["case_id"])
        document_id = str(case["candidate_document_ids"][0])
        status = "rejected" if reject_all else "approved"
        expected_refusal = case["category"] in {
            "evidence_insufficient",
            "refusal_safety",
        }
        a = _human_label(
            a_template,
            reviewer_id=reviewer_a,
            prediction_sha256=prediction_sha256,
            candidate_document_id=document_id,
            appropriate_refusal=expected_refusal,
            status=status,
        )
        if reviewer_b_pending:
            b = {**b_template, "reviewer_id": reviewer_b}
        else:
            b = _human_label(
                b_template,
                reviewer_id=reviewer_b,
                prediction_sha256=prediction_sha256,
                candidate_document_id=document_id,
                citation_supported=case_id != conflict_case_id,
                appropriate_refusal=expected_refusal,
                status=status,
            )
        if version_override and case_id == dataset.case_ids[0]:
            a["source_version"] = version_override
        a_rows.append(a)
        b_rows.append(b)

        conflict_fields = (
            ["citation_supported"] if case_id == conflict_case_id else []
        )
        if conflict_fields:
            adjudicated_rows.append(
                {
                    **adjudication_template,
                    "reviewer_a_id": reviewer_a,
                    "reviewer_b_id": reviewer_b,
                    "conflict_fields": conflict_fields,
                }
            )
            continue
        adjudicated = _human_label(
            adjudication_template,
            reviewer_id=adjudicator,
            prediction_sha256=prediction_sha256,
            candidate_document_id=document_id,
            appropriate_refusal=expected_refusal,
            status=status,
        )
        adjudicated.update(
            {
                "reviewer_a_id": reviewer_a,
                "reviewer_b_id": reviewer_b,
                "conflict_fields": [],
            }
        )
        adjudicated_rows.append(adjudicated)
    write_jsonl(dataset.root / "labels_reviewer_a.jsonl", a_rows)
    write_jsonl(dataset.root / "labels_reviewer_b.jsonl", b_rows)
    write_jsonl(dataset.root / "adjudicated_labels.jsonl", adjudicated_rows)


def test_committed_v3_dataset_has_45_pending_cases_and_no_human_claims() -> None:
    dataset = HumanGoldDataset.load(DATASET_DIR)

    assert len(dataset.candidate_cases) == 45
    assert dataset.manifest["category_distribution"] == EXPECTED_CATEGORIES
    assert all(label["review_status"] == "pending" for label in dataset.reviewer_a_labels)
    assert all(label["review_status"] == "pending" for label in dataset.reviewer_b_labels)
    assert all(label["review_status"] == "pending" for label in dataset.adjudicated_labels)
    assert all(label["reviewer_id"] is None for label in dataset.reviewer_a_labels)
    with pytest.raises(RuntimeError, match="two independently identified"):
        dataset.require_adjudicated_labels()


def test_v3_builder_is_deterministic(tmp_path: Path) -> None:
    generated_manifest = build_dataset(tmp_path / "generated-v3")
    committed = HumanGoldDataset.load(DATASET_DIR)

    assert generated_manifest["checksum"] == committed.manifest["checksum"]
    assert generated_manifest["category_distribution"] == EXPECTED_CATEGORIES


def test_zero_approved_adjudicated_labels_are_rejected(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(dataset, prediction_sha256=digest, reject_all=True)

    completed = HumanGoldDataset.load(dataset.root)
    with pytest.raises(RuntimeError, match="at least one approved adjudicated"):
        completed.require_adjudicated_labels()


def test_single_reviewer_is_insufficient(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(dataset, prediction_sha256=digest, reviewer_b_pending=True)

    incomplete = HumanGoldDataset.load(dataset.root)
    with pytest.raises(RuntimeError, match="must complete every candidate"):
        incomplete.require_completed_review_pair()


def test_same_reviewer_identity_is_rejected(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(
        dataset,
        prediction_sha256=digest,
        reviewer_a="same-human",
        reviewer_b="same-human",
    )

    with pytest.raises(ValueError, match="must be different identities"):
        HumanGoldDataset.load(dataset.root)


def test_unresolved_conflict_blocks_human_gold(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(
        dataset,
        prediction_sha256=digest,
        conflict_case_id=dataset.case_ids[0],
    )

    conflicted = HumanGoldDataset.load(dataset.root)
    with pytest.raises(RuntimeError, match="conflict requires completed adjudication"):
        conflicted.require_adjudicated_labels()


def test_independent_adjudicator_resolves_a_reviewer_conflict(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    digest = _write_bundle(dataset, prediction_path)
    conflict_case_id = dataset.case_ids[0]
    _complete_reviews(
        dataset,
        prediction_sha256=digest,
        conflict_case_id=conflict_case_id,
    )
    rows = jsonl(dataset.root / "adjudicated_labels.jsonl")
    case = dataset.candidate_cases[0]
    rows[0] = _human_label(
        rows[0],
        reviewer_id="human-adjudicator",
        prediction_sha256=digest,
        candidate_document_id=str(case["candidate_document_ids"][0]),
        citation_supported=True,
    )
    write_jsonl(dataset.root / "adjudicated_labels.jsonl", rows)
    adjudicated = HumanGoldDataset.load(dataset.root)

    report = build_human_gold_report(
        dataset=adjudicated,
        bundle=json.loads(prediction_path.read_text(encoding="utf-8")),
        prediction_path=prediction_path,
    )

    assert report["approved_human_gold_count"] == 45
    assert report["approved_consensus_count"] == 44
    assert report["approved_adjudicated_count"] == 1


def test_two_reviewer_agreement_and_adjudication_enable_metrics(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    digest = _write_bundle(dataset, prediction_path)
    _complete_reviews(dataset, prediction_sha256=digest)
    completed = HumanGoldDataset.load(dataset.root)

    report = build_human_gold_report(
        dataset=completed,
        bundle=json.loads(prediction_path.read_text(encoding="utf-8")),
        prediction_path=prediction_path,
    )

    validate_evaluation_report(report)
    assert report["evaluation_mode"] == "human_gold_eval"
    assert report["sample_count"] == 45
    assert report["mock_response_used"] is False
    assert report["human_label_used"] is True
    assert report["mode_isolation"]["synthetic_smoke_metrics_included"] is False
    assert report["retriever_comparison"]["bm25_fts_only"]["metrics"] == {
        "recall_at_5": 1.0,
        "mrr": 1.0,
        "ndcg_at_5": 1.0,
        "p50_latency_ms": 32.0,
        "p95_latency_ms": 51.8,
        "sample_count": 45,
    }
    assert report["retriever_comparison"]["hybrid_rrf_reranker"]["status"] == "unavailable"
    assert report["generation_metrics"]["citation_precision"] == 1.0
    assert report["generation_metrics"]["citation_support_rate"] == 1.0
    assert report["generation_metrics"]["citation_case_count"] == 45
    assert report["generation_metrics"]["evidence_insufficient_accuracy"] == 1.0
    assert report["generation_metrics"]["evidence_insufficient_sample_count"] == 4
    assert report["generation_metrics"]["refusal_precision"] == 1.0
    assert report["generation_metrics"]["p50_latency_ms"] == 42.0
    assert report["generation_metrics"]["p95_latency_ms"] == 61.8
    assert report["prediction_generated_at"] == "2026-08-24T00:00:00Z"
    assert set(report["review_label_sha256"]) == {
        "reviewer_a",
        "reviewer_b",
        "adjudicated",
    }
    assert report["dataset_immutable_checksums"] == completed.manifest["checksum"][
        "immutable_files"
    ]


def test_answer_completeness_excludes_not_applicable_labels(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    digest = _write_bundle(dataset, prediction_path)
    _complete_reviews(dataset, prediction_sha256=digest)
    for filename in (
        "labels_reviewer_a.jsonl",
        "labels_reviewer_b.jsonl",
        "adjudicated_labels.jsonl",
    ):
        rows = jsonl(dataset.root / filename)
        rows[0]["answer_completeness"] = "not_applicable"
        write_jsonl(dataset.root / filename, rows)
    completed = HumanGoldDataset.load(dataset.root)

    report = build_human_gold_report(
        dataset=completed,
        bundle=json.loads(prediction_path.read_text(encoding="utf-8")),
        prediction_path=prediction_path,
    )

    assert report["generation_metrics"]["answer_completeness_rate"] == 1.0
    assert report["generation_metrics"]["answer_completeness_sample_count"] == 44


def test_two_reviewer_consensus_does_not_invent_a_third_reviewer(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    digest = _write_bundle(dataset, prediction_path)
    _complete_reviews(dataset, prediction_sha256=digest)
    write_jsonl(dataset.root / "adjudicated_labels.jsonl", dataset.adjudicated_labels)
    consensus = HumanGoldDataset.load(dataset.root)

    approved = consensus.require_adjudicated_labels()
    report = build_human_gold_report(
        dataset=consensus,
        bundle=json.loads(prediction_path.read_text(encoding="utf-8")),
        prediction_path=prediction_path,
    )

    assert len(approved) == 45
    assert {label["reviewer_id"] for label in approved} == {"human-a"}
    assert report["approved_human_gold_count"] == 45
    assert report["approved_consensus_count"] == 45
    assert report["approved_adjudicated_count"] == 0
    assert all(
        label["review_status"] == "pending"
        for label in consensus.adjudicated_labels
    )


def test_dataset_version_mismatch_is_rejected(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(
        dataset,
        prediction_sha256=digest,
        version_override="2.0.0",
    )

    with pytest.raises(ValueError, match="dataset version mismatch"):
        HumanGoldDataset.load(dataset.root)


def test_completed_labels_require_prediction_provenance(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(dataset, prediction_sha256=digest, reject_all=True)
    rows = jsonl(dataset.root / "labels_reviewer_a.jsonl")
    rows[0]["prediction_sha256"] = None
    write_jsonl(dataset.root / "labels_reviewer_a.jsonl", rows)

    with pytest.raises(ValueError, match="requires prediction_sha256"):
        HumanGoldDataset.load(dataset.root)


def test_reviewers_must_review_the_same_prediction_bundle(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(dataset, prediction_sha256=digest)
    rows = jsonl(dataset.root / "labels_reviewer_b.jsonl")
    rows[0]["prediction_sha256"] = "b" * 64
    write_jsonl(dataset.root / "labels_reviewer_b.jsonl", rows)
    reviewed = HumanGoldDataset.load(dataset.root)

    with pytest.raises(ValueError, match="same prediction bundle"):
        reviewed.require_completed_review_pair()


def test_live_model_requires_credential_without_mock_fallback(monkeypatch) -> None:
    monkeypatch.setattr("evaluation.human_gold_metrics.settings.QWEN_API_KEY", "")

    with pytest.raises(RuntimeError, match="mock fallback is forbidden"):
        require_live_provider_credential("qwen", environ={})


def test_live_model_cli_without_credential_does_not_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "live-predictions.json"
    _write_bundle(dataset, prediction_path, mode="live_model_eval")
    monkeypatch.setattr("evaluation.human_gold_metrics.settings.QWEN_API_KEY", "")
    monkeypatch.delenv("QWEN_API_KEY", raising=False)

    assert (
        run_human_gold_cli(
            [
                "--mode",
                "live_model_eval",
                "--dataset-dir",
                str(dataset.root),
                "--predictions",
                str(prediction_path),
                "--output",
                str(tmp_path / "must-not-exist.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "must-not-exist.json").exists()


def test_mode_isolation_rejects_synthetic_and_production_in_human_cli(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    _write_bundle(dataset, prediction_path, mode="synthetic_smoke")

    assert (
        run_human_gold_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--predictions",
                str(prediction_path),
                "--mode",
                "synthetic_smoke",
            ]
        )
        == 2
    )
    _write_bundle(dataset, prediction_path, mode="production_monitoring")
    assert (
        run_human_gold_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--predictions",
                str(prediction_path),
                "--mode",
                "production_monitoring",
            ]
        )
        == 2
    )


def test_review_packet_and_adjudication_queue_never_auto_approve(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    packet_dir = tmp_path / "packets"
    assignment = prepare_review_packets(
        dataset,
        reviewer_a_id="human-a",
        reviewer_b_id="human-b",
        output_dir=packet_dir,
    )

    assert assignment["automated_approval_performed"] is False
    assert all(
        row["review_status"] == "pending"
        for row in jsonl(packet_dir / "labels_reviewer_a.jsonl")
    )
    assigned = with_review_files(
        dataset,
        reviewer_a_path=packet_dir / "labels_reviewer_a.jsonl",
        reviewer_b_path=packet_dir / "labels_reviewer_b.jsonl",
    )
    with pytest.raises(RuntimeError, match="must complete every candidate"):
        build_adjudication_queue(assigned, output_path=tmp_path / "queue.jsonl")
    assert set(RETRIEVER_VARIANTS) == {
        "bm25_fts_only",
        "vector_only",
        "hybrid_rrf",
        "hybrid_rrf_reranker",
    }


def test_completed_reviews_only_generate_a_pending_adjudication_queue(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    digest = _write_bundle(dataset, tmp_path / "predictions.json")
    _complete_reviews(dataset, prediction_sha256=digest)
    write_jsonl(dataset.root / "adjudicated_labels.jsonl", dataset.adjudicated_labels)
    reviewed = HumanGoldDataset.load(dataset.root)
    output = tmp_path / "adjudication.jsonl"

    summary = build_adjudication_queue(reviewed, output_path=output)
    rows = jsonl(output)

    assert summary["automated_approval_performed"] is False
    assert summary["agreement_count"] == 45
    assert all(row["review_status"] == "pending" for row in rows)
    assert all(row["automation_generated"] is True for row in rows)


def test_prediction_provenance_contract_is_complete(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    bundle = _prediction_bundle(dataset)

    validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")
    for field in (
        "model_provider",
        "model_name",
        "prompt_version",
        "temperature",
        "git_commit_sha",
        "generated_at",
        "dataset_version",
    ):
        assert field in bundle["metadata"]


@pytest.mark.parametrize(
    ("metadata_override", "error"),
    [
        ({"mock_response_used": True}, "cannot evaluate mock responses"),
        ({"real_model_used": False}, "requires real_model_used=true"),
        (
            {"response_source": "deterministic_fixture"},
            "requires response_source=live_model",
        ),
    ],
)
def test_human_gold_rejects_mock_or_fixture_prediction_bundles(
    tmp_path: Path,
    metadata_override: dict[str, object],
    error: str,
) -> None:
    dataset = _dataset(tmp_path)
    bundle = _prediction_bundle(dataset)
    bundle["metadata"].update(metadata_override)

    with pytest.raises(RuntimeError, match=error):
        validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")


@pytest.mark.parametrize(
    ("payload_kind", "error"),
    [
        ("retrieval_missing", "requires a retrieved list"),
        ("retrieval_wrong_type", "retrieved items must be objects"),
        ("generation_missing", "requires a citation_document_ids list"),
        ("generation_wrong_type", "requires boolean refused"),
    ],
)
def test_prediction_bundle_rejects_incomplete_or_mistyped_case_payloads(
    tmp_path: Path,
    payload_kind: str,
    error: str,
) -> None:
    dataset = _dataset(tmp_path)
    bundle = _prediction_bundle(dataset)
    if payload_kind == "retrieval_missing":
        case_id = bundle["retrievers"]["bm25_fts_only"]["cases"][0]["case_id"]
        bundle["retrievers"]["bm25_fts_only"]["cases"][0] = {"case_id": case_id}
    elif payload_kind == "retrieval_wrong_type":
        bundle["retrievers"]["bm25_fts_only"]["cases"][0]["retrieved"] = ["doc"]
    elif payload_kind == "generation_missing":
        case_id = bundle["generation"][0]["case_id"]
        bundle["generation"][0] = {"case_id": case_id}
    else:
        bundle["generation"][0]["refused"] = "false"

    with pytest.raises(ValueError, match=error):
        validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")


def test_prediction_bundle_rejects_duplicate_retrieved_document_ids(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    bundle = _prediction_bundle(dataset)
    retrieved = bundle["retrievers"]["bm25_fts_only"]["cases"][0]["retrieved"]
    retrieved.append(dict(retrieved[0]))

    with pytest.raises(ValueError, match="contains duplicate document IDs"):
        validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")


def test_available_reranker_must_cover_the_same_cases(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    bundle = _prediction_bundle(dataset)
    bundle["retrievers"]["hybrid_rrf_reranker"] = {
        "available": True,
        "cases": bundle["retrievers"]["hybrid_rrf"]["cases"][:-1],
    }

    with pytest.raises(ValueError, match="must cover the same captured cases"):
        validate_prediction_bundle(bundle, dataset=dataset, mode="human_gold_eval")


def test_review_clis_cover_assignment_validation_and_pending_boundaries(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    packet_dir = tmp_path / "packets"

    assert prepare_cli(["--dataset-dir", str(dataset.root)]) == 0
    assert (
        prepare_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--reviewer-a",
                "human-a",
                "--reviewer-b",
                "human-b",
                "--output-dir",
                str(packet_dir),
            ]
        )
        == 0
    )
    assert (
        validate_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--reviewer-a",
                str(packet_dir / "labels_reviewer_a.jsonl"),
                "--reviewer-b",
                str(packet_dir / "labels_reviewer_b.jsonl"),
            ]
        )
        == 0
    )
    assert (
        validate_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--reviewer-a",
                str(packet_dir / "labels_reviewer_a.jsonl"),
                "--reviewer-b",
                str(packet_dir / "labels_reviewer_b.jsonl"),
                "--require-complete",
            ]
        )
        == 2
    )
    assert (
        prepare_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--reviewer-a",
                "same-human",
                "--reviewer-b",
                "same-human",
                "--output-dir",
                str(tmp_path / "invalid"),
            ]
        )
        == 2
    )


def test_completed_review_clis_create_queue_validate_and_run_report(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "predictions.json"
    digest = _write_bundle(dataset, prediction_path)
    _complete_reviews(dataset, prediction_sha256=digest)
    adjudication_copy = tmp_path / "completed_adjudication.jsonl"
    adjudication_copy.write_bytes((dataset.root / "adjudicated_labels.jsonl").read_bytes())
    write_jsonl(dataset.root / "adjudicated_labels.jsonl", dataset.adjudicated_labels)
    queue_path = tmp_path / "pending_queue.jsonl"

    assert (
        adjudicate_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--output",
                str(queue_path),
            ]
        )
        == 0
    )
    assert all(row["review_status"] == "pending" for row in jsonl(queue_path))
    assert (
        validate_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--adjudicated",
                str(adjudication_copy),
                "--require-human-gold",
            ]
        )
        == 0
    )
    report_path = tmp_path / "human-report.json"
    assert (
        run_human_gold_cli(
            [
                "--dataset-dir",
                str(dataset.root),
                "--predictions",
                str(prediction_path),
                "--adjudicated",
                str(adjudication_copy),
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["sample_count"] == 45


def test_live_provenance_cli_requires_and_accepts_explicit_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dataset = _dataset(tmp_path)
    prediction_path = tmp_path / "live-predictions.json"
    _write_bundle(dataset, prediction_path, mode="live_model_eval")
    bundle = json.loads(prediction_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "evaluation.human_gold_metrics.settings.QWEN_API_KEY",
        "test-fixture-credential",
    )

    report = build_live_model_provenance_report(
        dataset=dataset,
        bundle=bundle,
        prediction_path=prediction_path,
    )
    assert report["metrics_status"] == "awaiting_human_adjudication"
    assert report["human_gold_metrics"] is None
    assert report["prediction_bundle_sha256"] == file_sha256(prediction_path)
    assert report["prediction_generated_at"] == "2026-08-24T00:00:00Z"
    output = tmp_path / "live-report.json"
    assert (
        run_human_gold_cli(
            [
                "--mode",
                "live_model_eval",
                "--dataset-dir",
                str(dataset.root),
                "--predictions",
                str(prediction_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["evaluation_mode"] == "live_model_eval"
    assert saved["mock_response_used"] is False


def test_dataset_builder_cli_keeps_every_label_pending(tmp_path: Path) -> None:
    output = tmp_path / "built-v3"

    assert build_dataset_cli(["--output-dir", str(output)]) == 0
    assert all(
        row["review_status"] == "pending"
        for name in (
            "labels_reviewer_a.jsonl",
            "labels_reviewer_b.jsonl",
            "adjudicated_labels.jsonl",
        )
        for row in jsonl(output / name)
    )


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
