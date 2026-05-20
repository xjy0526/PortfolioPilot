"""Structured financial analysis prompt for Qwen-compatible LLMs."""
from __future__ import annotations

import json
from typing import Any


FINANCIAL_ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "portfolio_summary": {"type": "string"},
        "risk_score": {"type": "number"},
        "main_risks": {"type": "array", "items": {"type": "string"}},
        "asset_level_comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                    "comment": {"type": "string"},
                },
                "required": ["ticker", "risk_level", "comment"],
            },
        },
        "rebalance_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["buy", "hold", "reduce", "watch"]},
                    "ticker": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["action", "ticker", "reason", "confidence"],
            },
        },
        "evidence_used": {"type": "array", "items": {"type": "string"}},
        "disclaimer": {"type": "string"},
    },
    "required": [
        "portfolio_summary",
        "risk_score",
        "main_risks",
        "asset_level_comments",
        "rebalance_suggestions",
        "evidence_used",
        "disclaimer",
    ],
}


SYSTEM_INSTRUCTION = (
    "You are a careful buy-side portfolio risk research assistant. "
    "The system only provides research analysis and risk warnings. "
    "It does not provide personalized investment advice, trading instructions, "
    "or guarantees of future returns. Return only valid JSON."
)


def build_financial_analysis_prompt(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
) -> str:
    """Build the user prompt for structured portfolio analysis."""
    lang = "Chinese" if language == "zh" else "English"
    evidence = evidence or []
    evidence_lines = []
    for idx, item in enumerate(evidence, start=1):
        source = item.get("source") or item.get("path") or "local_document"
        text = str(item.get("text", ""))[:1200]
        evidence_lines.append(f"[{idx}] {source}: {text}")

    return (
        f"Write the response in {lang}.\n"
        "Analyze this mixed-asset portfolio for securities research and fund asset-management risk control.\n"
        "Important: this is research and risk提示 only; it is not investment advice and must not be written as a trade order.\n\n"
        "Portfolio risk summary JSON:\n"
        f"{json.dumps(portfolio_risk_summary, ensure_ascii=False, default=str)}\n\n"
        "External evidence from local RAG retrieval:\n"
        f"{chr(10).join(evidence_lines) if evidence_lines else 'No external evidence was retrieved.'}\n\n"
        "Return exactly one JSON object matching the provided schema. "
        "Use risk_score on a 1-10 scale. Confidence should be between 0 and 1. "
        "Use only these action values: buy, hold, reduce, watch. "
        "Use only these risk levels: low, medium, high."
    )
