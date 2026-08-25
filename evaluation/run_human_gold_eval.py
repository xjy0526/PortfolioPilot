"""Run a provenance-safe V3 live or adjudicated human-gold evaluation."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config import settings
from evaluation.human_gold_dataset import DATASET_DIR, HumanGoldDataset
from evaluation.human_gold_metrics import (
    build_human_gold_report,
    build_live_model_provenance_report,
)
from evaluation.human_review import with_review_files
from evaluation.reporting import EVALUATION_MODES


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=EVALUATION_MODES,
        default="human_gold_eval",
    )
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path)
    parser.add_argument("--reviewer-b", type=Path)
    parser.add_argument("--adjudicated", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _load_bundle(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("prediction bundle must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    output = args.output or (
        settings.CACHE_DIR
        / "evaluation"
        / "v3"
        / args.mode
        / "evaluation_report.json"
    )
    try:
        dataset = with_review_files(
            HumanGoldDataset.load(args.dataset_dir),
            reviewer_a_path=args.reviewer_a,
            reviewer_b_path=args.reviewer_b,
            adjudicated_path=args.adjudicated,
        )
        bundle = _load_bundle(args.predictions)
        if args.mode == "synthetic_smoke":
            raise RuntimeError(
                "synthetic_smoke is isolated from V3 human review; use run_v2_eval"
            )
        if args.mode == "production_monitoring":
            raise RuntimeError(
                "production_monitoring requires a governed production observation source"
            )
        if args.mode == "human_gold_eval":
            report = build_human_gold_report(
                dataset=dataset,
                bundle=bundle,
                prediction_path=args.predictions,
            )
        else:
            report = build_live_model_provenance_report(
                dataset=dataset,
                bundle=bundle,
                prediction_path=args.predictions,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str(output),
                "evaluation_mode": report["evaluation_mode"],
                "dataset_version": report["dataset_version"],
                "sample_count": report["sample_count"],
                "mock_response_used": report["mock_response_used"],
                "human_label_used": report["human_label_used"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
