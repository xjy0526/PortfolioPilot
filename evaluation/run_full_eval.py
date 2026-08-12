"""Run all three evaluation layers and write the consolidated report."""
from __future__ import annotations

import argparse
import json

from config import settings
from evaluation.full_eval import run_full_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full PortfolioPilot evaluation")
    parser.add_argument("--output", default=str(settings.CACHE_DIR / "full_evaluation_report.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--mode",
        default="synthetic_smoke",
        choices=["synthetic_smoke", "live_model"],
        help="CI-safe synthetic smoke or explicit live Qwen evaluation",
    )
    args = parser.parse_args()
    mode = "live_model_eval" if args.mode == "live_model" else args.mode
    report = run_full_evaluation(args.output, top_k=max(1, args.top_k), mode=mode)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
