"""PortfolioPilot - AI Trade Advisor v2.

Evaluiert Kauf/Verkauf/Aufstocken-Entscheidungen mit:
  - Gemini 2.5 Pro (AI-Analyse + Google Search Grounding)
  - Function Calling: Gemini ruft selbst Score/Portfolio/Market-Tools auf
  - Structured Output: Garantiert valides JSON-Response
  - Optionale externe Quellen (Analysten, Artikel, User-Notizen)

v2 Änderungen:
  Feature 1: Structured Output (response_schema)
  Feature 2: Function Calling (Gemini entscheidet welche Daten benötigt werden)
"""
import json
import logging
from typing import Optional

from config import settings
from services.display_currency import format_display_money

logger = logging.getLogger(__name__)


def _normalize_lang(lang: str | None) -> str:
    return "en" if lang == "en" else "zh"


def _advisor_text(lang: str | None, zh: str, en: str) -> str:
    return en if _normalize_lang(lang) == "en" else zh


async def evaluate_trade(
    ticker: str,
    action: str = "buy",
    amount_eur: Optional[float] = None,
    extra_context: Optional[str] = None,
    lang: str = "zh",
) -> dict:
    """Evaluiert eine Trade-Entscheidung mit AI + Function Calling.

    Args:
        ticker: Aktien-Ticker (z.B. "NVDA", "AAPL")
        action: "buy" (Neukauf), "sell" (Verkauf), "increase" (Aufstocken)
        amount_eur: Geplanter Betrag in EUR (optional)
        extra_context: Zusätzliche Informationen vom User (Analysten, Artikel)

    Returns:
        dict mit AI-Bewertung, Score, Portfolio-Impact, Risiken
    """
    from state import portfolio_data
    lang = _normalize_lang(lang)

    if not settings.gemini_configured:
        return {
            "error": _advisor_text(
                lang,
                "Qwen/千问 API 未配置，请设置 QWEN_API_KEY。",
                "Qwen API is not configured. Please set QWEN_API_KEY.",
            ),
            "recommendation": "unknown",
        }

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return {
            "error": _advisor_text(
                lang,
                "暂无组合数据，请先刷新或导入持仓。",
                "No portfolio data yet. Please refresh or import holdings first.",
            ),
            "recommendation": "unknown",
        }

    ticker = ticker.upper().strip()
    action = action.lower().strip()
    if action not in ("buy", "sell", "increase"):
        action = "buy"

    # Feature 2: Pre-compute data that tools will return
    score_info = await _get_or_calculate_score(ticker, summary)
    portfolio_ctx = _build_portfolio_context(summary, ticker, action, amount_eur)

    try:
        result = await _call_gemini_with_tools(
            ticker=ticker,
            action=action,
            amount_eur=amount_eur,
            score_info=score_info,
            portfolio_ctx=portfolio_ctx,
            extra_context=extra_context,
            lang=lang,
        )
        result["ticker"] = ticker
        result["action"] = action
        result["amount_eur"] = amount_eur
        result["score"] = score_info
        result["portfolio_context"] = portfolio_ctx
        return result
    except Exception as e:
        logger.error(f"Trade Advisor Fehler: {e}")
        return {
            "error": str(e),
            "ticker": ticker,
            "action": action,
            "score": score_info,
            "portfolio_context": portfolio_ctx,
            "recommendation": "unknown",
        }


# ─────────────────────────────────────────────────────────────
# Score-Berechnung
# ─────────────────────────────────────────────────────────────

async def _get_or_calculate_score(ticker: str, summary) -> dict:
    """Holt Score aus Portfolio-Cache oder berechnet ihn live."""
    for stock in summary.stocks:
        if stock.position.ticker == ticker and stock.score:
            s = stock.score
            return {
                "total_score": s.total_score,
                "rating": s.rating.value,
                "confidence": s.confidence,
                "in_portfolio": True,
                "current_weight": round(
                    stock.position.current_value / summary.total_value * 100, 1
                ) if summary.total_value > 0 else 0,
                "current_pnl_pct": stock.position.pnl_percent,
                "breakdown": {
                    "quality": s.breakdown.quality_score,
                    "valuation": s.breakdown.valuation_score,
                    "analyst": s.breakdown.analyst_score,
                    "technical": s.breakdown.technical_score,
                    "momentum": s.breakdown.momentum_score,
                    "sentiment": s.breakdown.sentiment_score,
                },
            }

    # Nicht im Portfolio → Live-Score berechnen
    try:
        from services.data_loader import load_position_data
        from models import PortfolioPosition
        from fetchers.fear_greed import fetch_fear_greed_index

        fear_greed = summary.fear_greed
        if not fear_greed:
            try:
                fear_greed = await fetch_fear_greed_index()
            except Exception:
                pass

        dummy_pos = PortfolioPosition(
            ticker=ticker,
            name=ticker,
            shares=0,
            avg_cost=0,
            current_price=0,
        )
        stock_data = await load_position_data(dummy_pos, fear_greed)

        if stock_data.score:
            s = stock_data.score
            return {
                "total_score": s.total_score,
                "rating": s.rating.value,
                "confidence": s.confidence,
                "in_portfolio": False,
                "current_weight": 0,
                "current_pnl_pct": 0,
                "name": stock_data.position.name,
                "sector": stock_data.position.sector,
                "breakdown": {
                    "quality": s.breakdown.quality_score,
                    "valuation": s.breakdown.valuation_score,
                    "analyst": s.breakdown.analyst_score,
                    "technical": s.breakdown.technical_score,
                    "momentum": s.breakdown.momentum_score,
                    "sentiment": s.breakdown.sentiment_score,
                },
            }
    except Exception as e:
        logger.warning(f"Live-Score für {ticker} fehlgeschlagen: {e}")

    return {
        "total_score": None,
        "rating": "unknown",
        "in_portfolio": False,
        "confidence": 0,
        "current_weight": 0,
    }


# ─────────────────────────────────────────────────────────────
# Portfolio-Kontext
# ─────────────────────────────────────────────────────────────

def _build_portfolio_context(summary, ticker: str, action: str, amount_eur: Optional[float]) -> dict:
    """Baut Portfolio-Kontext für die AI-Analyse."""
    total = summary.total_value or 1
    stocks = [s for s in summary.stocks if s.position.ticker != "CASH"]

    sectors = {}
    for s in stocks:
        sec = s.position.sector or "Unknown"
        sectors[sec] = sectors.get(sec, 0) + s.position.current_value
    sector_pcts = {k: round(v / total * 100, 1) for k, v in sectors.items()}

    positions = []
    for s in sorted(stocks, key=lambda x: x.position.current_value, reverse=True)[:10]:
        positions.append({
            "ticker": s.position.ticker,
            "name": s.position.name,
            "weight": round(s.position.current_value / total * 100, 1),
            "score": s.score.total_score if s.score else None,
            "rating": s.score.rating.value if s.score else "unknown",
            "pnl_pct": s.position.pnl_percent,
            "sector": s.position.sector,
        })

    impact = {}
    if amount_eur and amount_eur > 0:
        target_ticker_sector = None
        for s in summary.stocks:
            if s.position.ticker == ticker:
                target_ticker_sector = s.position.sector
                break
        new_total = total + amount_eur if action != "sell" else total - amount_eur
        if new_total > 0 and target_ticker_sector:
            old_sector_pct = sector_pcts.get(target_ticker_sector, 0)
            sector_value = sectors.get(target_ticker_sector, 0)
            new_sector_value = sector_value - amount_eur if action == "sell" else sector_value + amount_eur
            new_sector_pct = round(new_sector_value / new_total * 100, 1)
            impact = {
                "sector": target_ticker_sector,
                "sector_weight_before": old_sector_pct,
                "sector_weight_after": new_sector_pct,
                "portfolio_value_after": round(new_total, 2),
            }

    return {
        "total_value": round(total, 2),
        "eur_usd_rate": getattr(summary, "eur_usd_rate", 1.08),
        "eur_cny_rate": getattr(summary, "eur_cny_rate", 7.8),
        "num_positions": len(stocks),
        "total_pnl_pct": summary.total_pnl_percent,
        "fear_greed": summary.fear_greed.value if summary.fear_greed else None,
        "fear_greed_label": summary.fear_greed.label if summary.fear_greed else None,
        "sector_distribution": sector_pcts,
        "top_positions": positions,
        "impact": impact,
    }


# ─────────────────────────────────────────────────────────────
# Feature 2: Function Calling — Tool Definitionen
# ─────────────────────────────────────────────────────────────

def _build_tool_declarations() -> list[dict]:
    """Definiert die Tools die Gemini aufrufen kann."""
    return [
        {
            "name": "get_stock_score",
            "description": (
                "Berechnet den 10-Faktor-Score einer Aktie. "
                "Gibt Score (0-100), Rating (buy/hold/sell), Confidence und Score-Breakdown zurück."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Aktien-Ticker (z.B. NVDA, AAPL)",
                    },
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_portfolio_overview",
            "description": (
                "Gibt eine Übersicht des aktuellen Portfolios: Gesamtwert, Positionen, "
                "Sektor-Verteilung, P&L, Fear&Greed Index."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "get_sector_impact",
            "description": (
                "Simuliert den Impact eines Trades auf die Sektor-Verteilung des Portfolios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "sell", "increase"]},
                    "amount_eur": {"type": "number"},
                },
                "required": ["ticker", "action", "amount_eur"],
            },
        },
        {
            "name": "fetch_url_content",
            "description": (
                "Ruft den Inhalt einer externen URL ab und gibt den Text zurück. "
                "Nutze dies um Artikel, Analysen, Berichte oder andere Webseiten zu lesen, "
                "die der User verlinkt oder die für die Analyse relevant sind."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Die vollständige URL (z.B. https://finance.yahoo.com/...)",
                    },
                },
                "required": ["url"],
            },
        },
    ]


async def _execute_tool_call(
    tool_name: str,
    tool_args: dict,
    score_info: dict,
    portfolio_ctx: dict,
) -> str:
    """Führt einen Tool-Aufruf aus und gibt das Ergebnis als JSON-String zurück."""
    if tool_name == "get_stock_score":
        return json.dumps(score_info, default=str)

    elif tool_name == "get_portfolio_overview":
        # Slim version: nur die wichtigsten Felder
        overview = {
            "total_value": portfolio_ctx["total_value"],
            "num_positions": portfolio_ctx["num_positions"],
            "total_pnl_pct": portfolio_ctx["total_pnl_pct"],
            "fear_greed": portfolio_ctx.get("fear_greed"),
            "fear_greed_label": portfolio_ctx.get("fear_greed_label"),
            "sector_distribution": portfolio_ctx["sector_distribution"],
            "top_positions": portfolio_ctx["top_positions"][:5],
        }
        return json.dumps(overview, default=str)

    elif tool_name == "get_sector_impact":
        impact = portfolio_ctx.get("impact", {})
        if not impact:
            return json.dumps({"info": "Keine Impact-Daten verfügbar (Betrag oder Sektor fehlt)"})
        return json.dumps(impact, default=str)

    elif tool_name == "fetch_url_content":
        url = tool_args.get("url", "")
        if not url:
            return json.dumps({"error": "Keine URL angegeben"})
        from services.url_fetcher import fetch_url_text
        text = await fetch_url_text(url, max_chars=6000)
        return json.dumps({"url": url, "content": text}, default=str)

    return json.dumps({"error": f"Unbekanntes Tool: {tool_name}"})


# ─────────────────────────────────────────────────────────────
# Feature 1: Structured Output Schema
# ─────────────────────────────────────────────────────────────

ADVISOR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "string",
            "enum": ["buy", "hold", "reduce", "avoid"],
            "description": "Trade recommendation",
        },
        "confidence": {
            "type": "integer",
            "description": "Confidence 0-100",
        },
        "summary": {
            "type": "string",
            "description": "1-2 sentence conclusion in the language specified by the system prompt",
        },
        "bull_case": {
            "type": "string",
            "description": "Arguments in favor of the trade",
        },
        "bear_case": {
            "type": "string",
            "description": "Arguments against the trade",
        },
        "portfolio_fit": {
            "type": "string",
            "description": "How does the trade fit the portfolio?",
        },
        "sizing_advice": {
            "type": "string",
            "description": "Recommended position size",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of key risks",
        },
        "timing": {
            "type": "string",
            "description": "Timing assessment",
        },
        "external_analysis": {
            "type": "string",
            "description": "Summary of external sources",
        },
    },
    "required": ["recommendation", "confidence", "summary", "bull_case",
                 "bear_case", "portfolio_fit", "sizing_advice", "risks", "timing"],
}


# ─────────────────────────────────────────────────────────────
# Gemini API Call mit Function Calling + Structured Output
# ─────────────────────────────────────────────────────────────

async def _call_gemini_with_tools(
    ticker: str,
    action: str,
    amount_eur: Optional[float],
    score_info: dict,
    portfolio_ctx: dict,
    extra_context: Optional[str],
    lang: str = "zh",
) -> dict:
    """Ruft Gemini 2.5 Pro mit Function Calling + Structured Output auf.

    Flow:
    1. Sende Prompt + Tool-Definitionen an Gemini
    2. Gemini entscheidet welche Tools es braucht
    3. Wir führen die Tools aus und senden Ergebnisse zurück
    4. Gemini erstellt die finale Bewertung (Structured Output)
    """
    from services.vertex_ai import (
        Content,
        FunctionDeclaration,
        Part,
        Tool,
        get_cached_content,
        get_client,
    )

    client = get_client()

    # System-Prompt (language-aware)
    lang = _normalize_lang(lang)
    action_labels = {
        "zh": {"buy": "买入", "sell": "卖出", "increase": "加仓"},
        "en": {"buy": "Buy", "sell": "Sell", "increase": "Add to Position"},
    }
    action_label = action_labels.get(lang, action_labels["zh"]).get(action, action)

    if lang == "en":
        system_prompt = (
            "You are an experienced portfolio advisor. The user wants to evaluate a trade. "
            "You have access to tools to retrieve portfolio data and stock scores. "
            "You can also read external URLs with the fetch_url_content tool — "
            "use it when the user shares links or when you want to read articles. "
            "Respond in English."
        )
    else:
        system_prompt = (
            "你是一名经验丰富的投资组合顾问。用户希望评估一笔交易。"
            "你可以使用工具获取组合数据和股票评分。"
            "你也可以用 fetch_url_content 工具读取外部 URL，"
            "当用户分享链接或需要阅读文章时使用。"
            "请用中文回答。"
        )

    # User-Prompt
    if lang == "en":
        user_prompt_parts = [
            f"Evaluate the following trade: {action_label.upper()} {ticker}",
        ]
        if amount_eur:
            user_prompt_parts.append(f"Planned amount: {format_display_money(amount_eur, portfolio_ctx, 'USD', digits=0)}")
        if extra_context:
            user_prompt_parts.append(f"\nExternal sources from user:\n{extra_context.strip()[:3000]}")
        user_prompt_parts.append(
            "\nUse the available tools to query the score and portfolio, "
            "then create a professional trade evaluation."
        )
    else:
        user_prompt_parts = [
            f"评估以下交易: {action_label} {ticker}",
        ]
        if amount_eur:
            user_prompt_parts.append(f"计划金额: {format_display_money(amount_eur, portfolio_ctx, 'USD', digits=0)}")
        if extra_context:
            user_prompt_parts.append(f"\n用户提供的外部信息:\n{extra_context.strip()[:3000]}")
        user_prompt_parts.append(
            "\n请使用可用工具查询评分和组合情况，然后给出专业的交易评估。"
        )
    user_prompt = "\n".join(user_prompt_parts)

    # Tool-Definitionen
    tool_declarations = [
        FunctionDeclaration(**td) for td in _build_tool_declarations()
    ]

    # Config: eigene Tools (GoogleSearch kann nicht mit function_declarations kombiniert werden)
    config = {
        "tools": [
            Tool(function_declarations=tool_declarations),
        ],
        "response_mime_type": "application/json",
        "response_schema": ADVISOR_RESPONSE_SCHEMA,
        "system_instruction": system_prompt,
    }

    cached = get_cached_content()
    if cached:
        config["cached_content"] = cached

    # Schritt 1: Initiale Anfrage
    response = await client.aio.models.generate_content(
        model="gemini-2.5-pro",
        contents=user_prompt,
        config=config,
    )

    # Schritt 2: Function Calling Loop (max 3 Runden)
    contents = [
        Content(role="user", parts=[Part(text=user_prompt)]),
        response.candidates[0].content,
    ]

    for round_num in range(3):
        # Prüfe ob Gemini Tools aufrufen will
        function_calls = []
        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_calls.append(part)

        if not function_calls:
            break  # Gemini ist fertig → finale Antwort

        logger.info(f"🔧 Function Calling Runde {round_num + 1}: "
                     f"{len(function_calls)} Tool-Aufrufe")

        # Alle Tool-Aufrufe ausführen
        tool_results = []
        for fc_part in function_calls:
            fc = fc_part.function_call
            tool_name = fc.name
            tool_args = dict(fc.args) if fc.args else {}

            logger.debug(f"  Tool: {tool_name}({tool_args})")
            result_str = await _execute_tool_call(
                tool_name, tool_args, score_info, portfolio_ctx,
            )

            tool_results.append(Part.from_function_response(
                name=tool_name,
                response={"result": result_str},
            ))

        # Tool-Ergebnisse zurücksenden
        contents.append(Content(role="user", parts=tool_results))

        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
            config=config,
        )
        contents.append(response.candidates[0].content)

    # Finale Antwort parsen
    raw = response.text.strip() if response.text else ""
    logger.info(f"🧠 Trade Advisor Response ({len(raw)} Zeichen, "
                f"nach {round_num + 1 if function_calls else 0} Tool-Runden)")

    return _parse_ai_response(raw)


def _parse_ai_response(raw: str) -> dict:
    """Parsed die Gemini-Antwort zu strukturiertem Dict.

    Mit Structured Output (Feature 1) sollte dies immer valides JSON sein.
    Fallback für Edge-Cases wird beibehalten.
    """
    cleaned = raw
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
        defaults = {
            "recommendation": "hold",
            "confidence": 50,
            "summary": "",
            "bull_case": "",
            "bear_case": "",
            "portfolio_fit": "",
            "sizing_advice": "",
            "risks": [],
            "timing": "",
            "external_analysis": "",
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default
        return result
    except json.JSONDecodeError:
        logger.warning(f"JSON-Parsing fehlgeschlagen, verwende Freitext")
        return {
            "recommendation": "hold",
            "confidence": 50,
            "summary": raw[:500],
            "bull_case": "",
            "bear_case": "",
            "portfolio_fit": "",
            "sizing_advice": "",
            "risks": [],
            "timing": "",
            "external_analysis": "",
            "raw_response": raw,
        }


# ─────────────────────────────────────────────────────────────
# Chat Advisor — Freie Konversation mit Portfolio-Kontext
# ─────────────────────────────────────────────────────────────

async def chat_with_advisor(
    message: str,
    history: list[dict] | None = None,
    lang: str = "zh",
) -> dict:
    """Freie Konversation mit dem AI Advisor.

    Unterstützt beliebige Fragen/Hypothesen mit Portfolio-Kontext.
    Nutzt Function Calling für Live-Daten und Google Search für Aktualität.

    Args:
        message: Aktuelle Nachricht des Users
        history: Bisheriger Chat-Verlauf [{"role": "user"|"assistant", "content": "..."}]

    Returns:
        dict mit "response" (AI-Antwort), "history" (aktualisiert)
    """
    from state import portfolio_data
    lang = _normalize_lang(lang)

    if not settings.gemini_configured:
        return {
            "response": _advisor_text(
                lang,
                "⚠️ Qwen/千问 API 未配置，请设置 QWEN_API_KEY。",
                "⚠️ Qwen API is not configured. Please set QWEN_API_KEY.",
            ),
            "history": history or [],
        }

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return {
            "response": _advisor_text(
                lang,
                "⚠️ 暂无组合数据，请先刷新或导入持仓。",
                "⚠️ No portfolio data yet. Please refresh or import holdings first.",
            ),
            "history": history or [],
        }

    if not message or not message.strip():
        return {
            "response": _advisor_text(
                lang,
                "请输入一个问题，或描述你的投资假设。",
                "Please ask a question or describe your hypothesis.",
            ),
            "history": history or [],
        }

    # Portfolio-Kontext für System-Prompt aufbauen
    portfolio_ctx = _build_portfolio_context(summary, "", "buy", None)

    # Score-Info für Tool-Calls (leerer Dummy — wird live berechnet)
    score_info = {"info": "Nutze get_stock_score Tool für Ticker-spezifische Scores"}

    try:
        response_text = await _call_gemini_chat(
            message=message.strip(),
            history=history or [],
            portfolio_ctx=portfolio_ctx,
            score_info=score_info,
            summary=summary,
            lang=lang,
        )
        # History aktualisieren
        updated_history = list(history or [])
        updated_history.append({"role": "user", "content": message.strip()})
        updated_history.append({"role": "assistant", "content": response_text})

        # History auf max 20 Nachrichten begrenzen (10 Turns)
        if len(updated_history) > 20:
            updated_history = updated_history[-20:]

        return {
            "response": response_text,
            "history": updated_history,
        }
    except Exception as e:
        logger.error(f"Chat Advisor Fehler: {e}")
        return {
            "response": _advisor_text(
                lang,
                f"❌ AI 分析失败: {str(e)}",
                f"❌ AI analysis failed: {str(e)}",
            ),
            "history": history or [],
        }


async def _call_gemini_chat(
    message: str,
    history: list[dict],
    portfolio_ctx: dict,
    score_info: dict,
    summary,
    lang: str = "zh",
) -> str:
    """Gemini-Call für freie Chat-Konversation mit Function Calling."""
    import asyncio
    from services.vertex_ai import (
        Content,
        FunctionDeclaration,
        Part,
        Tool,
        get_cached_content,
        get_client,
    )

    client = get_client()
    lang = _normalize_lang(lang)

    # Portfolio-Zusammenfassung für System-Prompt
    positions_text = ""
    for p in portfolio_ctx.get("top_positions", [])[:15]:
        if lang == "en":
            positions_text += (
                f"  {p['ticker']} ({p['name']}): "
                f"Weight {p['weight']}%, Score {p.get('score', '?')}, "
                f"Rating {p.get('rating', '?')}, P&L {p.get('pnl_pct', 0):.1f}%\n"
            )
        else:
            positions_text += (
                f"  {p['ticker']} ({p['name']}): "
                f"权重 {p['weight']}%, 评分 {p.get('score', '?')}, "
                f"评级 {p.get('rating', '?')}, 盈亏 {p.get('pnl_pct', 0):.1f}%\n"
            )

    sectors_text = ", ".join(
        f"{k} {v}%" for k, v in portfolio_ctx.get("sector_distribution", {}).items()
    )

    if lang == "en":
        system_prompt = (
            "You are an experienced portfolio advisor and financial analyst. "
            "The user has a personal stock portfolio and wants to discuss it with you.\n\n"
            f"PORTFOLIO OVERVIEW:\n"
            f"  Total Value: {format_display_money(portfolio_ctx.get('total_value', 0), portfolio_ctx, 'USD', digits=0)}\n"
            f"  Positions: {portfolio_ctx.get('num_positions', 0)}\n"
            f"  Total P&L: {portfolio_ctx.get('total_pnl_pct', 0):.1f}%\n"
            f"  Fear & Greed: {portfolio_ctx.get('fear_greed', '?')} ({portfolio_ctx.get('fear_greed_label', '?')})\n"
            f"  Sector Distribution: {sectors_text}\n\n"
            f"POSITIONS:\n{positions_text}\n"
            "RULES:\n"
            "- Respond in English, clearly and directly\n"
            "- Use available tools to retrieve current data when needed\n"
            "- If the user shares URLs, use the fetch_url_content tool to read the content\n"
            "- Reference the portfolio when relevant\n"
            "- Be honest about uncertainties and risks\n"
            "- Format with Markdown (bold, lists, headings)\n"
        )
    else:
        system_prompt = (
            "你是一名经验丰富的投资组合顾问和金融分析师。"
            "用户有一个个人股票组合，并希望与你讨论。\n\n"
            f"组合概览:\n"
            f"  总市值: {format_display_money(portfolio_ctx.get('total_value', 0), portfolio_ctx, 'USD', digits=0)}\n"
            f"  持仓数: {portfolio_ctx.get('num_positions', 0)}\n"
            f"  总盈亏: {portfolio_ctx.get('total_pnl_pct', 0):.1f}%\n"
            f"  Fear & Greed: {portfolio_ctx.get('fear_greed', '?')} ({portfolio_ctx.get('fear_greed_label', '?')})\n"
            f"  行业分布: {sectors_text}\n\n"
            f"持仓:\n{positions_text}\n"
            "规则:\n"
            "- 使用中文，清晰直接\n"
            "- 需要时使用可用工具获取最新数据\n"
            "- 如果用户分享 URL，使用 fetch_url_content 工具读取内容\n"
            "- 相关时结合用户组合回答\n"
            "- 对不确定性和风险保持诚实\n"
            "- 使用 Markdown 格式（加粗、列表、标题）\n"
        )

    # Tool-Definitionen (gleiche wie bei evaluate)
    tool_declarations = [
        FunctionDeclaration(**td) for td in _build_tool_declarations()
    ]

    config = {
        "tools": [
            Tool(function_declarations=tool_declarations),
        ],
        "system_instruction": system_prompt,
    }

    cached = get_cached_content()
    if cached:
        config["cached_content"] = cached

    # Chat-History als Contents aufbauen
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(Content(role=role, parts=[Part(text=msg["content"])]))

    # Aktuelle Nachricht hinzufügen
    contents.append(Content(role="user", parts=[Part(text=message)]))

    # Gemini-Call (async um Event-Loop nicht zu blockieren)
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
            config=config,
        ),
        timeout=90.0,
    )

    # Function Calling Loop (max 3 Runden)
    all_contents = list(contents)
    all_contents.append(response.candidates[0].content)

    for round_num in range(3):
        function_calls = [
            p for p in response.candidates[0].content.parts
            if p.function_call
        ]
        if not function_calls:
            break

        logger.info(f"💬 Chat Tool-Runde {round_num + 1}: {len(function_calls)} Calls")

        # Tool-Calls ausführen
        tool_results = []
        for fc_part in function_calls:
            fc = fc_part.function_call
            tool_name = fc.name
            tool_args = dict(fc.args) if fc.args else {}

            # Für get_stock_score: Live-Score berechnen
            if tool_name == "get_stock_score":
                ticker = tool_args.get("ticker", "")
                live_score = await _get_or_calculate_score(ticker, summary)
                result_str = json.dumps(live_score, default=str)
            else:
                result_str = await _execute_tool_call(
                    tool_name, tool_args, score_info, portfolio_ctx,
                )

            tool_results.append(Part.from_function_response(
                name=tool_name,
                response={"result": result_str},
            ))

        all_contents.append(Content(role="user", parts=tool_results))

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=all_contents,
                config=config,
            ),
            timeout=90.0,
        )
        all_contents.append(response.candidates[0].content)

    return response.text.strip() if response.text else _advisor_text(lang, "未收到回答。", "No response received.")
