"""Validate V3 candidates or prepare two independently assigned review packets."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from evaluation.human_gold_dataset import DATASET_DIR, HumanGoldDataset
from evaluation.human_review import prepare_review_packets, review_status_summary


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--reviewer-a")
    parser.add_argument("--reviewer-b")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--predictions", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        dataset = HumanGoldDataset.load(args.dataset_dir)
        assignment_requested = any(
            value is not None
            for value in (args.reviewer_a, args.reviewer_b, args.output_dir)
        )
        if assignment_requested:
            if not args.reviewer_a or not args.reviewer_b or args.output_dir is None:
                raise ValueError(
                    "--reviewer-a, --reviewer-b and --output-dir are required together"
                )
            result = prepare_review_packets(
                dataset,
                reviewer_a_id=args.reviewer_a,
                reviewer_b_id=args.reviewer_b,
                output_dir=args.output_dir,
                prediction_path=args.predictions,
            )
        else:
            result = review_status_summary(dataset)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
