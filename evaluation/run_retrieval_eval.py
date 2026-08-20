"""CLI entrypoint: ``python -m evaluation.run_retrieval_eval``."""
from __future__ import annotations

import argparse
import json

from config import settings
from evaluation.retrieval_eval import run_retrieval_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PortfolioPilot hybrid retrieval evaluation")
    parser.add_argument("--output", default=str(settings.CACHE_DIR / "retrieval_evaluation_report.json"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    report = run_retrieval_evaluation(args.output, top_k=max(1, args.top_k))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
