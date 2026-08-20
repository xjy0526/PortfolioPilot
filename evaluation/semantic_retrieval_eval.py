"""BM25, dense, and hybrid comparison on the versioned bilingual gold set."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.providers.embeddings import EmbeddingProvider, build_embedding_provider
from config import settings
from evaluation.reporting import build_evaluation_metadata, validate_evaluation_report
from rag.hybrid_retriever import BM25Retriever, reciprocal_rank_fusion

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "retrieval_gold_v1.jsonl"


def load_semantic_gold(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    required = {"id", "query", "document_id", "title", "text", "language"}
    if len(cases) < 40:
        raise ValueError("retrieval_gold_v1.jsonl must contain at least 40 cases")
    if any(not required.issubset(case) for case in cases):
        raise ValueError("retrieval gold case is missing required fields")
    if len({str(case["id"]) for case in cases}) != len(cases):
        raise ValueError("retrieval gold case IDs must be unique")
    return cases


def run_semantic_retrieval_comparison(
    output_path: str | Path | None = None,
    *,
    top_k: int = 5,
    embedder: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    cases = load_semantic_gold()
    encoder = embedder or build_embedding_provider()
    candidates = [_candidate(case) for case in cases]
    document_vectors = _normalized(encoder.encode([item["text"] for item in candidates]))
    query_vectors = _normalized(encoder.encode([str(case["query"]) for case in cases]))
    bm25 = BM25Retriever()
    case_results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        bm25_ranked = bm25.retrieve(str(case["query"]), candidates, top_k=len(candidates))
        dense_ranked = _dense_ranking(candidates, document_vectors, query_vectors[index])
        hybrid_ranked = reciprocal_rank_fusion([bm25_ranked, dense_ranked])
        expected = str(case["document_id"])
        case_results.append(
            {
                "case_id": case["id"],
                "language": case["language"],
                "expected_document_id": expected,
                "bm25_rank": _rank_of(bm25_ranked, expected),
                "dense_rank": _rank_of(dense_ranked, expected),
                "hybrid_rank": _rank_of(hybrid_ranked, expected),
            }
        )
    report = {
        **build_evaluation_metadata(
            evaluation_mode="synthetic_smoke",
            model_provider=encoder.provider_name,
            model_name=encoder.model_name,
            dataset_name="bilingual_retrieval_gold",
            dataset_version="v1",
            mock_response_used=False,
            synthetic_data_used=True,
        ),
        "data_classification": "self_authored_synthetic_public_fixture",
        "dataset": DATASET_PATH.name,
        "dataset_source": "self-authored synthetic public training fixtures",
        "case_count": len(cases),
        "top_k": max(1, top_k),
        "embedding": {
            "provider": encoder.provider_name,
            "model_name": encoder.model_name,
            "model_version": encoder.model_version,
            "dimensions": encoder.dimensions,
            "semantic_model": encoder.semantic,
        },
        "comparison": {
            method: _metrics(case_results, f"{method}_rank", top_k=max(1, top_k))
            for method in ("bm25", "dense", "hybrid")
        },
        "interpretation_allowed": bool(encoder.semantic),
        "warning": (
            "Dense and hybrid metrics use a non-semantic development hashing encoder; "
            "do not present them as semantic retrieval quality."
            if not encoder.semantic
            else ""
        ),
        "cases": case_results,
    }
    validate_evaluation_report(report)
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _candidate(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": f"chunk:{case['document_id']}",
        "document_id": str(case["document_id"]),
        "version": 1,
        "title": str(case["title"]),
        "section": str(case.get("topic") or ""),
        "text": str(case["text"]),
    }


def _normalized(values: np.ndarray) -> np.ndarray:
    vectors = np.asarray(values, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != settings.RAG_EMBEDDING_DIMENSION:
        raise ValueError(
            "Embedding comparison expected shape (*, "
            f"{settings.RAG_EMBEDDING_DIMENSION}), got {vectors.shape}"
        )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms > 0, norms, 1.0)


def _dense_ranking(
    candidates: list[dict[str, Any]],
    document_vectors: np.ndarray,
    query_vector: np.ndarray,
) -> list[dict[str, Any]]:
    scores = document_vectors @ query_vector
    ranked = []
    for candidate, score in zip(candidates, scores, strict=True):
        item = dict(candidate)
        item["dense_score"] = float(score)
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["dense_score"], item["chunk_id"]))
    return ranked


def _rank_of(ranked: list[dict[str, Any]], expected_document_id: str) -> int | None:
    return next(
        (
            rank
            for rank, item in enumerate(ranked, start=1)
            if str(item["document_id"]) == expected_document_id
        ),
        None,
    )


def _metrics(
    cases: list[dict[str, Any]], rank_key: str, *, top_k: int
) -> dict[str, float]:
    ranks = [item.get(rank_key) for item in cases]
    return {
        f"recall_at_{top_k}": round(
            sum(rank is not None and rank <= top_k for rank in ranks) / len(ranks), 6
        ),
        "mrr": round(
            sum((1.0 / rank) if rank else 0.0 for rank in ranks) / len(ranks), 6
        ),
    }
