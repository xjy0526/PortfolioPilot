"""Shared provenance and integrity rules for evaluation reports."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from config import settings


EvaluationMode = Literal[
    "synthetic_smoke",
    "live_model_eval",
    "human_gold_eval",
    "production_monitoring",
]
EVALUATION_MODES: tuple[EvaluationMode, ...] = (
    "synthetic_smoke",
    "live_model_eval",
    "human_gold_eval",
    "production_monitoring",
)
REQUIRED_REPORT_FIELDS = frozenset(
    {
        "evaluation_mode",
        "generated_at",
        "git_commit_sha",
        "mock_response_used",
        "model_provider",
        "model_name",
        "dataset_name",
        "dataset_version",
        "human_label_used",
        "production_data_used",
    }
)

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def normalize_evaluation_mode(mode: str) -> EvaluationMode:
    """Return one canonical evaluation mode or reject the value."""
    if mode not in EVALUATION_MODES:
        allowed = ", ".join(EVALUATION_MODES)
        raise ValueError(f"Unsupported evaluation mode {mode!r}; expected one of: {allowed}")
    return mode


def require_automated_runner_mode(mode: str) -> EvaluationMode:
    """Reject modes whose required governed data source was not supplied."""
    canonical_mode = normalize_evaluation_mode(mode)
    if canonical_mode == "human_gold_eval":
        raise RuntimeError(
            "human_gold_eval requires an explicitly supplied human-labeled dataset; "
            "the synthetic runner cannot create one"
        )
    if canonical_mode == "production_monitoring":
        raise RuntimeError(
            "production_monitoring requires an explicit production observation source; "
            "the evaluation runner cannot synthesize production data"
        )
    return canonical_mode


def resolve_git_commit_sha() -> str:
    """Resolve a full commit SHA without inventing a provenance value."""
    candidates = (
        str(getattr(settings, "CODE_VERSION", "") or "").strip(),
        os.getenv("GITHUB_HEAD_SHA", "").strip(),
        os.getenv("GITHUB_SHA", "").strip(),
        os.getenv("GIT_COMMIT_SHA", "").strip(),
    )
    for candidate in candidates:
        if _SHA_PATTERN.fullmatch(candidate):
            return candidate.lower()

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Evaluation reports require a full git commit SHA. Set CODE_VERSION "
            "when the .git directory is unavailable."
        ) from exc

    resolved = completed.stdout.strip()
    if not _SHA_PATTERN.fullmatch(resolved):
        raise RuntimeError("Unable to resolve a full git commit SHA for the evaluation report")
    return resolved.lower()


def resolve_git_worktree_dirty() -> bool | None:
    """Return whether tracked or untracked files differ from HEAD, or None if unknown."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(completed.stdout.strip())


def build_evaluation_metadata(
    *,
    evaluation_mode: str,
    model_provider: str,
    model_name: str,
    dataset_name: str,
    dataset_version: str,
    mock_response_used: bool,
    synthetic_data_used: bool,
    human_label_used: bool = False,
    production_data_used: bool = False,
) -> dict[str, Any]:
    """Build the mandatory, auditable metadata shared by every report."""
    mode = normalize_evaluation_mode(evaluation_mode)
    metadata: dict[str, Any] = {
        "evaluation_mode": mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit_sha": resolve_git_commit_sha(),
        "git_worktree_dirty": resolve_git_worktree_dirty(),
        "mock_response_used": bool(mock_response_used),
        "synthetic_data_used": bool(synthetic_data_used),
        "model_provider": str(model_provider or "not_applicable"),
        "model_name": str(model_name or "not_applicable"),
        "dataset_name": str(dataset_name),
        "dataset_version": str(dataset_version),
        "human_label_used": bool(human_label_used),
        "production_data_used": bool(production_data_used),
        "real_model_used": mode == "live_model_eval",
    }
    validate_evaluation_report(metadata)
    return metadata


def validate_evaluation_report(report: dict[str, Any]) -> None:
    """Reject incomplete provenance or contradictory mode disclosures."""
    missing = sorted(REQUIRED_REPORT_FIELDS.difference(report))
    if missing:
        raise ValueError(f"Evaluation report is missing required fields: {', '.join(missing)}")

    mode = normalize_evaluation_mode(str(report["evaluation_mode"]))
    if not _SHA_PATTERN.fullmatch(str(report["git_commit_sha"])):
        raise ValueError("git_commit_sha must be a full 40-character hexadecimal commit SHA")
    if not str(report["dataset_name"]).strip() or not str(report["dataset_version"]).strip():
        raise ValueError("dataset_name and dataset_version must be explicit")
    if not str(report["model_provider"]).strip() or not str(report["model_name"]).strip():
        raise ValueError("model_provider and model_name must be explicit")
    try:
        generated_at = datetime.fromisoformat(str(report["generated_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO-8601 timestamp") from exc
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must include a timezone")

    mock_used = report["mock_response_used"] is True
    human_used = report["human_label_used"] is True
    production_used = report["production_data_used"] is True
    synthetic_used = report.get("synthetic_data_used") is True
    if mode == "synthetic_smoke" and not (mock_used or synthetic_used):
        raise ValueError("synthetic_smoke must disclose mock responses or synthetic data")
    if mode == "live_model_eval" and mock_used:
        raise ValueError("live_model_eval cannot use mock responses")
    if mode == "human_gold_eval" and not human_used:
        raise ValueError("human_gold_eval requires human labels")
    if mode == "production_monitoring" and not production_used:
        raise ValueError("production_monitoring requires production observation data")


def report_metadata_view(report: dict[str, Any]) -> dict[str, Any] | None:
    """Return safe dashboard metadata only for a valid report."""
    if not isinstance(report, dict):
        return None
    try:
        validate_evaluation_report(report)
    except (TypeError, ValueError):
        return None
    return {key: report[key] for key in sorted(REQUIRED_REPORT_FIELDS)} | {
        "git_worktree_dirty": report.get("git_worktree_dirty"),
        "synthetic_data_used": bool(report.get("synthetic_data_used")),
        "real_model_used": bool(report.get("real_model_used")),
    }
