"""PortfolioPilot - Shadow Portfolio Agent.

Autonomer AI-Agent der ein fiktives Portfolio verwaltet:
  1. PERCEPTION  - Liest echtes Portfolio + Marktdaten + Shadow-State
  2. REASONING   - Gemini 2.5 Pro entscheidet per Function Calling
  3. ACTION      - Fuehrt fiktive Trades in Shadow-DB aus
  4. REPORTING   - Performance tracking + Decision Log

Startkapital: Cash-Bestand des echten Portfolios (erste Initialisierung)
Initialisierung: Gespiegelt vom echten Portfolio
Modus: Vollautomatisch
Universum: Freies Universum (alle yfinance-Ticker)

Regeln fuer den Agenten:
  - Max. 20 Positionen
  - Max. 10% Gewichtung pro Position
  - Min. 5% Cash-Reserve
  - Kein Trade unter dem Mindestbetrag
  - Max. 3 Trades pro Zyklus
  - Sektor-Konzentration max. 35%
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from config import settings
from services.display_currency import format_display_money

logger = logging.getLogger(__name__)
TZ_BERLIN = ZoneInfo("Europe/Berlin")

# ── Agenten-Regeln ────────────────────────────────────────────
MAX_POSITIONS = 20
MAX_WEIGHT_PCT = 10.0       # Max 10% pro Position
MIN_CASH_PCT = 5.0          # Min 5% Cash-Reserve
MIN_TRADE_EUR = 500.0       # Minimum Trade-Volumen
MAX_TRADES_PER_CYCLE = 3    # Max Trades pro Zyklus
MAX_SECTOR_PCT = 35.0       # Max Sektor-Konzentration
MIN_BUY_SCORE = 60.0        # Mindest-Score fuer Kaeufe

# ── Gemini Structured Output Schema ──────────────────────────
SHADOW_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "trades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                    "amount_eur": {"type": "number"},
                    "reason": {"type": "string"},
                    "priority": {"type": "integer"},
                },
                "required": ["ticker", "action", "amount_eur", "reason", "priority"],
            },
        },
        "market_assessment": {"type": "string"},
        "portfolio_health": {"type": "string"},
        "next_focus": {"type": "string"},
    },
    "required": ["trades", "market_assessment", "portfolio_health", "next_focus"],
}


# ─────────────────────────────────────────────────────────────
# Haupt-Entry-Point
# ─────────────────────────────────────────────────────────────

async def run_shadow_agent_cycle() -> dict:
    """Fuehrt einen vollstaendigen Shadow-Agent-Zyklus aus.

    Returns:
        Dict mit Cycle-Report (trades, performance, ai_reasoning)
    """
    from state import portfolio_data

    logger.info("🤖 Shadow Agent: Zyklus startet...")

    if not settings.gemini_configured:
        return {"error": "Qwen nicht konfiguriert", "status": "skipped"}

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return {"error": "Keine Portfolio-Daten", "status": "skipped"}

    # 1. Initialisierung (falls erstes Mal)
    await _ensure_initialized(summary)

    # 2. Kurse der Shadow-Positionen aktualisieren
    await _update_shadow_prices()

    # 3. Perception: Kontext aufbauen
    context = _build_agent_context(summary)

    # 4. Kandidaten evaluieren (Score >= MIN_BUY_SCORE)
    candidates = await _get_top_candidates(summary)

    # 5. Reasoning: Gemini entscheidet
    decision = await _call_gemini_agent(context, candidates)

    if "error" in decision:
        logger.error(f"Shadow Agent: Gemini-Fehler: {decision['error']}")
        _save_cycle_report(decision.get("error", "Fehler"), 0, len(candidates), "", context)
        return {"error": decision["error"], "status": "error"}

    # 6. Action: Trades ausfuehren
    trades_done = await _execute_trades(decision.get("trades", []), summary)

    # 7. Performance speichern
    perf = _calculate_and_save_performance(summary)

    # 8. Decision Log
    market_assessment = decision.get("market_assessment", "")
    portfolio_health = decision.get("portfolio_health", "")
    next_focus = decision.get("next_focus", "")
    ai_reasoning = f"Marktlage: {market_assessment}\n\nPortfolio-Gesundheit: {portfolio_health}\n\nFokus: {next_focus}"

    _save_cycle_report(
        cycle_summary=f"{len(trades_done)} Trades ausgeführt, {len(candidates)} Kandidaten evaluiert",
        trades_executed=len(trades_done),
        candidates_evaluated=len(candidates),
        ai_reasoning=ai_reasoning,
        context=context,
    )

    logger.info(f"✅ Shadow Agent: Zyklus abgeschlossen — {len(trades_done)} Trades")

    return {
        "status": "done",
        "trades_executed": trades_done,
        "candidates_evaluated": len(candidates),
        "market_assessment": market_assessment,
        "portfolio_health": portfolio_health,
        "next_focus": next_focus,
        "performance": perf,
    }


# ─────────────────────────────────────────────────────────────
# Initialisierung
# ─────────────────────────────────────────────────────────────

async def _ensure_initialized(summary) -> bool:
    """Initialisiert das Shadow-Portfolio beim ersten Start.

    Spiegelt das echte Portfolio und nutzt den Cash-Bestand als Startkapital.
    """
    import asyncio
    from database import (
        shadow_get_meta, shadow_set_meta, shadow_set_cash,
        shadow_upsert_position, shadow_add_transaction,
    )

    if shadow_get_meta("initialized") == "true":
        return False  # Bereits initialisiert

    logger.info("🚀 Shadow Agent: Erste Initialisierung aus echtem Portfolio...")

    # Cash-Bestand aus echtem Portfolio
    cash = 0.0
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            cash = stock.position.current_price  # Cash hat den Wert als Preis
            break

    if cash <= 0:
        # Fallback: 5% des Portfolio-Werts
        cash = summary.total_value * 0.05
        logger.warning(f"Kein Cash-Bestand gefunden — nutze Fallback: {format_display_money(cash, summary)}")

    shadow_set_cash(cash)
    logger.info(f"💶 Shadow-Startkapital (Cash): {format_display_money(cash, summary)}")

    # Echte Portfolio-Positionen (ausser CASH) spiegeln
    count = 0
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            continue

        ticker = stock.position.ticker
        name = stock.position.name or ticker
        shares = stock.position.shares
        avg_cost_eur = stock.position.avg_cost
        sector = stock.position.sector or "Unknown"

        # Aktuellen internen Basispreis berechnen
        current_price_eur = stock.position.current_value / shares if shares > 0 else avg_cost_eur

        shadow_upsert_position(
            ticker=ticker,
            name=name,
            shares=shares,
            avg_cost_eur=avg_cost_eur,
            current_price_eur=current_price_eur,
            sector=sector,
        )
        shadow_add_transaction(
            action="init",
            ticker=ticker,
            name=name,
            shares=shares,
            price_eur=current_price_eur,
            total_eur=shares * current_price_eur,
            reason="Initialisierung: Gespiegelt vom echten Portfolio",
            score=stock.score.total_score if stock.score else None,
            confidence=stock.score.confidence if stock.score else None,
        )
        count += 1

    shadow_set_meta("initialized", "true")
    shadow_set_meta("init_date", datetime.now(tz=TZ_BERLIN).isoformat())
    shadow_set_meta("start_capital_eur", str(round(cash + summary.total_value - _get_cash_position_value(summary), 2)))

    logger.info(f"✅ Shadow-Portfolio initialisiert: {count} Positionen + {format_display_money(cash, summary)} Cash")
    return True


def _get_cash_position_value(summary) -> float:
    """Gibt den Wert der CASH-Position zurueck."""
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            return stock.position.current_price
    return 0.0


# ─────────────────────────────────────────────────────────────
# Preise aktualisieren
# ─────────────────────────────────────────────────────────────

async def _update_shadow_prices():
    """Aktualisiert die aktuellen Preise aller Shadow-Positionen via yFinance."""
    from database import shadow_get_positions, shadow_upsert_position
    from fetchers.yfinance_data import quick_price_update

    positions = shadow_get_positions()
    if not positions:
        return

    tickers = [p["ticker"] for p in positions]

    try:
        prices, _ = await quick_price_update(tickers)

        for pos in positions:
            ticker = pos["ticker"]
            if ticker in prices and prices[ticker] > 0:
                shadow_upsert_position(
                    ticker=ticker,
                    name=pos["name"],
                    shares=pos["shares"],
                    avg_cost_eur=pos["avg_cost_eur"],
                    current_price_eur=prices[ticker],
                    sector=pos["sector"],
                )
        logger.debug(f"Shadow-Preise aktualisiert: {len(prices)} Ticker")
    except Exception as e:
        logger.warning(f"Shadow-Preis-Update fehlgeschlagen: {e}")


# ─────────────────────────────────────────────────────────────
# Kontext aufbauen (Perception)
# ─────────────────────────────────────────────────────────────

def _build_agent_context(summary) -> dict:
    """Baut den vollstaendigen Agenten-Kontext auf."""
    from database import shadow_get_positions, shadow_get_cash, shadow_get_meta, shadow_get_config

    positions = shadow_get_positions()
    cash = shadow_get_cash()
    cfg = shadow_get_config()

    # Shadow-Portfolio-Wert berechnen
    invested_value = sum(p["shares"] * p["current_price_eur"] for p in positions)
    total_value = invested_value + cash

    # Start-Kapital
    start_capital = float(shadow_get_meta("start_capital_eur", str(total_value)))
    pnl = total_value - start_capital
    pnl_pct = (pnl / start_capital * 100) if start_capital > 0 else 0

    # Sektor-Verteilung
    sectors: dict[str, float] = {}
    for p in positions:
        sec = p.get("sector", "Unknown")
        sectors[sec] = sectors.get(sec, 0) + p["shares"] * p["current_price_eur"]
    sector_pcts = {k: round(v / total_value * 100, 1) for k, v in sectors.items()} if total_value > 0 else {}

    # Echtes Portfolio als Vergleich
    real_positions = []
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            continue
        real_positions.append({
            "ticker": stock.position.ticker,
            "score": stock.score.total_score if stock.score else None,
            "rating": stock.score.rating.value if stock.score else "hold",
            "pnl_pct": stock.position.pnl_percent,
            "sector": stock.position.sector,
        })

    return {
        "shadow": {
            "total_value": round(total_value, 2),
            "cash": round(cash, 2),
            "cash_pct": round(cash / total_value * 100, 1) if total_value > 0 else 0,
            "invested": round(invested_value, 2),
            "start_capital": round(start_capital, 2),
            "pnl_eur": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "num_positions": len(positions),
            "positions": [
                {
                    "ticker": p["ticker"],
                    "name": p["name"],
                    "shares": p["shares"],
                    "avg_cost_eur": p["avg_cost_eur"],
                    "current_price_eur": p["current_price_eur"],
                    "value_eur": round(p["shares"] * p["current_price_eur"], 2),
                    "weight_pct": round(p["shares"] * p["current_price_eur"] / total_value * 100, 1) if total_value > 0 else 0,
                    "pnl_pct": round((p["current_price_eur"] - p["avg_cost_eur"]) / p["avg_cost_eur"] * 100, 2) if p["avg_cost_eur"] > 0 else 0,
                    "sector": p["sector"],
                }
                for p in positions
            ],
            "sector_distribution": sector_pcts,
        },
        "market": {
            "fear_greed": summary.fear_greed.value if summary.fear_greed else 50,
            "fear_greed_label": summary.fear_greed.label if summary.fear_greed else "Neutral",
        },
        "real_portfolio": {
            "total_value": round(summary.total_value, 2),
            "pnl_pct": round(summary.total_pnl_percent, 2),
            "positions": real_positions[:10],
        },
        "rules": {
            "max_positions": cfg["max_positions"],
            "max_weight_pct": cfg["max_weight_pct"],
            "min_cash_pct": cfg["min_cash_pct"],
            "min_trade_eur": cfg["min_trade_eur"],
            "max_trades_per_cycle": cfg["max_trades_per_cycle"],
            "max_sector_pct": cfg["max_sector_pct"],
            "min_buy_score": cfg["min_buy_score"],
            "strategy_mode": cfg["strategy_mode"],
        },
    }



# ─────────────────────────────────────────────────────────────
# Kandidaten-Screening
# ─────────────────────────────────────────────────────────────

async def _get_top_candidates(summary) -> list[dict]:
    """Sammelt Kauf-/Verkaufs-Kandidaten aus echtem Portfolio + Tech-Radar."""
    candidates = []

    # 1. Aus echtem Portfolio (BUY + SELL geratete Aktien)
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            continue
        if not stock.score:
            continue
        score = stock.score.total_score
        rating = stock.score.rating.value
        candidates.append({
            "ticker": stock.position.ticker,
            "name": stock.position.name or stock.position.ticker,
            "score": round(score, 1),
            "rating": rating,
            "sector": stock.position.sector or "Unknown",
            "source": "real_portfolio",
            "current_price_eur": stock.position.current_value / stock.position.shares if stock.position.shares > 0 else 0,
        })

    # 2. Tech-Radar-Picks
    tech_picks = summary.tech_picks or []
    for pick in tech_picks[:10]:
        if any(c["ticker"] == pick.ticker for c in candidates):
            continue  # Duplikat vermeiden
        candidates.append({
            "ticker": pick.ticker,
            "name": pick.name or pick.ticker,
            "score": round(pick.score, 1),
            "rating": "buy" if pick.score >= 65 else "hold",
            "sector": pick.sector or "Technology",
            "source": "tech_radar",
            "current_price_eur": pick.current_price or 0,
        })

    # Sortieren: BUY > score
    candidates.sort(key=lambda x: (x["rating"] == "buy", x["score"]), reverse=True)
    return candidates[:20]  # Max 20 Kandidaten an Gemini


# ─────────────────────────────────────────────────────────────
# Gemini Reasoning (Function Calling)
# ─────────────────────────────────────────────────────────────

def _build_shadow_tool_declarations() -> list[dict]:
    """Definiert die Tools die Gemini fuer den Shadow-Agent aufrufen kann."""
    return [
        {
            "name": "get_stock_score",
            "description": (
                "Berechnet den aktuellen 10-Faktor-Score einer Aktie (0-100). "
                "Nutze dies fuer Aktien die NICHT im echten Portfolio sind und deren Score du benotigst."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Aktien-Ticker (z.B. NVDA)"},
                },
                "required": ["ticker"],
            },
        },
        {
            "name": "get_shadow_portfolio",
            "description": "Gibt den aktuellen Shadow-Portfolio-Stand zurueck (Positionen, Cash, Performance).",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "get_market_context",
            "description": "Gibt aktuellen Markt-Kontext zurueck: Fear & Greed Index, Sektor-Trends.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "get_sector_concentration",
            "description": "Prueft die Sektor-Konzentration des Shadow-Portfolios und zeigt Risiken.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


async def _execute_agent_tool_call(tool_name: str, tool_args: dict, context: dict, summary) -> str:
    """Fuehrt einen Tool-Aufruf des Shadow Agents aus."""
    if tool_name == "get_shadow_portfolio":
        shadow = context.get("shadow", {})
        return json.dumps({
            "total_value_eur": shadow.get("total_value"),
            "cash_eur": shadow.get("cash"),
            "cash_pct": shadow.get("cash_pct"),
            "pnl_eur": shadow.get("pnl_eur"),
            "pnl_pct": shadow.get("pnl_pct"),
            "num_positions": shadow.get("num_positions"),
            "positions": shadow.get("positions", [])[:15],
        }, default=str)

    elif tool_name == "get_market_context":
        market = context.get("market", {})
        return json.dumps(market, default=str)

    elif tool_name == "get_sector_concentration":
        shadow = context.get("shadow", {})
        sectors = shadow.get("sector_distribution", {})
        max_sec = context.get("rules", {}).get("max_sector_pct", 35.0)
        violations = [f"{s}: {v}% (Max {max_sec}%)" for s, v in sectors.items() if v > max_sec]
        return json.dumps({
            "sector_distribution": sectors,
            "violations": violations,
            "max_allowed_pct": max_sec,
        }, default=str)


    elif tool_name == "get_stock_score":
        ticker = tool_args.get("ticker", "").upper()
        if not ticker:
            return json.dumps({"error": "Kein Ticker angegeben"})
        try:
            # Pruefe erst eigenes Portfolio
            for stock in summary.stocks:
                if stock.position.ticker == ticker and stock.score:
                    s = stock.score
                    return json.dumps({
                        "ticker": ticker,
                        "score": s.total_score,
                        "rating": s.rating.value,
                        "confidence": s.confidence,
                        "source": "real_portfolio",
                    }, default=str)

            # Sonst: Live Score berechnen
            from services.data_loader import load_position_data
            from models import PortfolioPosition
            dummy = PortfolioPosition(ticker=ticker, name=ticker, shares=0, avg_cost=0, current_price=0)
            fear_greed = summary.fear_greed
            stock_data = await load_position_data(dummy, fear_greed)
            if stock_data.score:
                s = stock_data.score
                return json.dumps({
                    "ticker": ticker,
                    "score": s.total_score,
                    "rating": s.rating.value,
                    "confidence": s.confidence,
                    "name": stock_data.position.name,
                    "sector": stock_data.position.sector,
                    "source": "live_calculated",
                }, default=str)

            return json.dumps({"ticker": ticker, "error": "Score nicht berechenbar"})
        except Exception as e:
            return json.dumps({"ticker": ticker, "error": str(e)})

    return json.dumps({"error": f"Unbekanntes Tool: {tool_name}"})


async def _call_gemini_agent(context: dict, candidates: list[dict]) -> dict:
    """Ruft Gemini 2.5 Pro auf um Trade-Entscheidungen zu treffen."""
    from state import portfolio_data
    from services.vertex_ai import (
        Content,
        FunctionDeclaration,
        Part,
        Tool,
        get_cached_content,
        get_client,
    )

    client = get_client()
    summary = portfolio_data.get("summary")

    shadow = context["shadow"]
    market = context["market"]
    rules = context["rules"]
    money = lambda value, digits=2, signed=False: format_display_money(
        value,
        summary,
        digits=digits,
        signed=signed,
    )
    min_cash_value = shadow["total_value"] * rules["min_cash_pct"] / 100
    max_buy_cash = max(0, shadow["cash"] - min_cash_value)

    # System-Prompt
    strategy_hints = {
        "aggressive": "Sei risikofreudig: Kaufe aggressiv BUY-Aktien, halte weniger Cash, toleriere hoehere Gewichtungen.",
        "conservative": "Sei konservativ: Halte viel Cash, kaufe nur bei hohem Score (>70), fokussiere auf stabile Sektoren.",
        "balanced": "Sei ausgewogen: Mische Wachstum und Sicherheit, halte die Regeln strikt ein.",
    }
    strategy_hint = strategy_hints.get(rules.get("strategy_mode", "balanced"), strategy_hints["balanced"])

    system_prompt = (
        "Du bist ein autonomer AI-Portfolio-Agent fuer PortfolioPilot. "
        "Du verwaltest ein fiktives Shadow-Portfolio mit Paper-Money. "
        "Deine Aufgabe: Analyse das Portfolio taeglich und triff eigenstaendig Kauf/Verkauf-Entscheidungen.\n\n"
        f"SHADOW-PORTFOLIO STATUS:\n"
        f"  Gesamtwert: {money(shadow['total_value'])}\n"
        f"  Cash: {money(shadow['cash'])} ({shadow['cash_pct']}%)\n"
        f"  Investiert: {money(shadow['invested'])}\n"
        f"  P&L: {money(shadow['pnl_eur'], signed=True)} ({shadow['pnl_pct']:+.1f}%)\n"
        f"  Positionen: {shadow['num_positions']}/{rules['max_positions']}\n"
        f"  Fear & Greed: {market['fear_greed']}/100 ({market['fear_greed_label']})\n\n"
        f"STRATEGIE-MODUS: {rules.get('strategy_mode', 'balanced').upper()}\n"
        f"  {strategy_hint}\n\n"
        f"REGELN (ZWINGEND einhalten):\n"
        f"  - Max {rules['max_positions']} Positionen\n"
        f"  - Max {rules['max_weight_pct']}% Gewichtung pro Position\n"
        f"  - Min {rules['min_cash_pct']}% Cash-Reserve (= mind. {money(min_cash_value, digits=0)})\n"
        f"  - Minimum Trade-Volumen: {money(rules['min_trade_eur'], digits=0)}\n"
        f"  - Max {rules['max_trades_per_cycle']} Trades pro Zyklus\n"
        f"  - Max {rules['max_sector_pct']}% Sektor-Konzentration\n"
        f"  - Mindest-Score fuer Kaeufe: {rules['min_buy_score']}\n\n"
        "STRATEGIE:\n"
        f"  - Kaufe Aktien mit Score >= {rules['min_buy_score']} und Rating BUY\n"
        "  - Verkaufe Aktien mit Score < 40 (SELL-Rating) oder starker Uebergewichtung\n"
        "  - Nutze die verfuegbaren Tools um aktuelle Daten abzurufen\n"
        "  - Begruende jede Entscheidung praezise (Score, Sektor, Portfolio-Fit)\n"
        "  - Antworte auf Deutsch\n"
    )


    # User-Prompt mit Kandidaten
    candidates_text = "\n".join(
        f"  {c['ticker']} ({c['name']}): Score {c['score']:.0f}, Rating {c['rating']}, Sektor {c['sector']}, Quelle {c['source']}"
        for c in candidates[:15]
    )

    positions_text = "\n".join(
        f"  {p['ticker']}: {p['shares']:.4f} Stk. @ {money(p['avg_cost_eur'])}, "
        f"Wert {money(p['value_eur'])} ({p['weight_pct']:.1f}%), "
        f"P&L {p['pnl_pct']:+.1f}%, Sektor {p['sector']}"
        for p in shadow.get("positions", [])
    )

    user_prompt = (
        f"Fuehre den taeglichen Shadow-Portfolio-Zyklus durch.\n\n"
        f"AKTUELLE POSITIONEN:\n{positions_text or '  (Keine Positionen)'}\n\n"
        f"KANDIDATEN FUER TRADES:\n{candidates_text or '  (Keine Kandidaten)'}\n\n"
        "Nutze die verfuegbaren Tools um mehr Daten abzurufen, dann entscheide:\n"
        "1. Welche Positionen sollen verkauft werden? (Schlechter Score, Uebergewichtung, SELL-Signale)\n"
        "2. Welche neuen Positionen sollen gekauft werden? (BUY Score >= 60)\n"
        "3. Wie ist die allgemeine Marktlage zu bewerten?\n\n"
        f"Maximale Cash-Verfuegbar fuer Kaeufe: {money(max_buy_cash, digits=0)}\n"
        "Erstelle einen konkreten Trade-Plan als strukturiertes JSON-Objekt."
    )

    # Tool-Deklarationen
    tool_declarations = [FunctionDeclaration(**td) for td in _build_shadow_tool_declarations()]

    config = {
        "tools": [Tool(function_declarations=tool_declarations)],
        "system_instruction": system_prompt,
    }

    cached = get_cached_content()
    if cached:
        config["cached_content"] = cached

    try:
        # Initiale Anfrage
        contents = [Content(role="user", parts=[Part(text=user_prompt)])]
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=contents,
                config=config,
            ),
            timeout=120.0,
        )
        contents.append(response.candidates[0].content)

        # Function Calling Loop (max 3 Runden)
        for round_num in range(3):
            function_calls = [
                p for p in response.candidates[0].content.parts
                if p.function_call
            ]
            if not function_calls:
                break

            logger.info(f"🔧 Shadow Agent Tool-Runde {round_num + 1}: {len(function_calls)} Aufrufe")

            tool_results = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                result_str = await _execute_agent_tool_call(
                    fc.name, dict(fc.args) if fc.args else {}, context, summary
                )
                tool_results.append(Part.from_function_response(
                    name=fc.name,
                    response={"result": result_str},
                ))

            contents.append(Content(role="user", parts=tool_results))
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=contents,
                    config=config,
                ),
                timeout=120.0,
            )
            contents.append(response.candidates[0].content)

        # Antwort parsen
        raw = response.text.strip() if response.text else "{}"
        logger.info(f"🧠 Shadow Agent Decision ({len(raw)} Zeichen)")
        return _parse_decision(raw)

    except asyncio.TimeoutError:
        return {"error": "Gemini-Timeout (120s)"}
    except Exception as e:
        logger.error(f"Shadow Agent Gemini-Fehler: {e}")
        return {"error": str(e)}


def _parse_decision(raw: str) -> dict:
    """Parsed die Gemini-Entscheidung."""
    cleaned = raw
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
        if "trades" not in result:
            result["trades"] = []
        return result
    except json.JSONDecodeError:
        logger.warning("Shadow Agent: JSON-Parsing fehlgeschlagen")
        return {
            "trades": [],
            "market_assessment": raw[:300] if raw else "Keine Bewertung",
            "portfolio_health": "",
            "next_focus": "",
        }


# ─────────────────────────────────────────────────────────────
# Trade-Execution
# ─────────────────────────────────────────────────────────────

async def _execute_trades(trades: list[dict], summary) -> list[dict]:
    """Fuehrt die Shadow-Trades aus."""
    from database import (
        shadow_get_cash, shadow_set_cash,
        shadow_get_positions, shadow_upsert_position, shadow_remove_position,
        shadow_add_transaction, shadow_get_config,
    )

    if not trades:
        return []

    cfg = shadow_get_config()
    max_trades = cfg["max_trades_per_cycle"]
    min_trade_eur = cfg["min_trade_eur"]
    min_cash_pct = cfg["min_cash_pct"]
    max_positions = cfg["max_positions"]
    max_weight_pct = cfg["max_weight_pct"]

    # Nach Prioritaet sortieren (hoechste zuerst)
    trades_sorted = sorted(trades, key=lambda t: t.get("priority", 5), reverse=True)

    executed = []
    trades_count = 0

    for trade in trades_sorted:
        if trades_count >= max_trades:
            logger.info(f"Shadow Agent: Max Trades ({max_trades}) erreicht")
            break

        ticker = trade.get("ticker", "").upper().strip()
        action = trade.get("action", "hold").lower()
        amount_eur = float(trade.get("amount_eur", 0))
        reason = trade.get("reason", "")

        if action == "hold" or not ticker:
            continue

        if amount_eur < min_trade_eur:
            logger.debug(
                f"Shadow Agent: Trade {ticker} unter Mindestvolumen "
                f"({format_display_money(amount_eur, summary, digits=0)} < "
                f"{format_display_money(min_trade_eur, summary, digits=0)})"
            )
            continue

        # Aktuellen Preis ermitteln
        current_price_eur = await _get_current_price_eur(ticker, summary)
        if current_price_eur <= 0:
            logger.warning(f"Shadow Agent: Kein Preis fuer {ticker}")
            continue

        cash = shadow_get_cash()
        positions = {p["ticker"]: p for p in shadow_get_positions()}
        total_value = sum(p["shares"] * p["current_price_eur"] for p in positions.values()) + cash

        if action == "buy":
            # Cash-Check
            min_cash = total_value * min_cash_pct / 100
            available_cash = max(0, cash - min_cash)

            if available_cash < min_trade_eur:
                logger.info(
                    f"Shadow Agent: Nicht genug Cash fuer {ticker} "
                    f"(Verfuegbar: {format_display_money(available_cash, summary, digits=0)})"
                )
                continue

            # Positions-Anzahl-Check
            if len(positions) >= max_positions and ticker not in positions:
                logger.info(f"Shadow Agent: Max Positionen ({max_positions}) erreicht")
                continue

            # Gewichtungs-Check
            if total_value > 0:
                new_value = min(amount_eur, available_cash)
                new_position_value = (positions.get(ticker, {}).get("shares", 0) * current_price_eur) + new_value
                new_weight = new_position_value / (total_value) * 100
                if new_weight > max_weight_pct:
                    # Betrag anpassen
                    max_invest = total_value * max_weight_pct / 100 - positions.get(ticker, {}).get("shares", 0) * current_price_eur
                    amount_eur = max(0, min(amount_eur, max_invest))
                    if amount_eur < min_trade_eur:
                        logger.debug(f"Shadow Agent: Max Gewicht {max_weight_pct}% fuer {ticker}")
                        continue

            actual_amount = min(amount_eur, available_cash)
            shares_bought = actual_amount / current_price_eur

            # Position aktualisieren
            existing = positions.get(ticker, {})
            old_shares = existing.get("shares", 0)
            old_cost = existing.get("avg_cost_eur", current_price_eur)
            new_shares = old_shares + shares_bought
            new_avg_cost = ((old_shares * old_cost) + actual_amount) / new_shares if new_shares > 0 else current_price_eur

            # Metadaten holen
            name, sector = _get_stock_meta(ticker, summary)

            shadow_upsert_position(
                ticker=ticker,
                name=name,
                shares=new_shares,
                avg_cost_eur=new_avg_cost,
                current_price_eur=current_price_eur,
                sector=sector,
            )
            shadow_set_cash(cash - actual_amount)
            shadow_add_transaction(
                action="buy",
                ticker=ticker,
                name=name,
                shares=shares_bought,
                price_eur=current_price_eur,
                total_eur=actual_amount,
                reason=reason,
            )

            executed.append({
                "action": "buy",
                "ticker": ticker,
                "shares": round(shares_bought, 4),
                "price_eur": round(current_price_eur, 2),
                "total_eur": round(actual_amount, 2),
                "reason": reason,
            })
            trades_count += 1
            logger.info(
                f"✅ Shadow BUY: {ticker} — {shares_bought:.4f} Stk. @ "
                f"{format_display_money(current_price_eur, summary)} = "
                f"{format_display_money(actual_amount, summary)}"
            )

        elif action == "sell":
            if ticker not in positions:
                logger.debug(f"Shadow Agent: {ticker} nicht im Shadow-Portfolio")
                continue

            pos = positions[ticker]
            sell_shares = min(amount_eur / current_price_eur, pos["shares"])
            actual_proceeds = sell_shares * current_price_eur

            if actual_proceeds < MIN_TRADE_EUR:
                continue

            remaining_shares = pos["shares"] - sell_shares

            if remaining_shares < 0.0001:
                shadow_remove_position(ticker)
            else:
                shadow_upsert_position(
                    ticker=ticker,
                    name=pos["name"],
                    shares=remaining_shares,
                    avg_cost_eur=pos["avg_cost_eur"],
                    current_price_eur=current_price_eur,
                    sector=pos["sector"],
                )

            shadow_set_cash(cash + actual_proceeds)
            shadow_add_transaction(
                action="sell",
                ticker=ticker,
                name=pos["name"],
                shares=sell_shares,
                price_eur=current_price_eur,
                total_eur=actual_proceeds,
                reason=reason,
            )

            executed.append({
                "action": "sell",
                "ticker": ticker,
                "shares": round(sell_shares, 4),
                "price_eur": round(current_price_eur, 2),
                "total_eur": round(actual_proceeds, 2),
                "reason": reason,
            })
            trades_count += 1
            logger.info(
                f"✅ Shadow SELL: {ticker} — {sell_shares:.4f} Stk. @ "
                f"{format_display_money(current_price_eur, summary)} = "
                f"{format_display_money(actual_proceeds, summary)}"
            )

    return executed


async def _get_current_price_eur(ticker: str, summary) -> float:
    """Ermittelt den aktuellen internen Basispreis einer Aktie."""
    # 1. Aus Shadow-DB
    from database import shadow_get_positions
    for p in shadow_get_positions():
        if p["ticker"] == ticker and p["current_price_eur"] > 0:
            return p["current_price_eur"]

    # 2. Aus echtem Portfolio
    for stock in summary.stocks:
        if stock.position.ticker == ticker and stock.position.shares > 0:
            return stock.position.current_value / stock.position.shares

    # 3. Via yFinance
    try:
        from fetchers.yfinance_data import quick_price_update
        prices, _ = await quick_price_update([ticker])
        return prices.get(ticker, 0)
    except Exception:
        return 0.0


def _get_stock_meta(ticker: str, summary) -> tuple[str, str]:
    """Gibt Name und Sektor einer Aktie zurueck."""
    for stock in summary.stocks:
        if stock.position.ticker == ticker:
            return stock.position.name or ticker, stock.position.sector or "Unknown"
    for pick in (summary.tech_picks or []):
        if pick.ticker == ticker:
            return pick.name or ticker, pick.sector or "Technology"
    return ticker, "Unknown"


# ─────────────────────────────────────────────────────────────
# Performance & Reporting
# ─────────────────────────────────────────────────────────────

def _calculate_and_save_performance(summary) -> dict:
    """Berechnet und speichert den aktuellen Shadow-Performance-Snapshot."""
    from database import (
        shadow_get_positions, shadow_get_cash, shadow_get_meta,
        shadow_save_performance,
    )

    positions = shadow_get_positions()
    cash = shadow_get_cash()
    invested_value = sum(p["shares"] * p["current_price_eur"] for p in positions)
    total_value = invested_value + cash
    start_capital = float(shadow_get_meta("start_capital_eur", str(total_value)))

    pnl = total_value - start_capital
    pnl_pct = (pnl / start_capital * 100) if start_capital > 0 else 0

    shadow_save_performance(
        total_value_eur=total_value,
        cash_eur=cash,
        invested_eur=invested_value,
        pnl_eur=pnl,
        pnl_pct=pnl_pct,
        num_positions=len(positions),
        real_portfolio_value=summary.total_value,
    )

    return {
        "total_value_eur": round(total_value, 2),
        "cash_eur": round(cash, 2),
        "invested_eur": round(invested_value, 2),
        "pnl_eur": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "num_positions": len(positions),
    }


def _save_cycle_report(
    cycle_summary: str,
    trades_executed: int,
    candidates_evaluated: int,
    ai_reasoning: str,
    context: dict,
):
    """Speichert den Zyklus-Report."""
    from database import shadow_add_decision_log, shadow_get_cash

    shadow = context.get("shadow", {}) if isinstance(context, dict) else {}
    total_value = shadow.get("total_value", 0)
    cash = shadow.get("cash", shadow_get_cash())

    shadow_add_decision_log(
        cycle_summary=cycle_summary,
        trades_executed=trades_executed,
        candidates_evaluated=candidates_evaluated,
        ai_reasoning=ai_reasoning,
        total_value_eur=total_value,
        cash_eur=cash,
    )


# ─────────────────────────────────────────────────────────────
# Helper: Shadow-Portfolio-Uebersicht (fuer API)
# ─────────────────────────────────────────────────────────────

def get_shadow_portfolio_summary() -> dict:
    """Gibt eine vollstaendige Shadow-Portfolio-Uebersicht zurueck."""
    from database import (
        shadow_get_positions, shadow_get_cash, shadow_get_meta,
    )

    positions = shadow_get_positions()
    cash = shadow_get_cash()
    invested_value = sum(p["shares"] * p["current_price_eur"] for p in positions)
    total_value = invested_value + cash
    start_capital = float(shadow_get_meta("start_capital_eur", str(total_value)))
    pnl = total_value - start_capital
    pnl_pct = (pnl / start_capital * 100) if start_capital > 0 else 0

    # Positionen anreichern
    enriched_positions = []
    for p in sorted(positions, key=lambda x: x["shares"] * x["current_price_eur"], reverse=True):
        value = p["shares"] * p["current_price_eur"]
        pos_pnl = (p["current_price_eur"] - p["avg_cost_eur"]) / p["avg_cost_eur"] * 100 if p["avg_cost_eur"] > 0 else 0
        enriched_positions.append({
            **p,
            "value_eur": round(value, 2),
            "weight_pct": round(value / total_value * 100, 1) if total_value > 0 else 0,
            "pnl_pct": round(pos_pnl, 2),
            "pnl_eur": round(value - p["shares"] * p["avg_cost_eur"], 2),
        })

    # Sektor-Verteilung
    sectors: dict[str, float] = {}
    for p in positions:
        sec = p.get("sector", "Unknown")
        sectors[sec] = sectors.get(sec, 0) + p["shares"] * p["current_price_eur"]
    sector_pcts = {k: round(v / total_value * 100, 1) for k, v in sectors.items()} if total_value > 0 else {}

    return {
        "initialized": shadow_get_meta("initialized") == "true",
        "init_date": shadow_get_meta("init_date", ""),
        "start_capital_eur": round(start_capital, 2),
        "total_value_eur": round(total_value, 2),
        "cash_eur": round(cash, 2),
        "cash_pct": round(cash / total_value * 100, 1) if total_value > 0 else 0,
        "invested_eur": round(invested_value, 2),
        "pnl_eur": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "num_positions": len(positions),
        "positions": enriched_positions,
        "sector_distribution": sector_pcts,
    }
