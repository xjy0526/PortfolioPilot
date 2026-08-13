import json
from types import SimpleNamespace

import pytest

from services.financial_analysis import (
    analyze_portfolio_with_llm,
    parse_llm_json_response,
    safe_financial_analysis_template,
)
from services.llm import LLMRequest, LLMResponse


VALID_PAYLOAD = {
    "portfolio_summary": "组合风险中等。",
    "risk_score": 5.5,
    "main_risks": ["科技行业集中"],
    "asset_level_comments": [
        {"ticker": "AAPL", "risk_level": "medium", "comment": "权重较高"}
    ],
    "rebalance_suggestions": [
        {"action": "hold", "ticker": "AAPL", "reason": "质量较高", "confidence": 0.7}
    ],
    "evidence_used": ["local.md"],
    "disclaimer": "仅用于研究和风险提示，不构成投资建议。",
}


def test_parse_normal_json():
    parsed = parse_llm_json_response(json.dumps(VALID_PAYLOAD, ensure_ascii=False))

    assert parsed["risk_score"] == 5.5
    assert parsed["asset_level_comments"][0]["risk_level"] == "medium"


def test_parse_markdown_json_block():
    parsed = parse_llm_json_response("```json\n" + json.dumps(VALID_PAYLOAD, ensure_ascii=False) + "\n```")

    assert parsed["portfolio_summary"]
    assert parsed["rebalance_suggestions"][0]["action"] == "hold"


def test_parse_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json_response("not a json payload")


def test_safe_template_has_required_shape():
    result = safe_financial_analysis_template({
        "risk_score": 6.0,
        "asset_metrics": {
            "AAPL": {"risk_level": "medium", "weight": 0.2},
        },
        "concentration_flags": ["single_asset:AAPL:20%"],
    })

    assert result["portfolio_summary"]
    assert result["main_risks"]
    assert result["research_observations"][0]["ticker"] == "AAPL"
    assert result["review_priorities"][0]["ticker"] == "AAPL"
    assert result["rebalance_suggestions"] == []
    assert "rebalance_suggestions" in result["deprecated_fields"]
    assert "投资建议" in result["disclaimer"]


class InvalidUsageProvider:
    provider_name = "qwen"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text="not-json",
            provider="qwen",
            model=request.model,
            usage={
                "prompt_tokens": 101,
                "completion_tokens": 7,
                "cost": 0.0042,
                "cost_currency": "USD",
            },
            usage_source="provider",
        )


class RecordingTraceRegistry:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.fallback_trace_id = ""

    async def record_llm_call(self, **kwargs):
        self.records.append(kwargs)

    async def mark_trace_fallback(self, trace_id: str):
        self.fallback_trace_id = trace_id


@pytest.mark.asyncio
async def test_invalid_json_trace_keeps_provider_usage_and_cost(monkeypatch):
    registry = RecordingTraceRegistry()
    prompt_version = SimpleNamespace(
        prompt_id="financial-analysis",
        version=1,
        model="qwen-plus",
        temperature=0.2,
        template=(
            "{language} {portfolio_risk_summary_json} {evidence_text} "
            "{retry_instruction}"
        ),
        output_schema={"type": "object"},
    )

    async def fake_prompt_seed(_registry):
        return prompt_version

    monkeypatch.setattr(
        "services.financial_analysis.ensure_financial_analysis_prompt",
        fake_prompt_seed,
    )
    result = await analyze_portfolio_with_llm(
        {
            "risk_score": 5.0,
            "asset_metrics": {"AAPL": {"risk_level": "medium", "weight": 0.2}},
            "concentration_flags": [],
        },
        provider=InvalidUsageProvider(),
        registry=registry,
        max_retries=0,
    )

    assert result["source"] == "fallback"
    assert registry.fallback_trace_id == result["llm_trace_id"]
    assert len(registry.records) == 1
    trace = registry.records[0]
    assert trace["status"] == "failed"
    assert trace["usage_source"] == "provider"
    assert trace["cost_source"] == "provider"
    assert trace["input_tokens"] == 101
    assert trace["output_tokens"] == 7
    assert trace["cost_amount"] == pytest.approx(0.0042)
    assert trace["provider_usage"]["prompt_tokens"] == 101
