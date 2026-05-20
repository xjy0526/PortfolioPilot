import json

import pytest

from services.financial_analysis import (
    parse_llm_json_response,
    safe_financial_analysis_template,
)


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
    assert result["rebalance_suggestions"][0]["ticker"] == "AAPL"
    assert "投资建议" in result["disclaimer"]
