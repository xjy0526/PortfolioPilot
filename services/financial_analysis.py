"""Registry-driven, provider-neutral structured portfolio analysis."""
from __future__ import annotations

import json
import logging
import hashlib
import time
import uuid
from typing import Any

from prompts.financial_analysis_models import validate_financial_analysis_output
from prompts.financial_analysis_prompt import (
    SYSTEM_INSTRUCTION,
    build_financial_analysis_prompt,
    ensure_financial_analysis_prompt,
)
from prompts.registry import PromptRegistry
from services.llm import LLMProvider, LLMRequest, MockProvider, get_llm_provider

logger = logging.getLogger(__name__)


def parse_llm_json_response(
    raw: str,
    *,
    portfolio_risk_summary: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Parse and validate one response against the single Pydantic contract."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```json", 1)[1] if "```json" in cleaned else cleaned.split("```", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    payload = json.loads(cleaned.strip())
    output = validate_financial_analysis_output(
        payload,
        portfolio_risk_summary=portfolio_risk_summary,
        evidence=evidence,
    )
    return output.model_dump(mode="json")


async def analyze_portfolio_with_llm(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
    max_retries: int = 1,
    *,
    provider: LLMProvider | None = None,
    registry: PromptRegistry | None = None,
    run_id: str = "",
    user_id: str = "",
    business_scene: str = "portfolio_financial_analysis",
) -> dict[str, Any]:
    evidence = evidence or []
    registry = registry or PromptRegistry()
    prompt_version = ensure_financial_analysis_prompt(registry)
    supplied_provider = provider is not None
    provider = provider or get_llm_provider()
    if not supplied_provider and isinstance(provider, MockProvider):
        mock_payload = safe_financial_analysis_template(portfolio_risk_summary, evidence, language=language)
        provider = MockProvider(json.dumps(_contract_fields(mock_payload), ensure_ascii=False))

    last_error: Exception | None = None
    last_trace_id = ""
    input_hash = hashlib.sha256(
        json.dumps(
            {"portfolio": portfolio_risk_summary, "evidence": evidence},
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")
    ).hexdigest()
    document_ids = sorted({str(item.get("document_id") or "") for item in evidence if item.get("document_id")})
    chunk_ids = sorted({str(item.get("chunk_id") or item.get("id") or "") for item in evidence if item.get("chunk_id") or item.get("id")})
    for attempt in range(max(0, max_retries) + 1):
        retry_instruction = (
            "Previous output failed validation. Correct it and return valid JSON only."
            if attempt else ""
        )
        prompt = build_financial_analysis_prompt(
            portfolio_risk_summary,
            evidence,
            language,
            prompt_version=prompt_version,
            retry_instruction=retry_instruction,
        )
        request = LLMRequest(
            prompt=prompt,
            system_instruction=SYSTEM_INSTRUCTION,
            model=prompt_version.model,
            temperature=prompt_version.temperature,
            output_schema=prompt_version.output_schema,
        )
        trace_id = str(uuid.uuid4())
        last_trace_id = trace_id
        started = time.perf_counter()
        try:
            response = await provider.generate(request)
            result = parse_llm_json_response(
                response.text,
                portfolio_risk_summary=portfolio_risk_summary,
                evidence=evidence,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            input_tokens = _estimate_tokens(prompt + SYSTEM_INSTRUCTION)
            output_tokens = _estimate_tokens(response.text)
            registry.record_llm_call(
                trace_id=trace_id,
                prompt_id=prompt_version.prompt_id,
                prompt_version=prompt_version.version,
                provider=response.provider,
                model=response.model,
                status="success",
                run_id=run_id, user_id=user_id, business_scene=business_scene,
                model_parameters={"temperature": prompt_version.temperature},
                input_hash=input_hash, retrieved_document_ids=document_ids,
                retrieved_chunk_ids=chunk_ids, latency_ms=latency_ms,
                input_tokens=input_tokens, output_tokens=output_tokens,
                estimated_cost=_estimate_cost(response.provider, input_tokens, output_tokens),
                output_schema_valid=True, fallback_used=response.provider == "mock",
            )
            result.update({
                "source": response.provider,
                "ai_available": response.provider != "mock",
                "prompt_id": prompt_version.prompt_id,
                "prompt_version": prompt_version.version,
                "llm_trace_id": trace_id,
            })
            return result
        except Exception as exc:
            last_error = exc
            latency_ms = (time.perf_counter() - started) * 1000
            registry.record_llm_call(
                trace_id=trace_id,
                prompt_id=prompt_version.prompt_id,
                prompt_version=prompt_version.version,
                provider=getattr(provider, "provider_name", "unknown"),
                model=prompt_version.model,
                status="failed",
                validation_error=str(exc),
                run_id=run_id, user_id=user_id, business_scene=business_scene,
                model_parameters={"temperature": prompt_version.temperature},
                input_hash=input_hash, retrieved_document_ids=document_ids,
                retrieved_chunk_ids=chunk_ids, latency_ms=latency_ms,
                input_tokens=_estimate_tokens(prompt + SYSTEM_INSTRUCTION),
                output_tokens=0, output_schema_valid=False,
                error_type=type(exc).__name__,
            )
            logger.warning("Structured analysis attempt %s failed: %s", attempt + 1, exc)

    result = safe_financial_analysis_template(portfolio_risk_summary, evidence, language=language)
    if last_trace_id:
        registry.mark_trace_fallback(last_trace_id)
    result.update({
        "source": "fallback",
        "ai_available": False,
        "ai_error": str(last_error or "Unknown provider error"),
        "prompt_id": prompt_version.prompt_id,
        "prompt_version": prompt_version.version,
        "llm_trace_id": last_trace_id,
    })
    return result


def safe_financial_analysis_template(
    portfolio_risk_summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    evidence = evidence or []
    risk_score = float(portfolio_risk_summary.get("risk_score", 5.0) or 5.0)
    asset_metrics = portfolio_risk_summary.get("asset_metrics", {}) or {}
    flags = portfolio_risk_summary.get("concentration_flags", []) or []
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
        level = str(data.get("risk_level", "medium"))
        level = level if level in {"low", "medium", "high"} else "medium"
        weight = float(data.get("weight", 0.0) or 0.0) * 100
        comments.append({
            "ticker": ticker,
            "risk_level": level,
            "comment": (
                f"{ticker} 当前风险等级为 {level}，组合权重约 {weight:.1f}%，需关注波动、回撤和集中度。"
                if language == "zh" else
                f"{ticker} is classified as {level} risk with about {weight:.1f}% portfolio weight."
            ),
        })
        suggestions.append({
            "action": "watch" if level == "high" else "hold",
            "ticker": ticker,
            "reason": (
                f"{ticker} 风险等级为 {level}，建议先作为研究观察项并结合外部证据复核。"
                if language == "zh" else
                f"{ticker} is {level} risk; keep it under research review with external evidence."
            ),
            "confidence": 0.55 if level == "high" else 0.5,
        })
    references = [
        {
            "document_id": str(item.get("document_id") or item.get("source") or ""),
            "chunk_id": str(item.get("chunk_id") or item.get("id") or item.get("source") or ""),
        }
        for item in evidence[:5]
    ]
    payload = {
        "portfolio_summary": summary,
        "risk_score": risk_score,
        "main_risks": [str(item) for item in (flags or [no_flag])],
        "asset_level_comments": comments,
        "rebalance_suggestions": suggestions,
        "evidence_used": references,
        "disclaimer": disclaimer,
    }
    return validate_financial_analysis_output(
        payload,
        portfolio_risk_summary=portfolio_risk_summary,
        evidence=evidence,
    ).model_dump(mode="json")


def enforce_retrieved_citations(
    result: dict[str, Any],
    evidence: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Compatibility helper that removes unrecognized citation references."""
    evidence = evidence or []
    allowed = {
        (
            str(item.get("document_id") or item.get("source") or ""),
            str(item.get("chunk_id") or item.get("id") or item.get("source") or ""),
        )
        for item in evidence
    }
    sanitized = dict(result)
    references = []
    for item in result.get("evidence_used", []):
        if isinstance(item, dict):
            pair = (str(item.get("document_id", "")), str(item.get("chunk_id", "")))
        else:
            text = str(item)
            pair = tuple(text.split(":", 1)) if ":" in text else (text, text)
        if pair in allowed:
            references.append({"document_id": pair[0], "chunk_id": pair[1]})
    sanitized["evidence_used"] = references
    return sanitized


def _contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "portfolio_summary", "risk_score", "main_risks", "asset_level_comments",
            "rebalance_suggestions", "evidence_used", "disclaimer",
        )
    }


def _estimate_tokens(text: str) -> int:
    return max(1, (len(str(text or "")) + 3) // 4)


def _estimate_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    if provider == "mock":
        return 0.0
    return round(input_tokens * 0.0000005 + output_tokens * 0.0000015, 8)
