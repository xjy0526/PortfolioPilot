"""Deterministic offline evaluation for the hybrid knowledge retriever."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from evaluation.reporting import build_evaluation_metadata, validate_evaluation_report
from rag.models import PermissionContext
from rag.repository import KnowledgeRepository
from rag.retriever import HashingEmbedder
from rag.service import KnowledgeBaseService


REQUIRED_CITATION_FIELDS = {
    "document_id", "version", "chunk_id", "title", "source_type", "publish_date",
    "page_number", "section_title", "score", "quote", "permission_level",
}


@dataclass(frozen=True)
class RetrievalEvalCase:
    case_id: str
    query: str
    relevant_document_ids: set[str]
    permission_groups: list[str] = field(default_factory=lambda: ["public"])
    expired_document_ids: set[str] = field(default_factory=set)
    unauthorized_document_ids: set[str] = field(default_factory=set)
    expected_decision: str = "use_evidence"


def run_retrieval_evaluation(
    output_path: str | Path | None = None,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        repository = KnowledgeRepository(connection)
        service = KnowledgeBaseService(repository, HashingEmbedder())
        cases = _build_fixture(service)
        all_chunk_ids = {
            row[0] for row in connection.execute("SELECT chunk_id FROM knowledge_chunks").fetchall()
        }
        case_results = [
            _evaluate_case(service, case, top_k=top_k, all_chunk_ids=all_chunk_ids)
            for case in cases
        ]
        ranking_cases = [item for item in case_results if item["relevant_document_count"] > 0]
        report = {
            **build_evaluation_metadata(
                evaluation_mode="synthetic_smoke",
                model_provider="hashing",
                model_name="legacy-hashing-embedder-v1",
                dataset_name="offline_hybrid_retrieval_fixture",
                dataset_version="v1",
                mock_response_used=False,
                synthetic_data_used=True,
            ),
            "data_classification": "self_authored_synthetic_public_fixture",
            "retriever": "hybrid_bm25_dense_rrf_hashing",
            "top_k": top_k,
            "test_case_count": len(case_results),
            "metrics": {
                f"recall_at_{top_k}": _average(ranking_cases, "recall_at_k"),
                f"precision_at_{top_k}": _average(ranking_cases, "precision_at_k"),
                "mrr": _average(ranking_cases, "reciprocal_rank"),
                "citation_reference_validity": _ratio(
                    case_results, "valid_citation_count", "retrieved_count"
                ),
                "expired_document_hit_rate": _ratio(case_results, "expired_hit_count", "retrieved_count"),
                "unauthorized_document_hit_count": sum(item["unauthorized_hit_count"] for item in case_results),
            },
            "cases": case_results,
        }
        validate_evaluation_report(report)
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        connection.close()


def _build_fixture(service: KnowledgeBaseService) -> list[RetrievalEvalCase]:
    today = date.today()
    documents: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "eval-nvda", "nvda_report.md",
            "# Semiconductor Research\n\n## Concentration Risk\n\nNVDA semiconductor concentration and supply chain risk.",
            {"title": "NVDA Semiconductor Research", "source_type": "research_report", "tickers": ["NVDA"],
             "publish_date": "2025-03-10"},
        ),
        (
            "eval-fund", "liquidity_policy.md",
            "# Liquidity Policy\n\n## Escalation\n\nFUND001 liquidity limits require human review.",
            {"title": "Fund Liquidity Policy", "source_type": "policy", "fund_codes": ["FUND001"],
             "publish_date": "2025-06-01"},
        ),
        (
            "eval-faq", "risk_faq.md",
            "# Risk Review FAQ\n\n## Workflow\n\nRisk review questions require documented evidence.",
            {"title": "Risk Review FAQ", "source_type": "faq", "publish_date": "2025-08-01"},
        ),
        (
            "eval-expired", "old_notice.md",
            "# Old TSLA Notice\n\nThis expired TSLA announcement must never be retrieved.",
            {"title": "Expired TSLA Notice", "source_type": "announcement", "tickers": ["TSLA"],
             "publish_date": "2024-01-15", "effective_to": (today - timedelta(days=1)).isoformat()},
        ),
        (
            "eval-secret", "deal_note.md",
            "# Restricted MNA Note\n\nMNA acquisition target BLUEBIRD is confidential.",
            {"title": "Restricted MNA Note", "source_type": "research_report", "tickers": ["MNA"],
             "publish_date": "2025-09-01", "confidentiality": "restricted",
             "permission_groups": ["deal_team"]},
        ),
        (
            "eval-msft-bull", "msft_bull.md",
            "# MSFT Outlook\n\nMSFT cloud demand may accelerate.",
            {"title": "MSFT Positive Outlook", "source_type": "research_report", "tickers": ["MSFT"],
             "publish_date": "2025-10-01"},
        ),
        (
            "eval-msft-bear", "msft_bear.md",
            "# MSFT Outlook\n\nMSFT cloud demand may decelerate and evidence conflicts.",
            {"title": "MSFT Cautious Outlook", "source_type": "research_report", "tickers": ["MSFT"],
             "publish_date": "2025-10-02"},
        ),
    ]
    for document_id, filename, content, metadata in documents:
        result = service.ingest(
            content=content.encode("utf-8"),
            filename=filename,
            metadata={"document_id": document_id, **metadata},
        )
        service.repository.publish(result["document_id"])

    return [
        RetrievalEvalCase("ticker_and_time", "ticker:NVDA 2025 research report concentration", {"eval-nvda"}),
        RetrievalEvalCase("fund_and_type", "fund:FUND001 liquidity policy", {"eval-fund"}),
        RetrievalEvalCase("faq_type", "FAQ risk review workflow", {"eval-faq"}),
        RetrievalEvalCase(
            "expired_safety", "$TSLA expired announcement", set(),
            expired_document_ids={"eval-expired"},
            expected_decision="refuse_insufficient_evidence",
        ),
        RetrievalEvalCase(
            "unauthorized_safety", "$MNA acquisition target", set(),
            unauthorized_document_ids={"eval-secret"},
            expected_decision="refuse_unauthorized",
        ),
        RetrievalEvalCase(
            "authorized_restricted", "$MNA acquisition target", {"eval-secret"},
            permission_groups=["deal_team"],
        ),
        RetrievalEvalCase(
            "conflicting_evidence", "$MSFT research report cloud outlook",
            {"eval-msft-bull", "eval-msft-bear"}, expected_decision="human_review_conflict",
        ),
        RetrievalEvalCase(
            "evidence_insufficient", "unobtainium quantum dossier", set(),
            expected_decision="refuse_insufficient_evidence",
        ),
    ]


def _evaluate_case(
    service: KnowledgeBaseService,
    case: RetrievalEvalCase,
    *,
    top_k: int,
    all_chunk_ids: set[str],
) -> dict[str, Any]:
    response = service.retrieve_with_status(
        case.query,
        top_k=top_k,
        permission_context=PermissionContext(permission_groups=case.permission_groups),
        score_threshold=0.0,
    )
    citations = response["citations"]
    retrieved_ids = [str(item["document_id"]) for item in citations]
    relevant_hits = sum(1 for document_id in retrieved_ids if document_id in case.relevant_document_ids)
    first_relevant_rank = next(
        (rank for rank, document_id in enumerate(retrieved_ids, start=1) if document_id in case.relevant_document_ids),
        None,
    )
    valid_citations = sum(
        1
        for item in citations
        if REQUIRED_CITATION_FIELDS.issubset(item) and str(item.get("chunk_id")) in all_chunk_ids
    )
    return {
        "case_id": case.case_id,
        "query": case.query,
        "expected_evidence": sorted(case.relevant_document_ids),
        "expected_decision": case.expected_decision,
        "permission_groups": case.permission_groups,
        "relevant_document_count": len(case.relevant_document_ids),
        "retrieved_count": len(citations),
        "retrieved_document_ids": retrieved_ids,
        "recall_at_k": round(relevant_hits / len(case.relevant_document_ids), 6) if case.relevant_document_ids else None,
        "precision_at_k": round(relevant_hits / top_k, 6) if case.relevant_document_ids else None,
        "reciprocal_rank": round(1.0 / first_relevant_rank, 6) if first_relevant_rank else 0.0,
        "valid_citation_count": valid_citations,
        "expired_hit_count": sum(document_id in case.expired_document_ids for document_id in retrieved_ids),
        "unauthorized_hit_count": sum(document_id in case.unauthorized_document_ids for document_id in retrieved_ids),
        "evidence_insufficient": response["evidence_insufficient"],
    }


def _average(items: list[dict[str, Any]], key: str) -> float:
    values = [float(item[key]) for item in items if item.get(key) is not None]
    return round(sum(values) / len(values), 6) if values else 0.0


def _ratio(items: list[dict[str, Any]], numerator: str, denominator: str) -> float:
    total_denominator = sum(int(item[denominator]) for item in items)
    if not total_denominator:
        return 0.0
    return round(sum(int(item[numerator]) for item in items) / total_denominator, 6)
