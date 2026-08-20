import sqlite3
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag.models import PermissionContext
from rag.parsers import parse_document, structured_chunks
from rag.repository import KnowledgeRepository, ensure_knowledge_schema
from rag.retriever import HashingEmbedder
from rag.service import IngestionError, KnowledgeBaseService
from routes import knowledge as knowledge_routes


class RecordingEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.delegate = HashingEmbedder()

    def encode(self, texts: list[str]):
        self.calls.append(list(texts))
        return self.delegate.encode(texts)


@pytest.fixture
def repository():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    repo = KnowledgeRepository(connection)
    yield repo
    connection.close()


@pytest.fixture
def service(repository):
    return KnowledgeBaseService(repository, HashingEmbedder())


def _ingest(service, content, *, document_id="doc-1", **metadata):
    return service.ingest(
        content=content.encode("utf-8"),
        filename="research.md",
        metadata={"document_id": document_id, "title": "Research", **metadata},
    )


def test_schema_migration_is_idempotent_and_preserves_old_tables():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE legacy_rag_cache (id INTEGER PRIMARY KEY)")

    ensure_knowledge_schema(connection)
    ensure_knowledge_schema(connection)

    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "legacy_rag_cache",
        "knowledge_documents",
        "knowledge_document_versions",
        "knowledge_chunks",
        "ingestion_jobs",
    }.issubset(tables)


def test_content_change_creates_version_and_publish_controls_visible_version(service):
    first = _ingest(service, "# Outlook\n\nVersion one growth outlook.")
    service.repository.publish(first["document_id"])
    second = _ingest(service, "# Outlook\n\nVersion two recession outlook.")

    assert first["version"] == 1
    assert second["version"] == 2
    assert len(service.repository.list_versions("doc-1")) == 2
    before_publish = service.retrieve("growth outlook", top_k=5)
    assert before_publish and all(item["version"] == 1 for item in before_publish)

    service.repository.publish("doc-1")
    after_publish = service.retrieve("recession outlook", top_k=5)
    assert after_publish and all(item["version"] == 2 for item in after_publish)


def test_unchanged_checksum_is_not_reindexed(service):
    first = _ingest(service, "# Policy\n\nThe same policy content.")
    duplicate = _ingest(service, "# Policy\n\nThe same policy content.")

    assert duplicate["status"] == "duplicate"
    assert duplicate["version_id"] == first["version_id"]
    assert duplicate["chunks_created"] == 0
    assert len(service.repository.list_versions("doc-1")) == 1
    assert service.repository.get_job(duplicate["job_id"]).status == "duplicate"


def test_expired_and_deactivated_documents_are_not_retrieved(service):
    expired = _ingest(
        service,
        "# Notice\n\nExpired investment notice.",
        effective_to=date.today() - timedelta(days=1),
    )
    service.repository.publish(expired["document_id"])
    assert service.retrieve("investment notice") == []

    active = _ingest(
        service,
        "# Notice\n\nCurrent investment notice.",
        document_id="doc-active",
    )
    service.repository.publish(active["document_id"])
    assert service.retrieve("current investment")
    service.repository.deactivate(active["document_id"])
    assert service.retrieve("current investment") == []


def test_permission_filter_happens_before_sensitive_text_is_embedded(repository):
    recorder = RecordingEmbedder()
    service = KnowledgeBaseService(repository, recorder)
    public = _ingest(service, "# FAQ\n\nPublic allocation guidance.", document_id="public")
    restricted = _ingest(
        service,
        "# Deal\n\nEMBARGOED ACQUISITION TARGET BLUEBIRD.",
        document_id="restricted",
        confidentiality="restricted",
        permission_groups=["deal_team"],
    )
    repository.publish(public["document_id"])
    repository.publish(restricted["document_id"])

    public_results = service.retrieve(
        "allocation guidance",
        permission_context=PermissionContext(permission_groups=["public"]),
    )

    assert public_results
    assert not any("BLUEBIRD" in text for call in recorder.calls for text in call)
    recorder.calls.clear()
    restricted_results = service.retrieve(
        "BLUEBIRD",
        permission_context=PermissionContext(permission_groups=["deal_team"]),
    )
    assert any(item["document_id"] == "restricted" for item in restricted_results)
    assert any("BLUEBIRD" in text for text in recorder.calls[0])


def test_non_public_document_requires_explicit_acl(service):
    with pytest.raises(IngestionError) as error:
        _ingest(service, "Internal policy", confidentiality="internal")

    job = service.repository.get_job(error.value.job_id)
    assert job.status == "failed"
    assert "permission_groups" in job.error_message


def test_parsing_failure_is_recorded_on_job(service):
    with pytest.raises(IngestionError) as error:
        service.ingest(content=b"not supported", filename="malware.exe", metadata={"title": "Bad"})

    job = service.repository.get_job(error.value.job_id)
    assert job.status == "failed"
    assert "Unsupported document type" in job.error_message


def test_markdown_and_pdf_preserve_structure_and_page(monkeypatch):
    title, blocks, parser = parse_document(
        b"# Fund Notice\n\n## Risks\n\nLiquidity can decline.", "notice.md", "Notice"
    )
    chunks = structured_chunks(blocks)
    assert parser == "markdown"
    assert title == "Fund Notice"
    assert chunks[0]["section"] == "Risks"

    class FakeReader:
        metadata = SimpleNamespace(title="Public Filing")
        pages = [SimpleNamespace(extract_text=lambda: "RISK FACTORS:\n\nMarket risk increased.")]

        def __init__(self, _stream):
            pass

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))
    pdf_title, pdf_blocks, pdf_parser = parse_document(b"%PDF-fake", "filing.pdf", "Filing")
    assert pdf_parser == "pypdf"
    assert pdf_title == "Public Filing"
    assert pdf_blocks[0].page_number == 1
    assert pdf_blocks[0].section == "RISK FACTORS"


def test_pdf_page_limit_is_enforced_before_parsing(monkeypatch):
    class FakeReader:
        metadata = SimpleNamespace(title="Long Filing")
        pages = [SimpleNamespace(extract_text=lambda: "Page") for _ in range(2)]

        def __init__(self, _stream):
            pass

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))

    with pytest.raises(ValueError, match="PDF page limit exceeded: 2 > 1"):
        parse_document(
            b"%PDF-fake",
            "long_filing.pdf",
            "Long Filing",
            max_pdf_pages=1,
        )


def test_knowledge_upload_route_uses_multipart_uploadfile():
    route = next(
        item
        for item in knowledge_routes.router.routes
        if getattr(item, "path", "") == "/api/knowledge/documents"
        and "POST" in getattr(item, "methods", set())
    )
    body_fields = {field.name: field for field in route.dependant.body_params}
    assert {"file", "metadata"}.issubset(body_fields)
    assert body_fields["file"].field_info.media_type == "multipart/form-data"
