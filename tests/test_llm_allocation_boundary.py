"""LLM wording may not mutate deterministic allocation fields."""
from services.holding_recommendations import _normalize_ai_report


def test_holding_recommendation_llm_cannot_change_target_weight_or_action():
    base = {
        "summary": "base",
        "portfolio_score": 60.0,
        "portfolio_view": "balanced",
        "key_actions": [],
        "risk_warnings": [],
        "next_review": "later",
        "recommendations": [
            {
                "ticker": "AAPL",
                "action": "hold",
                "priority": 5,
                "confidence": 50,
                "target_weight_pct": 20.0,
                "rationale": "deterministic rationale",
                "risk": "deterministic risk",
            }
        ],
    }
    attempted_override = {
        "portfolio_score": 99.0,
        "portfolio_view": "constructive",
        "recommendations": [
            {
                "ticker": "AAPL",
                "action": "add",
                "priority": 10,
                "confidence": 100,
                "target_weight_pct": 90.0,
                "rationale": "evidence-grounded wording",
                "risk": "review evidence",
            }
        ],
    }

    result = _normalize_ai_report(attempted_override, base, lang="en")
    item = result["recommendations"][0]

    assert item["target_weight_pct"] == 20.0
    assert item["action"] == "hold"
    assert item["priority"] == 5
    assert result["portfolio_score"] == 60.0
    assert result["portfolio_view"] == "balanced"
    assert result["target_weight_owner"] == "deterministic_rules"
    assert item["rationale"] == "evidence-grounded wording"
