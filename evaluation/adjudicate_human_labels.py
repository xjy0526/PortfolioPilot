"""Create a pending adjudication queue from two completed independent reviews."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from config import settings
from evaluation.human_gold_dataset import DATASET_DIR, HumanGoldDataset
from evaluation.human_review import build_adjudication_queue, with_review_files


DEFAULT_OUTPUT = settings.CACHE_DIR / "evaluation" / "v3" / "adjudication_queue.jsonl"


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--reviewer-a", type=Path)
    parser.add_argument("--reviewer-b", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        dataset = with_review_files(
            HumanGoldDataset.load(args.dataset_dir),
            reviewer_a_path=args.reviewer_a,
            reviewer_b_path=args.reviewer_b,
        )
        result = build_adjudication_queue(dataset, output_path=args.output)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "created", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
