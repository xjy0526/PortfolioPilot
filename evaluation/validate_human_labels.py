"""Validate independent V3 reviewer and adjudication files without approving them."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from evaluation.human_gold_dataset import DATASET_DIR, HumanGoldDataset
from evaluation.human_review import review_status_summary, with_review_files


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--reviewer-a", type=Path)
    parser.add_argument("--reviewer-b", type=Path)
    parser.add_argument("--adjudicated", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-human-gold", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        dataset = with_review_files(
            HumanGoldDataset.load(args.dataset_dir),
            reviewer_a_path=args.reviewer_a,
            reviewer_b_path=args.reviewer_b,
            adjudicated_path=args.adjudicated,
        )
        if args.require_human_gold:
            dataset.require_adjudicated_labels()
        elif args.require_complete:
            dataset.require_completed_review_pair()
        result = review_status_summary(dataset)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "valid", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
