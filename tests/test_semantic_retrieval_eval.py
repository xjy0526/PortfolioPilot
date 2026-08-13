from pathlib import Path

from app.providers.embeddings import HashingEmbeddingProvider
from evaluation.semantic_retrieval_eval import (
    load_semantic_gold,
    run_semantic_retrieval_comparison,
)


def test_bilingual_retrieval_gold_has_at_least_40_unique_cases():
    cases = load_semantic_gold()

    assert len(cases) >= 40
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["language"] for case in cases} >= {"zh", "en", "zh-en"}


def test_hashing_comparison_is_explicitly_not_semantic(tmp_path: Path):
    output = tmp_path / "comparison.json"
    report = run_semantic_retrieval_comparison(
        output,
        top_k=5,
        embedder=HashingEmbeddingProvider(384),
    )

    assert output.exists()
    assert report["case_count"] >= 40
    assert set(report["comparison"]) == {"bm25", "dense", "hybrid"}
    assert report["embedding"]["semantic_model"] is False
    assert report["interpretation_allowed"] is False
    assert "non-semantic" in report["warning"]
