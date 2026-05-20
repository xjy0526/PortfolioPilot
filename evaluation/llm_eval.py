"""Evaluate structured LLM financial analysis quality.

The evaluator uses synthetic portfolio-risk scenarios with known expected risk
signals. It can call the real Qwen-compatible analysis path, but defaults to a
deterministic mock response when no Qwen API key is configured.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from services.financial_analysis import (
    analyze_portfolio_with_llm,
    parse_llm_json_response,
)


RISK_ALIASES: dict[str, list[str]] = {
    "single_asset_concentration": ["single asset", "single-name", "single_asset", "单资产", "个股集中", "单一资产"],
    "sector_concentration": ["sector concentration", "sector:", "行业集中", "板块集中", "sector"],
    "high_volatility": ["high volatility", "volatility", "高波动", "波动"],
    "high_drawdown": ["drawdown", "回撤"],
    "prediction_market_exposure": ["prediction market", "polymarket", "预测市场"],
    "low_risk": ["low risk", "低风险", "defensive", "稳健"],
    "multi_asset_diversification": ["diversified", "diversification", "多资产", "分散"],
}


@dataclass(frozen=True)
class PortfolioRiskTestCase:
    id: str
    scenario: str
    portfolio_risk_summary: dict[str, Any]
    evidence: list[dict[str, Any]]
    expected_risk_tags: list[str]
    expected_rebalance_tickers: list[str]


async def run_llm_evaluation(
    output_path: str | Path | None = None,
    use_mock: bool | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    """Run the evaluation suite and optionally write an evaluation report."""
    cases = build_portfolio_risk_test_cases()
    effective_mock = (not settings.qwen_configured) if use_mock is None else use_mock
    case_results = []

    for case in cases:
        result = await evaluate_case(case, use_mock=effective_mock, language=language)
        case_results.append(result)

    metrics = aggregate_metrics(case_results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "mock" if effective_mock else "qwen",
        "model": settings.QWEN_MODEL if not effective_mock else "mock-llm-financial-analysis",
        "test_case_count": len(cases),
        "metrics": metrics,
        "cases": case_results,
    }

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


async def evaluate_case(
    case: PortfolioRiskTestCase,
    use_mock: bool = True,
    language: str = "zh",
) -> dict[str, Any]:
    """Evaluate one test case and return metric flags."""
    raw_response = ""
    parsed: dict[str, Any] | None = None
    json_valid = False
    error = ""

    try:
        if use_mock:
            raw_response = mock_llm_response(case, language=language)
            parsed = parse_llm_json_response(raw_response)
        else:
            parsed = await analyze_portfolio_with_llm(
                case.portfolio_risk_summary,
                evidence=case.evidence,
                language=language,
            )
            raw_response = json.dumps(parsed, ensure_ascii=False)
        json_valid = True
    except Exception as exc:
        error = str(exc)

    response = parsed or {}
    risk_detected = bool(response) and _detect_expected_risks(response, case.expected_risk_tags)
    evidence_used = bool(response) and _uses_allowed_evidence(response, case.evidence)
    rebalance_explainable = bool(response) and _has_explainable_rebalance(response, case.expected_rebalance_tickers)
    hallucination_flag = bool(response) and _has_hallucination(response, case)

    return {
        "id": case.id,
        "scenario": case.scenario,
        "expected_risk_tags": case.expected_risk_tags,
        "expected_rebalance_tickers": case.expected_rebalance_tickers,
        "json_valid": json_valid,
        "risk_detected": risk_detected,
        "evidence_used": evidence_used,
        "rebalance_explainable": rebalance_explainable,
        "hallucination_flag": hallucination_flag,
        "output_summary": str(response.get("portfolio_summary", ""))[:300] if response else "",
        "main_risks": response.get("main_risks", []) if response else [],
        "rebalance_suggestions": response.get("rebalance_suggestions", []) if response else [],
        "evidence_used_values": response.get("evidence_used", []) if response else [],
        "error": error,
    }


def aggregate_metrics(case_results: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate evaluation flags into requested rates."""
    total = len(case_results)
    if total == 0:
        return {
            "json_valid_rate": 0.0,
            "risk_detection_rate": 0.0,
            "evidence_usage_rate": 0.0,
            "rebalance_explainability_rate": 0.0,
            "hallucination_flag_rate": 0.0,
        }

    def rate(key: str) -> float:
        return round(sum(1 for item in case_results if item.get(key)) / total, 4)

    return {
        "json_valid_rate": rate("json_valid"),
        "risk_detection_rate": rate("risk_detected"),
        "evidence_usage_rate": rate("evidence_used"),
        "rebalance_explainability_rate": rate("rebalance_explainable"),
        "hallucination_flag_rate": rate("hallucination_flag"),
    }


def build_portfolio_risk_test_cases() -> list[PortfolioRiskTestCase]:
    """Construct at least 20 portfolio risk test cases."""
    specs = [
        ("single_tech_mega_cap", "单资产集中：NVDA 占组合 48%", ["single_asset_concentration"], {"NVDA": (0.48, "Technology", "US Equity", "high", 0.36, -0.22), "MSFT": (0.18, "Technology", "US Equity", "medium", 0.22, -0.10), "CASH": (0.34, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("single_china_a_share", "单资产集中：贵州茅台占组合 52%", ["single_asset_concentration"], {"600519.SS": (0.52, "Consumer", "China A-Share", "high", 0.28, -0.18), "AAPL": (0.20, "Technology", "US Equity", "medium", 0.24, -0.12), "CASH": (0.28, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("technology_sector_cluster", "行业集中：科技行业超过 70%", ["sector_concentration"], {"AAPL": (0.24, "Technology", "US Equity", "medium", 0.21, -0.09), "MSFT": (0.24, "Technology", "US Equity", "medium", 0.18, -0.08), "NVDA": (0.24, "Technology", "US Equity", "high", 0.38, -0.20), "SPY": (0.28, "ETF", "ETF", "low", 0.13, -0.06)}),
        ("consumer_sector_cluster", "行业集中：消费板块过高", ["sector_concentration"], {"600519.SS": (0.35, "Consumer", "China A-Share", "medium", 0.22, -0.12), "PG": (0.25, "Consumer", "US Equity", "low", 0.12, -0.05), "KO": (0.20, "Consumer", "US Equity", "low", 0.11, -0.04), "BND": (0.20, "Bond ETF", "ETF", "low", 0.05, -0.02)}),
        ("high_vol_crypto_proxy", "高波动：预测市场和加密相关资产", ["high_volatility", "prediction_market_exposure"], {"POLY-BTC-150K-2026": (0.18, "Prediction Markets", "Prediction Market", "high", 0.80, -0.35), "COIN": (0.27, "Financials", "US Equity", "high", 0.55, -0.28), "SPY": (0.35, "ETF", "ETF", "low", 0.14, -0.06), "CASH": (0.20, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("high_vol_semis", "高波动：半导体组合", ["high_volatility"], {"NVDA": (0.30, "Semiconductors", "US Equity", "high", 0.44, -0.21), "AMD": (0.25, "Semiconductors", "US Equity", "high", 0.48, -0.24), "TSM": (0.20, "Semiconductors", "US Equity", "medium", 0.30, -0.16), "CASH": (0.25, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("drawdown_growth_stocks", "高回撤：成长股回撤明显", ["high_drawdown"], {"SHOP": (0.25, "Technology", "US Equity", "high", 0.42, -0.38), "TSLA": (0.25, "Consumer Cyclical", "US Equity", "high", 0.50, -0.42), "ARKK": (0.20, "ETF", "ETF", "high", 0.46, -0.36), "CASH": (0.30, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("drawdown_china_growth", "高回撤：中国成长股波动回撤", ["high_drawdown", "high_volatility"], {"300750.SZ": (0.30, "Industrials", "China A-Share", "high", 0.42, -0.32), "BABA": (0.24, "Consumer Cyclical", "US Equity", "high", 0.40, -0.35), "KWEB": (0.20, "ETF", "ETF", "high", 0.39, -0.34), "CASH": (0.26, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("multi_asset_balanced", "多资产分散：股票、ETF、现金", ["multi_asset_diversification"], {"SPY": (0.25, "ETF", "ETF", "low", 0.13, -0.06), "BND": (0.25, "Bond ETF", "ETF", "low", 0.05, -0.02), "AAPL": (0.15, "Technology", "US Equity", "medium", 0.21, -0.08), "600519.SS": (0.10, "Consumer", "China A-Share", "medium", 0.22, -0.10), "CASH": (0.25, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("global_equity_diversified", "多资产分散：全球股票 ETF", ["multi_asset_diversification"], {"VT": (0.45, "ETF", "ETF", "low", 0.14, -0.07), "BND": (0.20, "Bond ETF", "ETF", "low", 0.05, -0.02), "GLD": (0.15, "Commodity ETF", "ETF", "low", 0.12, -0.05), "CASH": (0.20, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("low_risk_cash_bonds", "低风险组合：现金和债券 ETF 为主", ["low_risk"], {"BND": (0.45, "Bond ETF", "ETF", "low", 0.05, -0.02), "SHV": (0.30, "Treasury ETF", "ETF", "low", 0.02, -0.01), "CASH": (0.25, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("low_risk_dividend", "低风险组合：防御型股息资产", ["low_risk"], {"JNJ": (0.20, "Healthcare", "US Equity", "low", 0.13, -0.05), "PG": (0.20, "Consumer", "US Equity", "low", 0.12, -0.05), "BND": (0.35, "Bond ETF", "ETF", "low", 0.05, -0.02), "CASH": (0.25, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("prediction_market_concentrated", "预测市场暴露过高", ["prediction_market_exposure", "high_volatility"], {"POLY-TRUMP-2028": (0.16, "Prediction Markets", "Prediction Market", "high", 0.75, -0.40), "POLY-BTC-150K-2026": (0.14, "Prediction Markets", "Prediction Market", "high", 0.82, -0.45), "SPY": (0.40, "ETF", "ETF", "low", 0.14, -0.06), "CASH": (0.30, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("cash_drag_low_risk", "低风险但现金占比过高", ["low_risk"], {"CASH": (0.70, "Cash", "Cash", "low", 0.00, 0.00), "SHV": (0.20, "Treasury ETF", "ETF", "low", 0.02, -0.01), "SPY": (0.10, "ETF", "ETF", "low", 0.14, -0.06)}),
        ("financial_sector_cluster", "行业集中：金融股集中", ["sector_concentration"], {"JPM": (0.25, "Financials", "US Equity", "medium", 0.22, -0.11), "BAC": (0.23, "Financials", "US Equity", "medium", 0.24, -0.13), "GS": (0.20, "Financials", "US Equity", "medium", 0.25, -0.14), "SPY": (0.32, "ETF", "ETF", "low", 0.14, -0.06)}),
        ("healthcare_defensive", "低风险医疗防御组合", ["low_risk", "multi_asset_diversification"], {"XLV": (0.35, "Healthcare", "ETF", "low", 0.12, -0.05), "JNJ": (0.15, "Healthcare", "US Equity", "low", 0.13, -0.05), "BND": (0.30, "Bond ETF", "ETF", "low", 0.05, -0.02), "CASH": (0.20, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("emerging_market_vol", "高波动：新兴市场敞口", ["high_volatility"], {"EEM": (0.35, "ETF", "ETF", "medium", 0.28, -0.18), "KWEB": (0.25, "ETF", "ETF", "high", 0.39, -0.32), "INDA": (0.20, "ETF", "ETF", "medium", 0.27, -0.16), "CASH": (0.20, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("leveraged_etf_risk", "高波动高回撤：杠杆 ETF", ["high_volatility", "high_drawdown"], {"TQQQ": (0.25, "Leveraged ETF", "ETF", "high", 0.72, -0.48), "SOXL": (0.20, "Leveraged ETF", "ETF", "high", 0.86, -0.55), "SPY": (0.35, "ETF", "ETF", "low", 0.14, -0.06), "CASH": (0.20, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("two_name_concentration", "双资产集中度偏高", ["single_asset_concentration"], {"AAPL": (0.34, "Technology", "US Equity", "medium", 0.21, -0.08), "MSFT": (0.33, "Technology", "US Equity", "medium", 0.18, -0.08), "BND": (0.18, "Bond ETF", "ETF", "low", 0.05, -0.02), "CASH": (0.15, "Cash", "Cash", "low", 0.00, 0.00)}),
        ("mixed_risk_complex", "混合风险：行业集中、高波动与预测市场", ["sector_concentration", "high_volatility", "prediction_market_exposure"], {"NVDA": (0.24, "Technology", "US Equity", "high", 0.44, -0.21), "AMD": (0.20, "Technology", "US Equity", "high", 0.48, -0.24), "AAPL": (0.20, "Technology", "US Equity", "medium", 0.21, -0.08), "POLY-TRUMP-2028": (0.12, "Prediction Markets", "Prediction Market", "high", 0.75, -0.40), "BND": (0.24, "Bond ETF", "ETF", "low", 0.05, -0.02)}),
    ]
    return [_case_from_spec(*spec) for spec in specs]


def mock_llm_response(case: PortfolioRiskTestCase, language: str = "zh") -> str:
    """Return a deterministic valid JSON response for offline evaluation."""
    summary = case.portfolio_risk_summary
    asset_metrics = summary.get("asset_metrics", {})
    risk_phrases = [_risk_phrase(tag, language) for tag in case.expected_risk_tags]
    suggestions = []
    comments = []

    for ticker, metrics in asset_metrics.items():
        level = str(metrics.get("risk_level", "medium"))
        weight = float(metrics.get("weight", 0.0) or 0.0)
        action = "watch" if level == "high" else "reduce" if weight >= 0.30 else "hold"
        comments.append({
            "ticker": ticker,
            "risk_level": level if level in {"low", "medium", "high"} else "medium",
            "comment": f"{ticker} 权重 {weight:.1%}，风险等级 {level}，需要关注{', '.join(risk_phrases)}。",
        })
        suggestions.append({
            "action": action,
            "ticker": ticker,
            "reason": f"基于权重 {weight:.1%}、风险等级 {level} 和测试场景 {case.scenario}，建议保持可解释的风险控制动作。",
            "confidence": 0.72 if level == "high" else 0.58,
        })

    payload = {
        "portfolio_summary": f"{case.scenario}。核心风险包括：{'；'.join(risk_phrases)}。",
        "risk_score": float(summary.get("risk_score", 5.0) or 5.0),
        "main_risks": risk_phrases,
        "asset_level_comments": comments,
        "rebalance_suggestions": suggestions,
        "evidence_used": [item["source"] for item in case.evidence[:2]],
        "disclaimer": "本结果仅用于研究分析和风险提示，不构成投资建议或交易指令。",
    }
    return json.dumps(payload, ensure_ascii=False)


def _case_from_spec(
    case_id: str,
    scenario: str,
    expected_tags: list[str],
    holdings: dict[str, tuple[float, str, str, str, float, float]],
) -> PortfolioRiskTestCase:
    asset_metrics = {}
    sector_concentration: dict[str, dict[str, Any]] = {}
    asset_type_exposure: dict[str, dict[str, Any]] = {}
    concentration_flags = []

    for ticker, (weight, sector, asset_type, risk_level, vol, drawdown) in holdings.items():
        asset_metrics[ticker] = {
            "ticker": ticker,
            "weight": weight,
            "sector": sector,
            "asset_type": asset_type,
            "risk_level": risk_level,
            "annual_volatility": vol,
            "max_drawdown": drawdown,
            "sharpe_ratio": 0.4 if risk_level == "high" else 0.9,
        }
        _add_exposure(sector_concentration, sector, weight)
        _add_exposure(asset_type_exposure, asset_type, weight)
        if weight >= 0.30 and ticker != "CASH":
            concentration_flags.append(f"single_asset:{ticker}:{weight:.1%}")

    for sector, data in sector_concentration.items():
        if float(data["weight"]) >= 0.55 and sector != "Cash":
            concentration_flags.append(f"sector:{sector}:{float(data['weight']):.1%}")
    for asset_type, data in asset_type_exposure.items():
        if asset_type == "Prediction Market" and float(data["weight"]) >= 0.10:
            concentration_flags.append(f"prediction_market:{float(data['weight']):.1%}")

    max_vol = max(float(item["annual_volatility"]) for item in asset_metrics.values())
    max_dd = min(float(item["max_drawdown"]) for item in asset_metrics.values())
    risk_score = 2.0
    if "single_asset_concentration" in expected_tags:
        risk_score += 2.0
    if "sector_concentration" in expected_tags:
        risk_score += 1.5
    if "high_volatility" in expected_tags:
        risk_score += 2.0
    if "high_drawdown" in expected_tags:
        risk_score += 2.0
    if "prediction_market_exposure" in expected_tags:
        risk_score += 1.0
    if "low_risk" in expected_tags:
        risk_score = min(risk_score, 3.0)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_value": 1_000_000,
        "portfolio_metrics": {
            "period_return": 0.03,
            "annual_return": 0.08,
            "annual_volatility": round(max_vol, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_ratio": 0.7,
        },
        "risk_score": round(min(10.0, max(1.0, risk_score)), 1),
        "risk_level": "low" if risk_score <= 3 else "medium" if risk_score <= 6 else "high",
        "asset_weights": {ticker: data["weight"] for ticker, data in asset_metrics.items()},
        "sector_concentration": sector_concentration,
        "asset_type_exposure": asset_type_exposure,
        "asset_metrics": asset_metrics,
        "concentration_flags": concentration_flags,
        "data_quality": {"positions": len(asset_metrics), "price_history_assets": len(asset_metrics), "price_history_points": 252},
    }
    evidence = [
        {
            "source": f"{case_id}_risk_note.md",
            "text": f"{scenario}; expected risk tags: {', '.join(expected_tags)}.",
        },
        {
            "source": f"{case_id}_market_context.csv",
            "text": "Volatility, drawdown, sector concentration and external evidence should be considered.",
        },
    ]
    expected_tickers = [
        ticker for ticker, metrics in asset_metrics.items()
        if ticker != "CASH" and (metrics["risk_level"] == "high" or metrics["weight"] >= 0.30)
    ] or [next(ticker for ticker in asset_metrics if ticker != "CASH")]
    return PortfolioRiskTestCase(
        id=case_id,
        scenario=scenario,
        portfolio_risk_summary=summary,
        evidence=evidence,
        expected_risk_tags=expected_tags,
        expected_rebalance_tickers=expected_tickers,
    )


def _add_exposure(target: dict[str, dict[str, Any]], key: str, weight: float) -> None:
    bucket = target.setdefault(key, {"value": 0.0, "weight": 0.0, "count": 0})
    bucket["weight"] = float(bucket["weight"]) + weight
    bucket["value"] = round(float(bucket["weight"]) * 1_000_000, 2)
    bucket["count"] = int(bucket["count"]) + 1


def _risk_phrase(tag: str, language: str) -> str:
    zh = {
        "single_asset_concentration": "单资产集中风险",
        "sector_concentration": "行业集中风险",
        "high_volatility": "高波动风险",
        "high_drawdown": "高回撤风险",
        "prediction_market_exposure": "预测市场敞口风险",
        "low_risk": "低风险稳健特征",
        "multi_asset_diversification": "多资产分散特征",
    }
    en = {
        "single_asset_concentration": "single asset concentration risk",
        "sector_concentration": "sector concentration risk",
        "high_volatility": "high volatility risk",
        "high_drawdown": "high drawdown risk",
        "prediction_market_exposure": "prediction market exposure risk",
        "low_risk": "low risk defensive profile",
        "multi_asset_diversification": "multi asset diversification",
    }
    return (zh if language == "zh" else en).get(tag, tag)


def _detect_expected_risks(response: dict[str, Any], expected_tags: list[str]) -> bool:
    text = _response_text(response).lower()
    for tag in expected_tags:
        aliases = RISK_ALIASES.get(tag, [tag])
        if not any(alias.lower() in text for alias in aliases):
            return False
    return True


def _uses_allowed_evidence(response: dict[str, Any], evidence: list[dict[str, Any]]) -> bool:
    allowed = {str(item.get("source", "")) for item in evidence}
    used = {str(item) for item in response.get("evidence_used", [])}
    return bool(allowed & used)


def _has_explainable_rebalance(response: dict[str, Any], expected_tickers: list[str]) -> bool:
    suggestions = response.get("rebalance_suggestions", []) or []
    by_ticker = {
        str(item.get("ticker", "")).upper(): item
        for item in suggestions
        if isinstance(item, dict)
    }
    for ticker in expected_tickers:
        item = by_ticker.get(ticker.upper())
        if not item:
            return False
        reason = str(item.get("reason", "")).strip()
        if len(reason) < 12:
            return False
    return True


def _has_hallucination(response: dict[str, Any], case: PortfolioRiskTestCase) -> bool:
    known_tickers = set(case.portfolio_risk_summary.get("asset_metrics", {}).keys())
    allowed_evidence = {str(item.get("source", "")) for item in case.evidence}
    output_tickers = set()
    for item in response.get("asset_level_comments", []) or []:
        if isinstance(item, dict):
            output_tickers.add(str(item.get("ticker", "")).upper())
    for item in response.get("rebalance_suggestions", []) or []:
        if isinstance(item, dict):
            output_tickers.add(str(item.get("ticker", "")).upper())
    if any(ticker and ticker not in known_tickers for ticker in output_tickers):
        return True
    used_evidence = {str(item) for item in response.get("evidence_used", [])}
    if any(item and item not in allowed_evidence for item in used_evidence):
        return True
    return False


def _response_text(response: dict[str, Any]) -> str:
    parts = [
        str(response.get("portfolio_summary", "")),
        " ".join(str(item) for item in response.get("main_risks", []) or []),
    ]
    for item in response.get("asset_level_comments", []) or []:
        if isinstance(item, dict):
            parts.append(str(item.get("comment", "")))
            parts.append(str(item.get("risk_level", "")))
    for item in response.get("rebalance_suggestions", []) or []:
        if isinstance(item, dict):
            parts.append(str(item.get("reason", "")))
            parts.append(str(item.get("action", "")))
    return " ".join(parts)


def run_llm_evaluation_sync(
    output_path: str | Path | None = None,
    use_mock: bool | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    """Synchronous wrapper for scripts and tests."""
    return asyncio.run(run_llm_evaluation(output_path=output_path, use_mock=use_mock, language=language))
