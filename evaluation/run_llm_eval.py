"""CLI entrypoint for LLM financial-analysis evaluation.

Usage:
    python -m evaluation.run_llm_eval
"""
from __future__ import annotations

import argparse
import json

from config import settings
from evaluation.llm_eval import run_llm_evaluation_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PortfolioPilot LLM evaluation")
    parser.add_argument("--output", default=str(settings.CACHE_DIR / "evaluation_report.json"))
    parser.add_argument("--lang", default="zh", choices=["zh", "en"])
    parser.add_argument("--mock", action="store_true", help="Force mock LLM responses even when Qwen is configured")
    parser.add_argument("--real", action="store_true", help="Use Qwen when QWEN_API_KEY is configured")
    args = parser.parse_args()

    use_mock = True if args.mock else False if args.real else None
    report = run_llm_evaluation_sync(output_path=args.output, use_mock=use_mock, language=args.lang)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
