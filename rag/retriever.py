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
from typing import Protocol

import numpy as np

from config import BASE_DIR, settings

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts into a 2D float array."""


@dataclass
class DocumentChunk:
    id: str
    text: str
    source: str
    path: str
    chunk_index: int


class HashingEmbedder:
    """Small deterministic embedding fallback with no external dependency."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vectors[row, bucket] += sign
            norm = np.linalg.norm(vectors[row])
            if norm > 0:
                vectors[row] /= norm
        return vectors


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(texts, normalize_embeddings=True),
            dtype=np.float32,
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
            pairs = zip(indices[0].tolist(), scores[0].tolist(), strict=False)
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


def retrieve_evidence(query: str, top_k: int = 5, document_dir: str | Path | None = None) -> list[dict]:
    """Retrieve local evidence chunks for a query.

    The function is intentionally safe in empty or partially configured
    environments: no documents or missing vector libraries simply return [].
    """
    root = _resolve_document_dir(document_dir)
    if not root.exists() or not root.is_dir():
        return []

    try:
        index = _get_or_build_index(root)
        return index.search(query, top_k=top_k)
    except Exception as exc:
        logger.warning("RAG retrieval failed: %s", exc)
        return []


def load_documents(document_dir: str | Path | None = None) -> list[dict[str, str]]:
    """Load txt, md and csv documents from a local directory."""
    root = _resolve_document_dir(document_dir)
    if not root.exists() or not root.is_dir():
        return []

    docs: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".csv"}:
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
    model_name = getattr(settings, "RAG_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    try:
        return SentenceTransformerEmbedder(model_name)
    except Exception as exc:
        logger.debug("sentence-transformers unavailable, using hashing embedder: %s", exc)
        return HashingEmbedder()


def _read_document(path: Path) -> str:
    try:
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
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".csv"}:
            stat = path.stat()
            items.append((str(path), math.floor(stat.st_mtime), stat.st_size))
    return tuple(items)


def _resolve_document_dir(document_dir: str | Path | None) -> Path:
    value = document_dir or getattr(settings, "RAG_DOCUMENT_DIR", "rag_documents")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path
