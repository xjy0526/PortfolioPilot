"""Local document retrieval for financial evidence injection.

The retriever supports txt, md and csv files. It tries sentence-transformers
plus FAISS when available, and falls back to a deterministic hashing embedding
with NumPy cosine similarity when optional vector dependencies are missing.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Protocol

from rag.models import PermissionContext

import numpy as np

from config import BASE_DIR, settings
from app.providers.embeddings import (
    HashingEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
    build_embedding_provider,
)

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts into a 2D float array."""


@dataclass
class DocumentChunk:
    id: str
    text: str
    source: str
    path: str
    chunk_index: int


HashingEmbedder = HashingEmbeddingProvider


class SentenceTransformerEmbedder(SentenceTransformerEmbeddingProvider):
    """Compatibility wrapper that applies the configured vector dimension."""

    def __init__(self, model_name: str):
        super().__init__(
            model_name,
            expected_dimensions=settings.RAG_EMBEDDING_DIMENSION,
            local_files_only=settings.ENVIRONMENT == "production",
        )


class LocalVectorIndex:
    def __init__(self, chunks: list[DocumentChunk], embedder: Embedder):
        self.chunks = chunks
        self.embedder = embedder
        self.embeddings = embedder.encode([chunk.text for chunk in chunks]) if chunks else np.empty((0, 0))
        self.faiss_index = None
        if chunks:
            try:
                import faiss

                self.faiss_index = faiss.IndexFlatIP(self.embeddings.shape[1])
                self.faiss_index.add(self.embeddings.astype(np.float32))
            except Exception as exc:
                logger.debug("FAISS unavailable, using NumPy retrieval: %s", exc)

    def search(self, query: str, top_k: int) -> list[dict]:
        if not self.chunks or not query.strip():
            return []
        query_vec = self.embedder.encode([query]).astype(np.float32)
        k = max(1, min(top_k, len(self.chunks)))

        if self.faiss_index is not None:
            scores, indices = self.faiss_index.search(query_vec, k)
            pairs: Iterable[tuple[int, float]] = zip(
                indices[0].tolist(), scores[0].tolist(), strict=False
            )
        else:
            sims = self.embeddings @ query_vec[0]
            top_indices = np.argsort(sims)[::-1][:k]
            pairs = [(int(idx), float(sims[idx])) for idx in top_indices]

        results = []
        for idx, score in pairs:
            if idx < 0 or idx >= len(self.chunks):
                continue
            chunk = self.chunks[idx]
            results.append({
                "id": chunk.id,
                "source": chunk.source,
                "path": chunk.path,
                "chunk_index": chunk.chunk_index,
                "score": round(float(score), 4),
                "text": chunk.text,
            })
        return results


_INDEX_CACHE: dict[str, tuple[tuple[tuple[str, float, int], ...], LocalVectorIndex]] = {}


def retrieve_evidence(
    query: str,
    top_k: int = 5,
    document_dir: str | Path | None = None,
    permission_context: PermissionContext | None = None,
) -> list[dict]:
    """Retrieve authorized, published and currently effective evidence.

    An explicitly supplied ``document_dir`` keeps the legacy public-directory
    behavior for local tests/tools. Normal application retrieval uses
    ``PostgresKnowledgeService``; this function is an explicit offline fallback.
    """
    return retrieve_evidence_with_status(
        query,
        top_k=top_k,
        document_dir=document_dir,
        permission_context=permission_context,
    )["citations"]


def retrieve_evidence_with_status(
    query: str,
    top_k: int = 5,
    document_dir: str | Path | None = None,
    permission_context: PermissionContext | None = None,
    score_threshold: float | None = None,
) -> dict:
    """Retrieve evidence plus normalized intent and insufficiency status."""
    if document_dir is not None:
        root = _resolve_usable_document_dir(document_dir)
        if not root.exists() or not root.is_dir():
            return {"citations": [], "evidence_insufficient": True}
        try:
            index = _get_or_build_index(root)
            citations = index.search(query, top_k=top_k)
            return {"citations": citations, "evidence_insufficient": not citations}
        except Exception as exc:
            logger.warning("Legacy RAG retrieval failed: %s", exc)
            return {"citations": [], "evidence_insufficient": True}

    # Compatibility-only offline path for local tools and bundled samples.
    # FastAPI routes and workflow services use PostgresKnowledgeService directly.
    root = _resolve_usable_document_dir(None)
    if not root.exists() or not root.is_dir():
        return {
            "citations": [],
            "evidence_insufficient": True,
            "retrieval_backend": "legacy_local_fallback",
        }
    try:
        citations = _get_or_build_index(root).search(query, top_k=top_k)
        return {
            "citations": citations,
            "evidence_insufficient": not citations,
            "retrieval_backend": "legacy_local_fallback",
        }
    except Exception as exc:
        logger.warning("Legacy local RAG fallback failed: %s", exc)
        return {
            "citations": [],
            "evidence_insufficient": True,
            "retrieval_backend": "legacy_local_fallback",
            "error": str(exc),
        }


def load_documents(document_dir: str | Path | None = None) -> list[dict[str, str]]:
    """Load txt, md and csv documents from a local directory."""
    root = _resolve_usable_document_dir(document_dir)
    if not root.exists() or not root.is_dir():
        return []

    docs: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".csv", ".pdf"}:
            continue
        text = _read_document(path)
        if text.strip():
            docs.append({"path": str(path), "source": path.name, "text": text})
    return docs


def chunk_text(text: str, chunk_size: int | None = None, overlap: int = 120) -> list[str]:
    """Split text into overlapping chunks."""
    chunk_size = chunk_size or int(getattr(settings, "RAG_CHUNK_SIZE", 900))
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    if len(clean) <= chunk_size:
        return [clean]

    chunks = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(clean), step):
        chunk = clean[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(clean):
            break
    return chunks


def _get_or_build_index(root: Path) -> LocalVectorIndex:
    signature = _directory_signature(root)
    cache_key = str(root)
    cached = _INDEX_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]

    chunks = _load_chunks(root)
    embedder = _build_embedder()
    index = LocalVectorIndex(chunks, embedder)
    _INDEX_CACHE[cache_key] = (signature, index)
    return index


def _load_chunks(root: Path) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    for doc in load_documents(root):
        for idx, text in enumerate(chunk_text(doc["text"])):
            chunk_id = hashlib.sha1(f"{doc['path']}:{idx}:{text[:80]}".encode("utf-8")).hexdigest()[:16]
            chunks.append(
                DocumentChunk(
                    id=chunk_id,
                    text=text,
                    source=doc["source"],
                    path=doc["path"],
                    chunk_index=idx,
                )
            )
    return chunks


def _build_embedder() -> Embedder:
    return build_embedding_provider(settings)


def _read_document(path: Path) -> str:
    try:
        if path.suffix.lower() == ".pdf":
            from rag.parsers import parse_document

            _, blocks, _ = parse_document(path.read_bytes(), path.name, path.stem)
            return "\n\n".join(block.text for block in blocks)
        if path.suffix.lower() == ".csv":
            return _read_csv_document(path)
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.warning("Could not read RAG document %s: %s", path, exc)
        return ""


def _read_csv_document(path: Path) -> str:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            for i, row in enumerate(reader):
                if i >= 500:
                    break
                rows.append(" | ".join(f"{key}: {value}" for key, value in row.items() if value))
        else:
            f.seek(0)
            plain_reader = csv.reader(f)
            rows = [", ".join(row) for _, row in zip(range(500), plain_reader, strict=False)]
    return "\n".join(rows)


def _directory_signature(root: Path) -> tuple[tuple[str, float, int], ...]:
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".csv", ".pdf"}:
            stat = path.stat()
            items.append((str(path), math.floor(stat.st_mtime), stat.st_size))
    return tuple(items)


def _resolve_document_dir(document_dir: str | Path | None) -> Path:
    value: str | Path = document_dir or str(
        getattr(settings, "RAG_DOCUMENT_DIR", "rag_documents")
    )
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _resolve_usable_document_dir(document_dir: str | Path | None) -> Path:
    root = _resolve_document_dir(document_dir)
    configured = str(getattr(settings, "RAG_DOCUMENT_DIR", "rag_documents") or "").strip()
    uses_default_dir = document_dir is None and configured in {"", "rag_documents"}
    if uses_default_dir and not _has_supported_documents(root):
        demo_docs = BASE_DIR / "data" / "research_docs"
        if _has_supported_documents(demo_docs):
            return demo_docs
    return root


def _has_supported_documents(root: Path) -> bool:
    if not root.exists() or not root.is_dir():
        return False
    return any(
        path.is_file() and path.suffix.lower() in {".txt", ".md", ".csv", ".pdf"}
        for path in root.rglob("*")
    )
