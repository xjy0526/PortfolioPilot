"""Portfolio-level AI holding recommendations.

The module first builds deterministic, score-driven recommendations so the
feature remains useful in demo mode or without a Qwen API key. When Qwen is
configured, it asks the model to refine the summary and wording while keeping
the same structured output shape.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


HOLDING_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "portfolio_score": {"type": "number"},
        "portfolio_view": {
            "type": "string",
            "enum": ["constructive", "balanced", "cautious", "defensive"],
        },
        "key_actions": {"type": "array", "items": {"type": "string"}},
        "risk_warnings": {"type": "array", "items": {"type": "string"}},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "name": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["add", "hold", "trim", "reduce", "review"],
                    },
                    "priority": {"type": "integer"},
                    "confidence": {"type": "integer"},
                    "current_weight_pct": {"type": "number"},
                    "target_weight_pct": {"type": "number"},
                    "score": {"type": "number"},
                    "rating": {"type": "string"},
                    "rationale": {"type": "string"},
                    "risk": {"type": "string"},
                    "asset_type": {"type": "string"},
                    "market": {"type": "string"},
                },
                "required": [
                    "ticker",
                    "name",
                    "action",
                    "priority",
                    "confidence",
                    "current_weight_pct",
                    "target_weight_pct",
                    "score",
                    "rating",
                    "rationale",
                    "risk",
                ],
            },
        },
        "next_review": {"type": "string"},
    },
    "required": [
        "summary",
        "portfolio_score",
        "portfolio_view",
        "key_actions",
        "risk_warnings",
        "recommendations",
        "next_review",
    ],
}


async def generate_holding_recommendations(lang: str = "zh") -> dict[str, Any]:
    """Return portfolio-level recommendations for every current holding."""
    from state import portfolio_data

    summary = portfolio_data.get("summary")
    if not summary or not getattr(summary, "stocks", None):
        return {
            "error": _txt(lang, "no_data"),
            "source": "none",
            "recommendations": [],
        }

    base_report = build_rule_based_recommendations(summary, lang=lang)

    if not settings.gemini_configured:
        base_report["source"] = "rule_based"
        base_report["ai_available"] = False
        base_report["ai_note"] = _txt(lang, "no_ai")
        return base_report

    try:
        ai_report = await _call_qwen_for_holding_recommendations(base_report, lang=lang)
        normalized = _normalize_ai_report(ai_report, base_report, lang=lang)
        normalized["source"] = "qwen"
        normalized["ai_available"] = True
        return normalized
    except Exception as exc:
        logger.warning("Holding recommendation AI refinement failed: %s", exc)
        base_report["source"] = "rule_based"
        base_report["ai_available"] = False
        base_report["ai_error"] = str(exc)
        base_report["ai_note"] = _txt(lang, "ai_failed")
        return base_report


def build_rule_based_recommendations(summary, lang: str = "zh") -> dict[str, Any]:
    """Build deterministic recommendations from scores, weights and risk flags."""
    total_value = float(getattr(summary, "total_value", 0) or 0)
    stocks = [
        stock for stock in getattr(summary, "stocks", [])
        if getattr(stock.position, "ticker", "") != "CASH"
    ]

    if not stocks:
        return {
            "summary": _txt(lang, "empty"),
            "portfolio_score": 0,
            "portfolio_view": "defensive",
            "key_actions": [],
            "risk_warnings": [],
            "recommendations": [],
            "next_review": _txt(lang, "next_review"),
            "generated_at": _utc_now_iso(),
        }

    sector_weights = _build_sector_weights(stocks, total_value)
    recommendations = [
        _recommend_single_holding(stock, total_value, sector_weights, lang)
        for stock in stocks
    ]
    recommendations.sort(key=lambda item: (item["priority"], abs(item["current_weight_pct"])), reverse=True)

    weighted_score = _weighted_portfolio_score(stocks, total_value)
    portfolio_view = _portfolio_view(weighted_score, recommendations)
    risk_warnings = _build_risk_warnings(stocks, sector_weights, lang)
    key_actions = _build_key_actions(recommendations, lang)

    return {
        "summary": _build_summary(weighted_score, portfolio_view, recommendations, lang),
        "portfolio_score": round(weighted_score, 1),
        "portfolio_view": portfolio_view,
        "key_actions": key_actions,
        "risk_warnings": risk_warnings,
        "recommendations": recommendations,
        "next_review": _txt(lang, "next_review"),
        "generated_at": _utc_now_iso(),
    }


def _recommend_single_holding(stock, total_value: float, sector_weights: dict[str, float], lang: str) -> dict[str, Any]:
    pos = stock.position
    score_obj = getattr(stock, "score", None)
    score = float(getattr(score_obj, "total_score", 50) or 50)
    rating = getattr(getattr(score_obj, "rating", None), "value", "hold") or "hold"
    confidence = int(round(float(getattr(score_obj, "confidence", 0.45) or 0.45) * 100))
    weight = round((pos.current_value / total_value * 100) if total_value > 0 else 0, 1)
    pnl_pct = float(getattr(pos, "pnl_percent", 0) or 0)
    asset_type = getattr(pos, "asset_type", "equity") or "equity"
    sector = getattr(pos, "sector", "") or "Unknown"
    sector_weight = sector_weights.get(sector, 0)

    action = "hold"
    priority = 3
    target_weight = weight
    reasons: list[str] = []
    risks: list[str] = []

    if asset_type == "prediction_market":
        action, priority, target_weight = _prediction_market_action(weight, pnl_pct)
        reasons.append(_txt(lang, "prediction_reason"))
        if weight > 5:
            risks.append(_txt(lang, "prediction_weight_risk"))
        if pnl_pct > 25:
            risks.append(_txt(lang, "profit_lock"))
    elif rating == "sell" or score < 42:
        action, priority, target_weight = "reduce", 9, max(0, round(weight * 0.45, 1))
        reasons.append(_txt(lang, "weak_score").format(score=score))
    elif score >= 76 and weight < 12 and sector_weight < 38:
        action, priority, target_weight = "add", 8, min(12, round(weight + 3, 1))
        reasons.append(_txt(lang, "strong_score").format(score=score))
    elif score >= 68 and weight <= 18:
        action, priority, target_weight = "hold", 5, weight
        reasons.append(_txt(lang, "quality_hold").format(score=score))
    elif score < 55:
        action, priority, target_weight = "review", 6, max(0, round(weight * 0.75, 1))
        reasons.append(_txt(lang, "needs_review").format(score=score))

    if weight > 22:
        priority = max(priority, 8)
        if action == "add":
            action = "hold"
            target_weight = weight
        elif action == "hold":
            action = "trim"
            target_weight = min(weight, 18)
        risks.append(_txt(lang, "single_position_risk").format(weight=weight))

    if sector_weight > 38 and action == "add":
        action = "hold"
        target_weight = weight
        risks.append(_txt(lang, "sector_risk").format(sector=sector, weight=sector_weight))

    if pnl_pct < -20 and score < 60:
        priority = max(priority, 7)
        risks.append(_txt(lang, "drawdown_risk").format(pnl=pnl_pct))

    if not reasons:
        reasons.append(_txt(lang, "balanced_hold").format(score=score))
    if not risks:
        risks.append(_txt(lang, "standard_risk"))

    return {
        "ticker": pos.ticker,
        "name": pos.name or pos.ticker,
        "action": action,
        "priority": int(max(1, min(10, priority))),
        "confidence": int(max(0, min(100, confidence))),
        "current_weight_pct": weight,
        "target_weight_pct": round(target_weight, 1),
        "score": round(score, 1),
        "rating": rating,
        "pnl_pct": round(pnl_pct, 1),
        "sector": sector,
        "asset_type": asset_type,
        "market": getattr(pos, "market", "") or "Global",
        "rationale": " ".join(reasons),
        "risk": " ".join(risks),
    }


def _prediction_market_action(weight: float, pnl_pct: float) -> tuple[str, int, float]:
    if weight > 6:
        return "trim", 8, 4.0
    if pnl_pct > 35:
        return "trim", 7, max(1.0, round(weight * 0.7, 1))
    if pnl_pct < -30:
        return "review", 7, max(0.5, round(weight * 0.6, 1))
    return "hold", 4, weight


def _build_sector_weights(stocks: list[Any], total_value: float) -> dict[str, float]:
    sectors: dict[str, float] = {}
    if total_value <= 0:
        return sectors
    for stock in stocks:
        pos = stock.position
        sector = getattr(pos, "sector", "") or "Unknown"
        sectors[sector] = sectors.get(sector, 0) + getattr(pos, "current_value", 0)
    return {sector: round(value / total_value * 100, 1) for sector, value in sectors.items()}


def _weighted_portfolio_score(stocks: list[Any], total_value: float) -> float:
    if total_value <= 0:
        scores = [float(getattr(s.score, "total_score", 50) or 50) for s in stocks if getattr(s, "score", None)]
        return sum(scores) / len(scores) if scores else 50
    weighted = 0.0
    covered = 0.0
    for stock in stocks:
        score_obj = getattr(stock, "score", None)
        score = float(getattr(score_obj, "total_score", 50) or 50)
        value = float(getattr(stock.position, "current_value", 0) or 0)
        weighted += score * value
        covered += value
    return weighted / covered if covered > 0 else 50


def _portfolio_view(score: float, recommendations: list[dict[str, Any]]) -> str:
    reduce_count = sum(1 for item in recommendations if item["action"] in {"reduce", "trim"})
    if score >= 72 and reduce_count <= 1:
        return "constructive"
    if score >= 58:
        return "balanced"
    if score >= 45:
        return "cautious"
    return "defensive"


def _build_risk_warnings(stocks: list[Any], sector_weights: dict[str, float], lang: str) -> list[str]:
    warnings: list[str] = []
    for sector, weight in sorted(sector_weights.items(), key=lambda item: item[1], reverse=True):
        if weight > 38:
            warnings.append(_txt(lang, "sector_warning").format(sector=sector, weight=weight))
    for stock in stocks:
        pos = stock.position
        weight = (pos.current_value / sum(s.position.current_value for s in stocks) * 100) if stocks else 0
        if weight > 22:
            warnings.append(_txt(lang, "position_warning").format(ticker=pos.ticker, weight=round(weight, 1)))
    return warnings[:5]


def _build_key_actions(recommendations: list[dict[str, Any]], lang: str) -> list[str]:
    key_items = [item for item in recommendations if item["action"] in {"add", "trim", "reduce", "review"}]
    result = []
    for item in key_items[:4]:
        result.append(_txt(lang, f"action_{item['action']}").format(ticker=item["ticker"]))
    if not result:
        result.append(_txt(lang, "no_major_action"))
    return result


def _build_summary(score: float, view: str, recommendations: list[dict[str, Any]], lang: str) -> str:
    add_count = sum(1 for item in recommendations if item["action"] == "add")
    reduce_count = sum(1 for item in recommendations if item["action"] in {"trim", "reduce"})
    return _txt(lang, "summary").format(score=round(score, 1), view=_view_label(view, lang), add=add_count, reduce=reduce_count)


async def _call_qwen_for_holding_recommendations(base_report: dict[str, Any], lang: str) -> dict[str, Any]:
    from services.llm.compat import get_client

    client = get_client()
    language = "Chinese" if lang == "zh" else "English"
    prompt = (
        f"Create portfolio-level holding recommendations in {language}. "
        "Use the provided rule-based baseline as the source of truth. "
        "Do not invent new tickers. Keep every holding in the recommendations array. "
        "You may improve rationale, risk wording, summary and key actions. "
        "Deterministic rules own all target_weight_pct values: repeat them unchanged and never "
        "calculate or modify allocation weights.\n\n"
        f"Baseline JSON:\n{json.dumps(base_report, ensure_ascii=False)}"
    )

    response = await client.aio.models.generate_content(
        model=settings.QWEN_MODEL,
        contents=prompt,
        config={
            "system_instruction": (
                "You are a careful portfolio risk analyst. Return only valid JSON. "
                "This is educational analysis, not personalized financial advice."
            ),
            "response_mime_type": "application/json",
            "response_schema": HOLDING_RECOMMENDATION_SCHEMA,
        },
    )
    return _parse_json_response(response.text)


def _parse_json_response(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def _normalize_ai_report(ai_report: dict[str, Any], base_report: dict[str, Any], lang: str) -> dict[str, Any]:
    base_by_ticker = {item["ticker"]: item for item in base_report.get("recommendations", [])}
    ai_recs = ai_report.get("recommendations") or []
    normalized_recs = []

    for base_item in base_report.get("recommendations", []):
        ai_item = next((item for item in ai_recs if item.get("ticker") == base_item["ticker"]), {})
        merged = dict(base_item)
        for key in ("rationale", "risk"):
            if key in ai_item and ai_item[key] not in (None, ""):
                merged[key] = ai_item[key]
        normalized_recs.append(merged)

    result = dict(base_report)
    result.update({
        "summary": ai_report.get("summary") or base_report["summary"],
        "portfolio_score": base_report["portfolio_score"],
        "portfolio_view": base_report["portfolio_view"],
        "key_actions": ai_report.get("key_actions") or base_report["key_actions"],
        "risk_warnings": ai_report.get("risk_warnings") or base_report["risk_warnings"],
        "recommendations": normalized_recs,
        "next_review": ai_report.get("next_review") or _txt(lang, "next_review"),
        "target_weight_owner": "deterministic_rules",
    })
    missing = set(base_by_ticker) - {item["ticker"] for item in normalized_recs}
    if missing:
        logger.warning("AI recommendation response missed tickers: %s", sorted(missing))
    return result


def _view_label(view: str, lang: str) -> str:
    labels = {
        "zh": {
            "constructive": "积极",
            "balanced": "均衡",
            "cautious": "谨慎",
            "defensive": "防御",
        },
        "en": {
            "constructive": "constructive",
            "balanced": "balanced",
            "cautious": "cautious",
            "defensive": "defensive",
        },
    }
    return labels.get(lang, labels["en"]).get(view, view)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _txt(lang: str, key: str) -> str:
    zh = {
        "no_data": "暂无组合数据，请先刷新或导入持仓。",
        "no_ai": "未配置千问 API，当前使用本地评分规则生成建议。",
        "ai_failed": "AI 推荐暂不可用，已回退到本地评分规则。",
        "empty": "当前没有可分析的持仓。",
        "next_review": "建议在下一次完整数据刷新或重大市场波动后复核。",
        "prediction_reason": "预测市场持仓波动和事件风险高，建议控制仓位。",
        "prediction_weight_risk": "Polymarket 权重偏高，单一事件风险可能影响组合。",
        "profit_lock": "已有较大浮盈，可考虑锁定部分收益。",
        "weak_score": "综合评分 {score:.0f}/100 偏弱，继续持有需要更强理由。",
        "strong_score": "综合评分 {score:.0f}/100 较强，且当前权重仍有提升空间。",
        "quality_hold": "综合评分 {score:.0f}/100 较稳，当前更适合持有观察。",
        "needs_review": "综合评分 {score:.0f}/100 未达加仓区间，建议复核基本面与技术面。",
        "single_position_risk": "单一持仓权重 {weight:.1f}% 偏高。",
        "sector_risk": "{sector} 行业权重 {weight:.1f}% 偏高，不建议继续加仓。",
        "drawdown_risk": "当前回撤 {pnl:.1f}% 较深，需要确认是否为基本面恶化。",
        "balanced_hold": "综合评分 {score:.0f}/100 处于中性区间，维持当前仓位更稳妥。",
        "standard_risk": "主要风险来自市场波动、估值变化和数据不完整。",
        "sector_warning": "{sector} 行业权重 {weight:.1f}% 偏高。",
        "position_warning": "{ticker} 单一持仓权重 {weight:.1f}% 偏高。",
        "action_add": "{ticker}: 可考虑小幅加仓",
        "action_trim": "{ticker}: 可考虑适度降权",
        "action_reduce": "{ticker}: 建议减仓或退出",
        "action_review": "{ticker}: 需要重点复核",
        "no_major_action": "暂无强制调仓动作，优先保持纪律和分散度。",
        "summary": "组合评分 {score}/100，整体观点为{view}。当前识别到 {add} 个加仓候选和 {reduce} 个降权/减仓候选。",
    }
    en = {
        "no_data": "No portfolio data available. Refresh or import holdings first.",
        "no_ai": "Qwen API is not configured. Using local score-based recommendations.",
        "ai_failed": "AI recommendations are unavailable, falling back to local scoring rules.",
        "empty": "There are no holdings to analyze.",
        "next_review": "Review again after the next full data refresh or a major market move.",
        "prediction_reason": "Prediction-market positions carry high event risk and should stay size-controlled.",
        "prediction_weight_risk": "Polymarket weight is elevated; single-event risk may affect the portfolio.",
        "profit_lock": "The position has a large unrealized gain; partial profit-taking can be considered.",
        "weak_score": "Score {score:.0f}/100 is weak; continued ownership needs a stronger thesis.",
        "strong_score": "Score {score:.0f}/100 is strong and the current weight still has room to rise.",
        "quality_hold": "Score {score:.0f}/100 is solid; holding and monitoring is appropriate.",
        "needs_review": "Score {score:.0f}/100 is below add territory; review fundamentals and technicals.",
        "single_position_risk": "Single-position weight of {weight:.1f}% is elevated.",
        "sector_risk": "{sector} sector weight of {weight:.1f}% is elevated; avoid adding more.",
        "drawdown_risk": "Drawdown of {pnl:.1f}% is deep; verify whether fundamentals deteriorated.",
        "balanced_hold": "Score {score:.0f}/100 is neutral; maintaining the current weight is prudent.",
        "standard_risk": "Main risks are market volatility, valuation changes and incomplete data.",
        "sector_warning": "{sector} sector weight is elevated at {weight:.1f}%.",
        "position_warning": "{ticker} position weight is elevated at {weight:.1f}%.",
        "action_add": "{ticker}: consider a small add",
        "action_trim": "{ticker}: consider trimming weight",
        "action_reduce": "{ticker}: reduce or exit",
        "action_review": "{ticker}: review closely",
        "no_major_action": "No urgent rebalance action; prioritize discipline and diversification.",
        "summary": "Portfolio score is {score}/100 with a {view} stance. Found {add} add candidate(s) and {reduce} trim/reduce candidate(s).",
    }
    table = zh if lang == "zh" else en
    return table.get(key, en.get(key, key))
