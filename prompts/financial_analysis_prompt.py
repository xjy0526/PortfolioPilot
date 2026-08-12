"""Financial-analysis Prompt Registry seed and renderer."""
from __future__ import annotations

import json
from typing import Any

from config import settings
from prompts.financial_analysis_models import FINANCIAL_ANALYSIS_JSON_SCHEMA
from prompts.registry import PromptRegistry
from prompts.registry_models import PromptVersion


FINANCIAL_ANALYSIS_PROMPT_ID = "financial-analysis"
FINANCIAL_ANALYSIS_SCENE = "portfolio_financial_analysis"
SYSTEM_INSTRUCTION = (
    "You are a careful buy-side portfolio risk research assistant. "
    "The system only provides research analysis and risk warnings. "
    "It does not provide personalized investment advice, trading instructions, "
    "or guarantees of future returns. Deterministic optimizers alone own target weights. "
    "Never create or modify target_weight. Return only valid JSON."
)

DEFAULT_TEMPLATE = """Write the response in {language}.
Analyze this mixed-asset portfolio for securities research and fund asset-management risk control.
This is research and risk analysis only, not investment advice or a trade order.

Portfolio risk summary JSON:
{portfolio_risk_summary_json}

Retrieved citation objects:
{evidence_text}

Return exactly one JSON object matching the registered output schema.
Every ticker must come from the portfolio input. Every evidence_used item must contain an exact
document_id and chunk_id from the retrieved citation objects. Do not introduce financial numbers
that cannot be mapped to the structured portfolio input. Express interpretation only through
research_observations and review_priorities. Do not calculate or propose allocation weights.
The deprecated rebalance_suggestions field must be an empty array. {retry_instruction}"""

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "portfolio_risk_summary_json": {"type": "string"},
        "evidence_text": {"type": "string"},
        "retry_instruction": {"type": "string"},
    },
    "required": ["language", "portfolio_risk_summary_json", "evidence_text", "retry_instruction"],
    "additionalProperties": False,
}


def ensure_financial_analysis_prompt(registry: PromptRegistry | None = None) -> PromptVersion:
    registry = registry or PromptRegistry()
    published = registry.get_published_by_scene(FINANCIAL_ANALYSIS_SCENE)
    if published and _has_current_analysis_contract(published.output_schema):
        return published
    existing = registry.get_prompt(FINANCIAL_ANALYSIS_PROMPT_ID)
    if existing:
        target = registry.create_version(existing.prompt_id, {
            "template": DEFAULT_TEMPLATE,
            "variables": ["language", "portfolio_risk_summary_json", "evidence_text", "retry_instruction"],
            "input_schema": INPUT_SCHEMA,
            "output_schema": FINANCIAL_ANALYSIS_JSON_SCHEMA,
            "model": settings.QWEN_MODEL,
            "temperature": 0.2,
            "owner": "Research Platform",
            "change_log": "Move allocation ownership to deterministic optimizers",
        })
        assert target is not None
        registry.publish(existing.prompt_id, target.version)
        return registry.get_version(existing.prompt_id, target.version) or target
    _, version = registry.create_prompt({
        "prompt_id": FINANCIAL_ANALYSIS_PROMPT_ID,
        "name": "Financial Portfolio Analysis",
        "business_scene": FINANCIAL_ANALYSIS_SCENE,
        "template": DEFAULT_TEMPLATE,
        "variables": ["language", "portfolio_risk_summary_json", "evidence_text", "retry_instruction"],
        "input_schema": INPUT_SCHEMA,
        "output_schema": FINANCIAL_ANALYSIS_JSON_SCHEMA,
        "model": settings.QWEN_MODEL,
        "temperature": 0.2,
        "owner": "Research Platform",
        "change_log": "Registry baseline migrated from financial_analysis_prompt",
    })
    registry.publish(version.prompt_id, version.version)
    return registry.get_version(version.prompt_id, version.version) or version


def _has_current_analysis_contract(schema: dict[str, Any]) -> bool:
    properties = schema.get("properties", {})
    return {"research_observations", "review_priorities"}.issubset(properties)


def build_financial_analysis_prompt(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
    *,
    prompt_version: PromptVersion | None = None,
    retry_instruction: str = "",
) -> str:
    prompt_version = prompt_version or ensure_financial_analysis_prompt()
    evidence = evidence or []
    citations = [
        {
            "document_id": item.get("document_id") or item.get("source") or "",
            "chunk_id": item.get("chunk_id") or item.get("id") or item.get("source") or "",
            "title": item.get("title") or item.get("source") or "",
            "quote": str(item.get("quote") or item.get("text") or "")[:1200],
        }
        for item in evidence
    ]
    values = {
        "language": "Chinese" if language == "zh" else "English",
        "portfolio_risk_summary_json": json.dumps(portfolio_risk_summary, ensure_ascii=False, default=str),
        "evidence_text": json.dumps(citations, ensure_ascii=False),
        "retry_instruction": retry_instruction,
    }
    return prompt_version.template.format_map(values)
