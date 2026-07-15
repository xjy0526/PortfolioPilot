"""Single Pydantic contract for structured portfolio analysis output."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetLevelComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    risk_level: Literal["low", "medium", "high"]
    comment: str

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, value: str) -> str:
        return value.strip().upper()


class RebalanceSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["buy", "hold", "reduce", "watch"]
    ticker: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, value: str) -> str:
        return value.strip().upper()


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    chunk_id: str


class FinancialAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_summary: str
    risk_score: float = Field(ge=1.0, le=10.0)
    main_risks: list[str]
    asset_level_comments: list[AssetLevelComment]
    rebalance_suggestions: list[RebalanceSuggestion]
    evidence_used: list[EvidenceReference]
    disclaimer: str


FINANCIAL_ANALYSIS_JSON_SCHEMA: dict[str, Any] = FinancialAnalysisOutput.model_json_schema()


def validate_financial_analysis_output(
    payload: dict[str, Any],
    *,
    portfolio_risk_summary: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> FinancialAnalysisOutput:
    normalized = dict(payload)
    normalized["evidence_used"] = [_normalize_reference(item) for item in payload.get("evidence_used", [])]
    output = FinancialAnalysisOutput.model_validate(normalized)
    if portfolio_risk_summary is not None:
        _validate_tickers(output, portfolio_risk_summary)
        _validate_financial_numbers(output, portfolio_risk_summary)
    if evidence is not None:
        _validate_evidence(output, evidence)
    return output


def _normalize_reference(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {"document_id": str(value.get("document_id", "")), "chunk_id": str(value.get("chunk_id", ""))}
    text = str(value or "").strip()
    if ":" in text:
        document_id, chunk_id = text.split(":", 1)
        return {"document_id": document_id, "chunk_id": chunk_id}
    return {"document_id": text, "chunk_id": text}


def _validate_tickers(output: FinancialAnalysisOutput, summary: dict[str, Any]) -> None:
    allowed = {str(value).upper() for value in (summary.get("asset_metrics") or {}).keys()}
    generated = {
        item.ticker for item in [*output.asset_level_comments, *output.rebalance_suggestions]
        if item.ticker
    }
    unknown = sorted(generated - allowed)
    if unknown:
        raise ValueError(f"Output contains tickers outside current portfolio: {', '.join(unknown)}")


def _validate_evidence(output: FinancialAnalysisOutput, evidence: list[dict[str, Any]]) -> None:
    allowed = {
        (
            str(item.get("document_id") or item.get("source") or ""),
            str(item.get("chunk_id") or item.get("id") or item.get("source") or ""),
        )
        for item in evidence
    }
    generated = {(item.document_id, item.chunk_id) for item in output.evidence_used}
    unknown = sorted(generated - allowed)
    if unknown:
        formatted = ", ".join(f"{document_id}:{chunk_id}" for document_id, chunk_id in unknown)
        raise ValueError(f"Output contains citations outside retrieved evidence: {formatted}")


def _validate_financial_numbers(output: FinancialAnalysisOutput, summary: dict[str, Any]) -> None:
    expected_risk = summary.get("risk_score")
    if expected_risk is not None and abs(output.risk_score - float(expected_risk)) > 1e-6:
        raise ValueError("risk_score does not map to the structured portfolio input")
    allowed = {0.0, 1.0, 10.0, 100.0}
    for number in _walk_numbers(summary):
        allowed.add(round(number, 6))
        allowed.add(round(number * 100.0, 6))
    texts = [output.portfolio_summary, output.disclaimer, *output.main_risks]
    texts.extend(item.comment for item in output.asset_level_comments)
    texts.extend(item.reason for item in output.rebalance_suggestions)
    ticker_labels = sorted(
        (str(value) for value in (summary.get("asset_metrics") or {}).keys()),
        key=len,
        reverse=True,
    )
    for text in texts:
        numeric_text = text
        for ticker in ticker_labels:
            numeric_text = numeric_text.replace(ticker, "")
        for raw in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", numeric_text):
            value = round(float(raw), 6)
            if not any(abs(value - candidate) <= 1e-4 for candidate in allowed):
                raise ValueError(f"Financial number {raw} cannot be mapped to structured input")


def _walk_numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_numbers(child)
