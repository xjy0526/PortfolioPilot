"""Unit contracts for governed RAG and server-owned identity."""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from starlette.requests import Request

from app.core.principal import Principal, get_principal
from app.core.resources import embedding_model_name
from app.services.research_knowledge import KnowledgeIngestionError, PostgresKnowledgeService
from config import settings
from rag.retriever import HashingEmbedder
from routes.knowledge import _document_payload


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        result = np.zeros((len(texts), settings.RAG_EMBEDDING_DIMENSION), dtype=np.float32)
        if texts:
            result[:, 0] = 1.0
        return result


class RetrievalRepositoryStub:
    async def full_text_search(self, query: str, **_kwargs):
        return [_candidate("fts", query)]

    async def vector_search(self, vector: list[float], **_kwargs):
        assert len(vector) == settings.RAG_EMBEDDING_DIMENSION
        return [_candidate("vector", "persisted chunk text")]


class LowSimilarityRepositoryStub:
    async def full_text_search(self, query: str, **_kwargs):
        return []

    async def vector_search(self, vector: list[float], **_kwargs):
        candidate = _candidate("vector", "unrelated persisted chunk")
        candidate["score"] = 0.01
        candidate["vector_score"] = 0.01
        return [candidate]


class ExistingJobRepositoryStub:
    def __init__(self, checksum: str) -> None:
        self.job = SimpleNamespace(checksum=checksum)

    async def find_job(self, _user_id: str, _key: str):
        return self.job


class WrongDimensionEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 3), dtype=np.float32)


def _candidate(channel: str, text: str) -> dict:
    return {
        "chunk_id": "00000000-0000-0000-0000-000000000002",
        "document_id": "00000000-0000-0000-0000-000000000001",
        "document_key": "public-doc",
        "version_id": "00000000-0000-0000-0000-000000000003",
        "version": 1,
        "source_filename": "public.md",
        "source_type": "research_report",
        "title": "Public Research",
        "section": "Risk",
        "page_number": 1,
        "text": text,
        "publish_date": date.today().isoformat(),
        "confidentiality": "public",
        "permission_groups": ["public"],
        "score": 0.9,
        f"{channel}_score": 0.9,
    }


@pytest.mark.asyncio
async def test_retrieval_encodes_query_only_and_uses_persisted_document_vectors():
    embedder = RecordingEmbedder()
    service = PostgresKnowledgeService(object(), embedder=embedder)
    service.repository = RetrievalRepositoryStub()

    result = await service.retrieve_with_status(
        "AAPL concentration risk",
        principal=Principal("analyst", frozenset({"public"})),
        score_threshold=0.0,
    )

    assert embedder.calls == [["AAPL concentration risk"]]
    assert result["retrieval_backend"] == "postgresql_fts+pgvector_rrf"
    assert result["citations"][0]["document_key"] == "public-doc"


@pytest.mark.asyncio
async def test_retrieval_removes_metadata_control_prefix_from_search_text():
    embedder = RecordingEmbedder()
    service = PostgresKnowledgeService(object(), embedder=embedder)
    service.repository = RetrievalRepositoryStub()

    await service.retrieve_with_status(
        "ticker:AAPL concentration risk",
        principal=Principal("analyst", frozenset({"public"})),
        score_threshold=0.0,
    )

    assert embedder.calls == [["AAPL concentration risk"]]


@pytest.mark.asyncio
async def test_retrieval_filters_low_similarity_vector_candidates(monkeypatch):
    monkeypatch.setattr(settings, "RAG_VECTOR_SCORE_THRESHOLD", 0.2)
    service = PostgresKnowledgeService(object(), embedder=RecordingEmbedder())
    service.repository = LowSimilarityRepositoryStub()

    result = await service.retrieve_with_status(
        "unmatched evidence query",
        principal=Principal("analyst", frozenset({"public"})),
        score_threshold=0.0,
    )

    assert result["citations"] == []
    assert result["evidence_insufficient"] is True


@pytest.mark.asyncio
async def test_retrieval_rejects_query_embedding_dimension_mismatch():
    service = PostgresKnowledgeService(object(), embedder=WrongDimensionEmbedder())
    service.repository = RetrievalRepositoryStub()

    with pytest.raises(KnowledgeIngestionError, match="Query embedding dimension mismatch"):
        await service.retrieve_with_status(
            "AAPL risk",
            principal=Principal("analyst", frozenset({"public"})),
        )


@pytest.mark.asyncio
async def test_upload_size_limit_is_enforced_before_database_access(monkeypatch):
    monkeypatch.setattr(settings, "RAG_MAX_UPLOAD_BYTES", 4)
    service = PostgresKnowledgeService(object(), embedder=RecordingEmbedder())

    with pytest.raises(KnowledgeIngestionError, match="File size limit exceeded"):
        await service.queue_upload(
            content=b"12345",
            filename="risk.md",
            metadata={},
            principal=Principal("admin", frozenset({"knowledge_admin"})),
            idempotency_key="size-test",
        )


@pytest.mark.asyncio
async def test_upload_idempotency_key_rejects_different_content():
    import hashlib

    original = b"first public filing"
    service = PostgresKnowledgeService(object(), embedder=RecordingEmbedder())
    service.repository = ExistingJobRepositoryStub(hashlib.sha256(original).hexdigest())

    with pytest.raises(KnowledgeIngestionError, match="different document content"):
        await service.queue_upload(
            content=b"different public filing",
            filename="filing.md",
            metadata={},
            principal=Principal("admin", frozenset({"knowledge_admin"})),
            idempotency_key="same-key",
        )


@pytest.mark.asyncio
async def test_restricted_upload_requires_explicit_server_acl_before_database_access():
    service = PostgresKnowledgeService(object(), embedder=RecordingEmbedder())

    with pytest.raises(KnowledgeIngestionError, match="explicit non-public permission_groups"):
        await service.queue_upload(
            content=b"restricted research",
            filename="restricted.md",
            metadata={"confidentiality": "restricted"},
            principal=Principal("admin", frozenset({"knowledge_admin"})),
            idempotency_key="restricted-key",
        )


def test_principal_permissions_are_server_owned_and_case_normalized():
    principal = Principal("server-user", frozenset({"public", "research_reviewer"}))
    assert principal.user_id == "server-user"
    assert principal.has_group("research_reviewer")
    principal.require_any_group("research_reviewer", "knowledge_admin")
    assert not principal.has_group("client-claimed-admin")


@pytest.mark.asyncio
async def test_unauthenticated_production_principal_has_public_access_only(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})

    principal = await get_principal(request)

    assert principal.user_id == "anonymous"
    assert principal.permission_groups == frozenset({"public"})


def test_hashing_fallback_has_an_explicit_model_identity():
    assert embedding_model_name(HashingEmbedder(384)) == "hashing-blake2b-384"


def test_public_document_payload_hides_unpublished_current_version():
    now = datetime.now(UTC)
    document = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        document_key="public-document",
        title="Published title",
        source_type="research_report",
        department="Research",
        author="Analyst",
        status="published",
        current_version=2,
        published_version=1,
        created_at=now,
        updated_at=now,
    )

    assert _document_payload(document, include_drafts=False)["current_version"] == 1
    assert _document_payload(document, include_drafts=True)["current_version"] == 2
