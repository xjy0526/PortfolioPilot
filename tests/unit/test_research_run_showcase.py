"""Unit guards for Showcase truth labels and explicit unavailable states."""
from __future__ import annotations

from app.db.models import LLMCallTrace, WorkflowRun
from app.services.research_run_showcase import (
    _evidence_payload,
    _truth_labels,
    _validation_payload,
)


def test_evidence_insufficient_is_explicit_without_fabricated_scores() -> None:
    payload = _evidence_payload(["missing-chunk"], [])

    assert payload == {
        "status": "evidence_insufficient",
        "requested_count": 1,
        "visible_count": 0,
        "withheld_or_invalid_count": 1,
        "items": [],
    }


def test_truth_labels_only_use_persisted_boolean_values() -> None:
    run = WorkflowRun(
        context_json={
            "synthetic_data_used": True,
            "production_data_used": "false",
        }
    )
    trace = LLMCallTrace(
        response_payload={
            "mock_response_used": True,
            "real_model_used": False,
        },
        model_parameters={},
    )

    labels = _truth_labels(run, trace)

    assert labels["synthetic_data_used"] is True
    assert labels["mock_response_used"] is True
    assert labels["real_model_used"] is False
    assert labels["production_data_used"] is None
    assert labels["human_label_used"] is None


def test_missing_validations_remain_unavailable_and_schema_failure_is_visible() -> None:
    trace = LLMCallTrace(output_schema_valid=False, evidence_ids=[])
    evidence = _evidence_payload([], [])

    validation = {
        item["key"]: item
        for item in _validation_payload([], trace, evidence)
    }

    assert validation["schema"]["status"] == "failed"
    assert validation["numeric_consistency"]["status"] == "unavailable"
    assert validation["numeric_consistency"]["reason"] == "not_recorded_by_workflow"
    assert validation["citation_reference_validity"]["status"] == "failed"
    assert validation["citation_reference_validity"]["reason"] == "evidence_insufficient"
    assert validation["compliance_rules"]["status"] == "unavailable"
