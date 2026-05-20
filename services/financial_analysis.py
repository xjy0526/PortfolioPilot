"""Structured LLM portfolio analysis with JSON parsing and safe fallback."""
from __future__ import annotations

import json
import logging
from typing import Any

from config import settings
from prompts.financial_analysis_prompt import (
    FINANCIAL_ANALYSIS_JSON_SCHEMA,
    SYSTEM_INSTRUCTION,
    build_financial_analysis_prompt,
)

logger = logging.getLogger(__name__)

VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_ACTIONS = {"buy", "hold", "reduce", "watch"}


def parse_llm_json_response(raw: str) -> dict[str, Any]:
    """Parse LLM JSON, including fenced Markdown JSON blocks."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1]
        else:
            cleaned = cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()
    data = json.loads(cleaned)
    return _normalize_analysis_payload(data)


async def analyze_portfolio_with_llm(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
    max_retries: int = 1,
) -> dict[str, Any]:
    """Analyze a portfolio with Qwen when configured, otherwise use fallback."""
    evidence = evidence or []
    if not settings.qwen_configured:
        result = safe_financial_analysis_template(portfolio_risk_summary, evidence, language=language)
        result["source"] = "mock"
        result["ai_available"] = False
        return result

    prompt = build_financial_analysis_prompt(portfolio_risk_summary, evidence, language=language)

    try:
        from services.vertex_ai import get_client

        client = get_client()
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            response = await client.aio.models.generate_content(
                model=settings.QWEN_MODEL,
                contents=prompt if attempt == 0 else prompt + "\n\nPrevious output was invalid JSON. Return valid JSON only.",
                config={
                    "system_instruction": SYSTEM_INSTRUCTION,
                    "response_mime_type": "application/json",
                    "response_schema": FINANCIAL_ANALYSIS_JSON_SCHEMA,
                },
            )
            try:
                result = parse_llm_json_response(response.text)
                result["source"] = "qwen"
                result["ai_available"] = True
                return result
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                last_error = exc
                logger.warning("Structured portfolio analysis parse failed on attempt %s: %s", attempt + 1, exc)

        raise RuntimeError(f"LLM JSON parse failed: {last_error}")
    except Exception as exc:
        logger.warning("Structured portfolio analysis fell back to safe template: %s", exc)
        result = safe_financial_analysis_template(portfolio_risk_summary, evidence, language=language)
        result["source"] = "fallback"
        result["ai_available"] = False
        result["ai_error"] = str(exc)
        return result


def safe_financial_analysis_template(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    """Return a deterministic analysis when LLM output is unavailable."""
    evidence = evidence or []
    risk_score = float(portfolio_risk_summary.get("risk_score", 5.0) or 5.0)
    asset_metrics = portfolio_risk_summary.get("asset_metrics", {}) or {}
    flags = portfolio_risk_summary.get("concentration_flags", []) or []
    high_assets = [
        ticker for ticker, data in asset_metrics.items()
        if data.get("risk_level") == "high"
    ]
    if language == "zh":
        summary = f"组合当前风险评分约为 {risk_score:.1f}/10，主要关注集中度、波动率和高风险资产暴露。"
        disclaimer = "本结果仅用于研究分析和风险提示，不构成投资建议或交易指令。"
        no_flag = "暂无明显集中度异常，但仍需结合行情和基本面持续复核。"
    else:
        summary = f"The portfolio risk score is about {risk_score:.1f}/10, with focus on concentration, volatility and high-risk exposure."
        disclaimer = "This output is for research and risk awareness only and is not investment advice or a trading instruction."
        no_flag = "No major concentration flag is detected, but market and fundamental risks still require review."

    comments = []
    suggestions = []
    for ticker, data in asset_metrics.items():
        level = data.get("risk_level", "medium")
        if level not in VALID_RISK_LEVELS:
            level = "medium"
        comments.append({
            "ticker": ticker,
            "risk_level": level,
            "comment": _fallback_asset_comment(ticker, data, language),
        })
        suggestions.append({
            "action": "watch" if level == "high" else "hold",
            "ticker": ticker,
            "reason": _fallback_rebalance_reason(ticker, data, language),
            "confidence": 0.55 if level == "high" else 0.5,
        })

    return {
        "portfolio_summary": summary,
        "risk_score": risk_score,
        "main_risks": flags or [no_flag],
        "asset_level_comments": comments,
        "rebalance_suggestions": suggestions,
        "evidence_used": [
            str(item.get("source") or item.get("path") or item.get("id") or "local evidence")
            for item in evidence[:5]
        ],
        "disclaimer": disclaimer,
    }


def _normalize_analysis_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")

    normalized = {
        "portfolio_summary": str(data.get("portfolio_summary", "")),
        "risk_score": _clamp_float(data.get("risk_score", 5.0), 1.0, 10.0),
        "main_risks": _string_list(data.get("main_risks")),
        "asset_level_comments": [],
        "rebalance_suggestions": [],
        "evidence_used": _string_list(data.get("evidence_used")),
        "disclaimer": str(data.get("disclaimer", "")),
    }

    for item in data.get("asset_level_comments") or []:
        if not isinstance(item, dict):
            continue
        risk_level = str(item.get("risk_level", "medium")).lower()
        if risk_level not in VALID_RISK_LEVELS:
            risk_level = "medium"
        normalized["asset_level_comments"].append({
            "ticker": str(item.get("ticker", "")).upper(),
            "risk_level": risk_level,
            "comment": str(item.get("comment", "")),
        })

    for item in data.get("rebalance_suggestions") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "hold")).lower()
        if action not in VALID_ACTIONS:
            action = "hold"
        normalized["rebalance_suggestions"].append({
            "action": action,
            "ticker": str(item.get("ticker", "")).upper(),
            "reason": str(item.get("reason", "")),
            "confidence": _clamp_float(item.get("confidence", 0.5), 0.0, 1.0),
        })

    if not normalized["portfolio_summary"]:
        raise ValueError("portfolio_summary is required")
    if not normalized["disclaimer"]:
        normalized["disclaimer"] = "Research and risk warning only. Not investment advice."
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _clamp_float(value: Any, lower: float, upper: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = lower
    return max(lower, min(upper, numeric))


def _fallback_asset_comment(ticker: str, data: dict[str, Any], language: str) -> str:
    weight = float(data.get("weight", 0.0) or 0.0) * 100
    level = data.get("risk_level", "medium")
    if language == "zh":
        return f"{ticker} 当前风险等级为 {level}，组合权重约 {weight:.1f}%，需关注波动、回撤和集中度。"
    return f"{ticker} is classified as {level} risk with about {weight:.1f}% portfolio weight."


def _fallback_rebalance_reason(ticker: str, data: dict[str, Any], language: str) -> str:
    level = data.get("risk_level", "medium")
    if language == "zh":
        return f"{ticker} 风险等级为 {level}，建议先作为研究观察项并结合外部证据复核。"
    return f"{ticker} is {level} risk; keep it under research review with external evidence."
