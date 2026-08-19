from pathlib import Path

import pytest

from evaluation.reporting import REQUIRED_REPORT_FIELDS, validate_evaluation_report
from scripts.check_evaluation_integrity import find_unqualified_claims


ROOT = Path(__file__).resolve().parent.parent


def _valid_metadata(**overrides):
    report = {
        "evaluation_mode": "synthetic_smoke",
        "generated_at": "2026-08-19T00:00:00+00:00",
        "git_commit_sha": "a" * 40,
        "mock_response_used": True,
        "synthetic_data_used": True,
        "model_provider": "deterministic_mock",
        "model_name": "portfolio-risk-mock-v1",
        "dataset_name": "portfolio_risk_cases",
        "dataset_version": "v1",
        "human_label_used": False,
        "production_data_used": False,
    }
    report.update(overrides)
    return report


def test_readme_has_no_unqualified_quality_claims():
    issues = find_unqualified_claims((ROOT / "README.md").read_text(encoding="utf-8"))

    assert issues == []


def test_claim_checker_rejects_absolute_and_unbounded_wording():
    text = "这是生产级平台。\n模型达到 100% 准确率。\n系统实现零幻觉。"

    assert len(find_unqualified_claims(text)) == 3
    assert find_unqualified_claims("当前尚非生产级系统。") == []


def test_report_contract_requires_provenance_and_dataset_version():
    report = _valid_metadata()

    validate_evaluation_report(report)
    assert REQUIRED_REPORT_FIELDS.issubset(report)

    with pytest.raises(ValueError, match="git_commit_sha"):
        validate_evaluation_report(report | {"git_commit_sha": "unknown"})
    with pytest.raises(ValueError, match="dataset_name and dataset_version"):
        validate_evaluation_report(report | {"dataset_version": ""})


def test_generated_metadata_discloses_dirty_worktree_state():
    from evaluation.reporting import build_evaluation_metadata

    metadata = build_evaluation_metadata(
        evaluation_mode="synthetic_smoke",
        model_provider="deterministic_fixture",
        model_name="fixture-v1",
        dataset_name="fixture",
        dataset_version="v1",
        mock_response_used=True,
        synthetic_data_used=True,
    )

    assert metadata["git_worktree_dirty"] in {True, False, None}


def test_report_contract_rejects_mode_disclosure_contradictions():
    with pytest.raises(ValueError, match="cannot use mock"):
        validate_evaluation_report(
            _valid_metadata(evaluation_mode="live_model_eval", mock_response_used=True)
        )
    with pytest.raises(ValueError, match="requires human labels"):
        validate_evaluation_report(
            _valid_metadata(evaluation_mode="human_gold_eval", human_label_used=False)
        )
    with pytest.raises(ValueError, match="requires production observation"):
        validate_evaluation_report(
            _valid_metadata(
                evaluation_mode="production_monitoring",
                mock_response_used=False,
                production_data_used=False,
            )
        )
