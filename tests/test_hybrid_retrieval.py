import sqlite3
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from evaluation.retrieval_eval import run_retrieval_evaluation
from rag.hybrid_retriever import BM25Retriever, DenseRetriever, HybridRetriever
from rag.models import PermissionContext
from rag.query import extract_query_intent, normalize_query
from rag.repository import KnowledgeRepository
from rag.retriever import HashingEmbedder
from rag.service import KnowledgeBaseService
from routes import research
from services.financial_analysis import enforce_retrieved_citations


def test_query_normalization_and_intent_extraction():
    query = "  ticker：NVDA   fund:FUND001  研报  2024-01-01 至 2024-12-31  "
    intent = extract_query_intent(query)

    assert normalize_query(query).startswith("ticker:NVDA")
    assert intent.tickers == ["NVDA"]
    assert intent.fund_codes == ["FUND001"]
    assert intent.source_types == ["research_report"]
    assert intent.publish_date_from == date(2024, 1, 1)
    assert intent.publish_date_to == date(2024, 12, 31)


def test_bm25_dense_and_rrf_hybrid_rank_matching_chunk_first():
    candidates = [
        {"chunk_id": "one", "document_id": "d1", "version": 1, "title": "Semiconductor", "section": "Risk", "text": "NVDA supply chain concentration"},
        {"chunk_id": "two", "document_id": "d2", "version": 1, "title": "FAQ", "section": "Fees", "text": "fund fee questions"},
    ]
    bm25 = BM25Retriever()
    dense = DenseRetriever(HashingEmbedder())
    hybrid = HybridRetriever(bm25, dense)

    assert bm25.retrieve("NVDA concentration", candidates, top_k=2)[0]["chunk_id"] == "one"
    assert dense.retrieve("NVDA concentration", candidates, top_k=2)[0]["chunk_id"] == "one"
    result = hybrid.retrieve("NVDA concentration", candidates, top_k=2, score_threshold=0.1)
    assert result[0]["chunk_id"] == "one"
    assert 0 < result[0]["score"] <= 1


def test_optional_reranker_is_pluggable():
    class ReverseReranker:
        called = False

        def rerank(self, query, candidates):
            self.called = True
            return list(reversed(candidates))

    reranker = ReverseReranker()
    candidates = [
        {"chunk_id": "one", "document_id": "d1", "version": 1, "title": "Alpha", "section": "", "text": "alpha risk"},
        {"chunk_id": "two", "document_id": "d2", "version": 1, "title": "Alpha", "section": "", "text": "alpha secondary"},
    ]
    hybrid = HybridRetriever(BM25Retriever(), DenseRetriever(HashingEmbedder()), reranker=reranker)
    result = hybrid.retrieve("alpha", candidates, top_k=2, score_threshold=0.0)
    assert reranker.called is True
    assert result


def test_citation_contract_threshold_and_metadata_filter():
    connection = sqlite3.connect(":memory:")
    repository = KnowledgeRepository(connection)
    service = KnowledgeBaseService(repository, HashingEmbedder())
    result = service.ingest(
        content=b"# Semiconductor Report\n\n## Risk\n\nNVDA concentration risk.",
        filename="nvda.md",
        metadata={
            "document_id": "nvda-doc",
            "title": "NVDA Report",
            "source_type": "research_report",
            "tickers": ["NVDA"],
            "publish_date": "2025-02-01",
        },
    )
    repository.publish(result["document_id"])

    response = service.retrieve_with_status(
        "ticker:NVDA 2025 research report concentration",
        permission_context=PermissionContext(permission_groups=["public"]),
        score_threshold=0.1,
    )
    citation = response["citations"][0]
    assert {
        "document_id", "version", "chunk_id", "title", "source_type", "publish_date",
        "page_number", "section_title", "score", "quote", "permission_level",
    }.issubset(citation)
    assert citation["document_id"] == "nvda-doc"
    assert response["intent"]["tickers"] == ["NVDA"]
    assert response["evidence_insufficient"] is False

    insufficient = service.retrieve_with_status("NVDA concentration", score_threshold=1.1)
    assert insufficient["citations"] == []
    assert insufficient["evidence_insufficient"] is True
    connection.close()


def test_unretrieved_citation_references_are_removed():
    evidence = [{"document_id": "doc-1", "chunk_id": "chunk-1", "citation_key": "doc-1:chunk-1"}]
    result = enforce_retrieved_citations(
        {"evidence_used": ["doc-1:chunk-1", "doc-2:invented"]},
        evidence,
    )
    assert result["evidence_used"] == [{"document_id": "doc-1", "chunk_id": "chunk-1"}]


def test_rag_api_exposes_citations_and_insufficient_flag(monkeypatch):
    app = FastAPI()
    app.include_router(research.router)
    monkeypatch.setattr(
        research,
        "retrieve_evidence_with_status",
        lambda **_kwargs: {
            "normalized_query": "unknown topic",
            "intent": {"tickers": []},
            "citations": [],
            "evidence_insufficient": True,
            "score_threshold": 0.5,
        },
    )

    payload = TestClient(app).post(
        "/api/rag/retrieve",
        json={"query": "unknown topic", "score_threshold": 0.5},
    ).json()
    assert payload["citations"] == []
    assert payload["evidence"] == []
    assert payload["evidence_insufficient"] is True


def test_retrieval_evaluation_reports_quality_and_safety_metrics(tmp_path):
    output = tmp_path / "retrieval_report.json"
    report = run_retrieval_evaluation(output, top_k=5)

    assert output.exists()
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["metrics"]["citation_reference_validity"] == 1.0
    assert report["metrics"]["expired_document_hit_rate"] == 0.0
    assert report["metrics"]["unauthorized_document_hit_count"] == 0
