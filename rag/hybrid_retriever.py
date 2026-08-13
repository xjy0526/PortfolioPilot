"""Dependency-light BM25, dense and reciprocal-rank-fusion retrieval."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Protocol

import numpy as np


class Reranker(Protocol):
    """Optional reranker contract; implementations may call a local or hosted model."""

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return candidates in preferred order, optionally replacing ``score``."""


class BM25Retriever:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def retrieve(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not candidates or not query.strip():
            return []
        documents = [_tokenize(_candidate_text(item)) for item in candidates]
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        average_length = sum(len(tokens) for tokens in documents) / max(1, len(documents))
        document_frequency: Counter[str] = Counter()
        for tokens in documents:
            document_frequency.update(set(tokens))

        scored: list[dict[str, Any]] = []
        corpus_size = len(documents)
        for candidate, tokens in zip(candidates, documents, strict=False):
            frequencies = Counter(tokens)
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                frequency_docs = document_frequency[term]
                inverse_frequency = math.log(1.0 + (corpus_size - frequency_docs + 0.5) / (frequency_docs + 0.5))
                length_ratio = len(tokens) / average_length if average_length else 0.0
                denominator = frequency + self.k1 * (1.0 - self.b + self.b * length_ratio)
                score += inverse_frequency * (frequency * (self.k1 + 1.0) / denominator)
            item = dict(candidate)
            item["bm25_score"] = float(score)
            if score > 0:
                scored.append(item)
        scored.sort(key=lambda item: (-item["bm25_score"], item["chunk_id"]))
        return scored[:max(1, min(top_k, len(scored)))]


class DenseRetriever:
    def __init__(self, embedder: Any):
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not candidates or not query.strip():
            return []
        texts = [_candidate_text(item) for item in candidates]
        embeddings = np.asarray(self.embedder.encode(texts), dtype=np.float32)
        query_vector = np.asarray(self.embedder.encode([query]), dtype=np.float32)[0]
        similarities = embeddings @ query_vector
        scored = []
        for candidate, similarity in zip(candidates, similarities, strict=False):
            item = dict(candidate)
            item["dense_score"] = float(similarity)
            if similarity > 0:
                scored.append(item)
        scored.sort(key=lambda item: (-item["dense_score"], item["chunk_id"]))
        return scored[:max(1, min(top_k, len(scored)))]


class HybridRetriever:
    def __init__(
        self,
        bm25: BM25Retriever,
        dense: DenseRetriever,
        *,
        rrf_k: int = 60,
        reranker: Reranker | None = None,
    ):
        self.bm25 = bm25
        self.dense = dense
        self.rrf_k = max(1, int(rrf_k))
        self.reranker = reranker

    def retrieve(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int,
        pool_size: int | None = None,
        score_threshold: float = 0.0,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        pool = max(top_k, int(pool_size or top_k * 4))
        bm25_results = self.bm25.retrieve(query, candidates, top_k=pool)
        dense_results = self.dense.retrieve(query, candidates, top_k=pool)
        fused = reciprocal_rank_fusion([bm25_results, dense_results], rrf_k=self.rrf_k)
        if self.reranker:
            fused = self.reranker.rerank(query, fused)
        deduplicated = deduplicate_candidates(fused)
        accepted = [item for item in deduplicated if float(item.get("score", 0.0)) >= score_threshold]
        return accepted[:max(1, top_k)]


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """Fuse rankings and normalize by the theoretical best RRF score."""
    if not ranked_lists:
        return []
    by_chunk: dict[str, dict[str, Any]] = {}
    rank_scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, candidate in enumerate(ranked, start=1):
            chunk_id = str(candidate["chunk_id"])
            by_chunk.setdefault(chunk_id, dict(candidate))
            rank_scores[chunk_id] = rank_scores.get(chunk_id, 0.0) + 1.0 / (
                max(1, rrf_k) + rank
            )
            for key in ("bm25_score", "dense_score", "fts_score", "vector_score"):
                if key in candidate:
                    by_chunk[chunk_id][key] = candidate[key]
    maximum = len(ranked_lists) / (max(1, rrf_k) + 1)
    fused = []
    for chunk_id, candidate in by_chunk.items():
        item = dict(candidate)
        item["rrf_score"] = float(rank_scores[chunk_id])
        item["score"] = float(rank_scores[chunk_id] / maximum) if maximum else 0.0
        fused.append(item)
    fused.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    return fused


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_chunks: set[str] = set()
    seen_quotes: set[tuple[str, int, str]] = set()
    results = []
    for candidate in candidates:
        chunk_id = str(candidate.get("chunk_id", ""))
        quote_key = (
            str(candidate.get("document_id", "")),
            int(candidate.get("version", 0) or 0),
            re.sub(r"\s+", " ", str(candidate.get("text", ""))).strip().lower(),
        )
        if not chunk_id or chunk_id in seen_chunks or quote_key in seen_quotes:
            continue
        seen_chunks.add(chunk_id)
        seen_quotes.add(quote_key)
        results.append(candidate)
    return results


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (
            str(candidate.get("title", "")),
            str(candidate.get("section", "")),
            str(candidate.get("text", "")),
        )
        if value
    )


def _tokenize(text: str) -> list[str]:
    normalized = str(text or "").lower()
    words = re.findall(r"[a-z0-9][a-z0-9._-]*", normalized)
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese_tokens: list[str] = []
    for sequence in chinese_sequences:
        chinese_tokens.extend(sequence)
        chinese_tokens.extend(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return words + chinese_tokens
