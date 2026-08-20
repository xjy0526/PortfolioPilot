import rag.retriever as retriever
from rag.retriever import chunk_text, retrieve_evidence
from config import settings


def test_chunk_text_handles_empty_and_long_text():
    assert chunk_text("") == []
    chunks = chunk_text("A" * 1200, chunk_size=400, overlap=50)
    assert len(chunks) > 1


def test_retrieve_evidence_returns_local_docs(tmp_path):
    doc = tmp_path / "news.md"
    doc.write_text("Apple earnings growth and technology concentration risk are important.", encoding="utf-8")

    evidence = retrieve_evidence("Apple technology risk", top_k=1, document_dir=tmp_path)

    assert len(evidence) == 1
    assert evidence[0]["source"] == "news.md"


def test_retrieve_evidence_empty_dir_safe(tmp_path):
    assert retrieve_evidence("anything", document_dir=tmp_path) == []


def test_retrieve_evidence_falls_back_to_sample_docs(monkeypatch, tmp_path):
    sample_dir = tmp_path / "data" / "research_docs"
    sample_dir.mkdir(parents=True)
    (sample_dir / "sample.md").write_text("AI hardware supply chain concentration risk.", encoding="utf-8")
    retriever._INDEX_CACHE.clear()
    monkeypatch.setattr(retriever, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "RAG_DOCUMENT_DIR", "rag_documents")

    evidence = retrieve_evidence("AI hardware concentration", top_k=1)

    assert len(evidence) == 1
    assert evidence[0]["source"] == "sample.md"
