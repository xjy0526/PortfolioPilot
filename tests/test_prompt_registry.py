import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prompts.financial_analysis_models import (
    FINANCIAL_ANALYSIS_JSON_SCHEMA,
    validate_financial_analysis_output,
)
from prompts.registry import PromptRegistry
from routes import prompts as prompt_routes
from services.financial_analysis import analyze_portfolio_with_llm
from services.llm import MockProvider
from services.llm import providers as provider_module


@pytest.fixture
def registry():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    value = PromptRegistry(connection)
    yield value
    connection.close()


def _prompt_payload(prompt_id="test-prompt"):
    return {
        "prompt_id": prompt_id,
        "name": f"Prompt {prompt_id}",
        "business_scene": "test_scene",
        "template": "Analyze {portfolio_json}",
        "variables": ["portfolio_json"],
        "input_schema": {
            "type": "object",
            "properties": {"portfolio_json": {"type": "string"}},
            "required": ["portfolio_json"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "model": "mock-model",
        "temperature": 0.1,
        "owner": "Risk Team",
    }


def test_prompt_publish_new_version_and_rollback(registry):
    template, first = registry.create_prompt(_prompt_payload())
    deployment_one = registry.publish(template.prompt_id, first.version)
    second = registry.create_version(
        template.prompt_id,
        {"template": "Analyze carefully {portfolio_json}", "change_log": "Stricter wording"},
    )
    deployment_two = registry.publish(template.prompt_id, second.version)

    assert deployment_one.version == 1
    assert deployment_two.previous_version == 1
    assert registry.get_prompt(template.prompt_id).published_version == 2

    rollback = registry.rollback(template.prompt_id)
    assert rollback.action == "rollback"
    assert rollback.version == 1
    assert registry.get_prompt(template.prompt_id).published_version == 1
    assert registry.get_version(template.prompt_id, 1).status == "published"
    assert registry.get_version(template.prompt_id, 2).status == "deprecated"


def test_invalid_prompt_schema_is_rejected(registry):
    payload = _prompt_payload("bad-schema")
    payload["output_schema"] = {
        "type": "object",
        "properties": {"answer": {"type": "imaginary"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    with pytest.raises(ValueError, match="Invalid JSON Schema type"):
        registry.create_prompt(payload)

    payload = _prompt_payload("open-schema")
    payload["output_schema"].pop("additionalProperties")
    with pytest.raises(ValueError, match="additionalProperties=false"):
        registry.create_prompt(payload)


def test_compare_versions_uses_same_test_set(registry):
    template, first = registry.create_prompt(_prompt_payload("compare"))
    second = registry.create_version(
        template.prompt_id,
        {"template": "Review {portfolio_json} and include CONTROL", "change_log": "Add control"},
    )
    result = registry.compare_versions(
        template.prompt_id,
        first.version,
        second.version,
        [{"variables": {"portfolio_json": "{}"}, "expected_tokens": ["CONTROL"]}],
    )
    assert result.metrics_a["expected_token_hit_rate"] == 0.0
    assert result.metrics_b["expected_token_hit_rate"] == 1.0
    assert result.metric_delta["expected_token_hit_rate"] == 1.0


def _valid_financial_payload():
    return {
        "portfolio_summary": "Risk score is 5.0 out of 10.",
        "risk_score": 5.0,
        "main_risks": ["Concentration is 20%."],
        "asset_level_comments": [
            {"ticker": "AAPL", "risk_level": "medium", "comment": "AAPL weight is 20%."}
        ],
        "rebalance_suggestions": [
            {"action": "hold", "ticker": "AAPL", "reason": "Maintain review.", "confidence": 0.5}
        ],
        "evidence_used": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        "disclaimer": "Research only.",
    }


def _structured_input():
    return {"risk_score": 5.0, "asset_metrics": {"AAPL": {"weight": 0.2, "risk_level": "medium"}}}


def test_structured_contract_rejects_fictitious_ticker_reference_and_number():
    payload = _valid_financial_payload()
    payload["asset_level_comments"][0]["ticker"] = "TSLA"
    with pytest.raises(ValueError, match="outside current portfolio"):
        validate_financial_analysis_output(payload, portfolio_risk_summary=_structured_input(), evidence=[])

    payload = _valid_financial_payload()
    payload["evidence_used"] = [{"document_id": "fake", "chunk_id": "fake-chunk"}]
    with pytest.raises(ValueError, match="outside retrieved evidence"):
        validate_financial_analysis_output(
            payload,
            portfolio_risk_summary=_structured_input(),
            evidence=[{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        )

    payload = _valid_financial_payload()
    payload["portfolio_summary"] = "An unsupported 77.7% loss was observed."
    with pytest.raises(ValueError, match="cannot be mapped"):
        validate_financial_analysis_output(payload, portfolio_risk_summary=_structured_input())


def test_pydantic_output_schema_forbids_additional_properties():
    assert FINANCIAL_ANALYSIS_JSON_SCHEMA["additionalProperties"] is False
    for definition in FINANCIAL_ANALYSIS_JSON_SCHEMA.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


@pytest.mark.asyncio
async def test_validation_retries_once_then_safe_fallback_and_records_prompt(registry):
    provider = MockProvider("{not valid json")
    result = await analyze_portfolio_with_llm(
        _structured_input(),
        evidence=[],
        language="en",
        provider=provider,
        registry=registry,
    )

    assert len(provider.calls) == 2
    assert result["source"] == "fallback"
    assert result["prompt_id"] == "financial-analysis"
    assert result["prompt_version"] == 1
    traces = registry.conn.execute(
        "SELECT prompt_id, prompt_version, status FROM llm_call_traces ORDER BY created_at"
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in traces] == [
        ("financial-analysis", 1, "failed"),
        ("financial-analysis", 1, "failed"),
    ]


def test_provider_factory_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "AI_PROVIDER", "qwen")
    monkeypatch.setattr(provider_module.settings, "QWEN_API_KEY", "")
    assert isinstance(provider_module.get_llm_provider(), MockProvider)


def test_prompt_api_lifecycle(monkeypatch, registry):
    app = FastAPI()
    app.include_router(prompt_routes.router)
    monkeypatch.setattr(prompt_routes, "get_prompt_registry", lambda: registry)
    client = TestClient(app)

    created = client.post("/api/prompts", json=_prompt_payload("api-prompt"))
    assert created.status_code == 201
    assert client.get("/api/prompts").json()["count"] == 1
    version = client.post(
        "/api/prompts/api-prompt/versions",
        json={"template": "Analyze with review {portfolio_json}"},
    )
    assert version.status_code == 201
    assert client.post("/api/prompts/api-prompt/versions/1/publish").status_code == 200
    assert client.post("/api/prompts/api-prompt/versions/2/publish").status_code == 200
    assert client.post("/api/prompts/api-prompt/rollback").json()["version"] == 1
    compared = client.post(
        "/api/prompts/compare",
        json={
            "prompt_id": "api-prompt", "version_a": 1, "version_b": 2,
            "test_cases": [{"variables": {"portfolio_json": "{}"}}],
        },
    )
    assert compared.status_code == 200
    assert compared.json()["test_case_count"] == 1
