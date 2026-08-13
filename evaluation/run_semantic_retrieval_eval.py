"""CLI: python -m evaluation.run_semantic_retrieval_eval."""
from __future__ import annotations

import argparse
import json

from config import settings
from evaluation.semantic_retrieval_eval import run_semantic_retrieval_comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare BM25, dense, and hybrid retrieval")
    parser.add_argument(
        "--output",
        default=str(settings.CACHE_DIR / "semantic_retrieval_report.json"),
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    report = run_semantic_retrieval_comparison(
        args.output,
        top_k=max(1, args.top_k),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
