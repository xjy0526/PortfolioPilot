"""CLI entry point for the governed PortfolioPilot evaluation V2 dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.reporting import EVALUATION_MODES
from evaluation.v2_dataset import DATASET_DIR
from evaluation.v2_runner import run_v2_evaluation, write_v2_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=EVALUATION_MODES, default="synthetic_smoke")
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--production-observations", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_v2_evaluation(
        mode=args.mode,
        dataset_root=args.dataset_dir,
        predictions_path=args.predictions,
        production_observations_path=args.production_observations,
    )
    output = write_v2_report(report, args.output)
    print(
        json.dumps(
            {
                "output": str(output),
                "evaluation_mode": report["evaluation_mode"],
                "sample_count": report["sample_count"],
                "dataset_version": report["dataset_version"],
                "mock_response_used": report["mock_response_used"],
                "human_reviewed_count": report["human_reviewed_count"],
                "badcase_distribution": report["badcase_distribution"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
