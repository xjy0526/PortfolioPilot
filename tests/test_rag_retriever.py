from rag.retriever import chunk_text, retrieve_evidence


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
