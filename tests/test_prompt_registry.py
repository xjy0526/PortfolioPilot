import json
from datetime import UTC, datetime
import pytest
from types import SimpleNamespace

from prompts.financial_analysis_models import (
    FINANCIAL_ANALYSIS_JSON_SCHEMA,
    validate_financial_analysis_output,
)
from services.llm import MockProvider
from services.llm import providers as provider_module
from prompts.registry import PromptRegistry


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


def test_invalid_prompt_schema_is_rejected():
    from prompts.registry_models import PromptVersion

    payload = _prompt_payload("bad-schema")
    payload["output_schema"] = {
        "type": "object",
        "properties": {"answer": {"type": "imaginary"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    with pytest.raises(ValueError, match="Invalid JSON Schema type"):
        PromptVersion(
            prompt_id=payload["prompt_id"],
            version=1,
            template=payload["template"],
            variables=payload["variables"],
            input_schema=payload["input_schema"],
            output_schema=payload["output_schema"],
            model=payload["model"],
            temperature=payload["temperature"],
            owner=payload["owner"],
        )

    payload = _prompt_payload("open-schema")
    payload["output_schema"].pop("additionalProperties")
    with pytest.raises(ValueError, match="additionalProperties=false"):
        PromptVersion(
            prompt_id=payload["prompt_id"], version=1, template=payload["template"],
            variables=payload["variables"], input_schema=payload["input_schema"],
            output_schema=payload["output_schema"], model=payload["model"],
            temperature=payload["temperature"], owner=payload["owner"],
        )


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
async def test_new_prompt_draft_does_not_unpublish_active_version():
    template = SimpleNamespace(
        id="prompt-uuid",
        prompt_key="financial-analysis",
        current_version=1,
        published_version=1,
        status="published",
    )
    version = SimpleNamespace(
        version=1,
        template="Analyze {portfolio_json}",
        variables=["portfolio_json"],
        input_schema=_prompt_payload()["input_schema"],
        output_schema=_prompt_payload()["output_schema"],
        model="qwen-plus",
        temperature=0.2,
        owner="Risk Team",
        status="published",
        change_log="Published baseline",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        baseline_metrics={},
    )

    class Repository:
        async def get_template(self, _prompt_key):
            return template

        async def get_version(self, _prompt_id, _version):
            return version

    class Session:
        def add(self, _value):
            return None

        async def flush(self):
            return None

    registry = PromptRegistry(Session())
    registry.repository = Repository()

    created = await registry.create_version(
        "financial-analysis",
        {"template": "Updated {portfolio_json}", "status": "draft"},
    )

    assert created is not None and created.version == 2
    assert template.current_version == 2
    assert template.published_version == 1
    assert template.status == "published"


@pytest.mark.asyncio
async def test_get_published_prompt_uses_exact_prompt_key():
    template = SimpleNamespace(
        id="prompt-uuid",
        prompt_key="financial-analysis",
        status="published",
        published_version=3,
    )
    version = SimpleNamespace(
        version=3,
        template="Analyze {portfolio_json}",
        variables=["portfolio_json"],
        input_schema=_prompt_payload()["input_schema"],
        output_schema=_prompt_payload()["output_schema"],
        model="qwen-plus",
        temperature=0.2,
        owner="Risk Team",
        status="published",
        change_log="Exact-key deployment",
        created_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        baseline_metrics={},
    )

    class Repository:
        async def get_template(self, prompt_key):
            assert prompt_key == "financial-analysis"
            return template

        async def get_version(self, prompt_id, version_number):
            assert prompt_id == "prompt-uuid"
            assert version_number == 3
            return version

    registry = PromptRegistry(SimpleNamespace())
    registry.repository = Repository()

    published = await registry.get_published("financial-analysis")

    assert published is not None
    assert published.prompt_id == "financial-analysis"
    assert published.version == 3


@pytest.mark.asyncio
async def test_provider_factory_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(provider_module.settings, "AI_PROVIDER", "qwen")
    monkeypatch.setattr(provider_module.settings, "QWEN_API_KEY", "")
    assert isinstance(await provider_module.get_llm_provider(), MockProvider)
