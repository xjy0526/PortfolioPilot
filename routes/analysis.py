"""PortfolioPilot - Analyse API-Routes.

Endpoints für die Portfolio-Analyse:
  POST /api/analysis/run     → Analyse starten (full/mid/light)
  GET  /api/analysis/latest  → Letzten Report holen
  GET  /api/analysis/history → Analyse-Historie
  GET  /api/analysis/trend/{ticker} → Score-Trend für einen Ticker
"""
import asyncio
import logging

from fastapi import APIRouter

from state import portfolio_data
from services.refresh import _refresh_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/analysis/run")
async def run_analysis(level: str = "full"):
    """Startet eine vollständige Portfolio-Analyse.

    Query-Parameter:
        level: "full" (alle Quellen neu), "mid" (Cache + yfinance), "light" (nur Neuberechnung)
    """
    if level not in ("full", "mid", "light"):
        return {"status": "error", "message": f"Unbekanntes Level: {level}. Erlaubt: full, mid, light"}

    if portfolio_data["refreshing"]:
        return {"status": "already_running", "message": "Analyse/Refresh läuft bereits..."}

    if level == "full":
        # Voller Refresh + Analyse-Report
        asyncio.create_task(_run_full_analysis())
        return {"status": "started", "message": "Vollständige Analyse gestartet (alle Datenquellen)..."}

    elif level == "mid":
        # Mid-Day: Nur yfinance + Fear&Greed + Neuberechnung
        asyncio.create_task(_run_mid_analysis())
        return {"status": "started", "message": "Mid-Day Analyse gestartet (Preis + Technical Update)..."}

    else:  # light
        # Nur Neuberechnung mit gecachten Daten
        report = await _run_light_analysis()
        if report:
            return {
                "status": "done",
                "report": report.model_dump(),
            }
        return {"status": "error", "message": "Keine Portfolio-Daten vorhanden. Bitte zuerst 'full' Analyse starten."}


@router.get("/api/analysis/latest")
async def get_latest_analysis():
    """Gibt den letzten Analyse-Report zurück."""
    # Demo-Modus: Report aus State oder generieren
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        report = portfolio_data.get("last_analysis")
        if report:
            return {"status": "ok", "report": report.model_dump()}
        # Fallback: Analyse-Historie
        from fetchers.demo_data import get_demo_analysis_history
        hist = get_demo_analysis_history(days=1)
        if hist:
            return {"status": "ok", "report": hist[-1]}
        return {"status": "no_data", "message": "Noch keine Analyse durchgeführt."}

    from engine.analysis import get_analysis_history

    history = get_analysis_history(days=7)
    if not history:
        return {"status": "no_data", "message": "Noch keine Analyse durchgeführt."}

    return {"status": "ok", "report": history[-1]}


@router.get("/api/analysis/history")
async def get_analysis_history_endpoint(days: int = 30):
    """Gibt die Analyse-Historie der letzten X Tage zurück."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_analysis_history
        history = get_demo_analysis_history(days=min(days, 7))
        return {"status": "ok", "count": len(history), "history": history}

    from engine.analysis import get_analysis_history

    history = get_analysis_history(days=days)
    return {
        "status": "ok",
        "count": len(history),
        "history": history,
    }


@router.get("/api/analysis/trend/{ticker}")
async def get_score_trend(ticker: str, days: int = 7):
    """Gibt den Score-Trend für einen einzelnen Ticker zurück."""
    from engine.analysis import get_score_trend as _get_trend

    trend = _get_trend(ticker.upper(), days=days)
    if not trend:
        return {"status": "no_data", "ticker": ticker, "message": "Keine Trend-Daten vorhanden."}

    return {
        "status": "ok",
        "ticker": ticker.upper(),
        "trend": trend,
        "current_score": trend[-1]["score"] if trend else None,
    }


async def _run_full_analysis():
    """Volle Analyse: Refresh aller Daten + Report generieren."""
    # _refresh_data erstellt bereits Scores → wir generieren danach den Report
    await _refresh_data()

    # Nach Refresh: Report generieren
    summary = portfolio_data.get("summary")
    if summary and summary.stocks:
        from engine.analysis import build_analysis_report
        report = build_analysis_report(
            stocks_with_scores=summary.stocks,
            analysis_level="full",
            total_portfolio_value=summary.total_value,
        )
        portfolio_data["last_analysis"] = report
        logger.info(f"📊 Full-Analyse abgeschlossen: Portfolio-Score {report.portfolio_score:.1f}")


async def _run_mid_analysis():
    """Mid-Day Analyse: yfinance-Daten + Technical Indicators + Neuberechnung."""
    from services.refresh import _quick_price_refresh
    from fetchers.technical import fetch_technical_indicators
    from fetchers.fear_greed import fetch_fear_greed_index
    from engine.scorer import calculate_score
    from engine.analysis import build_analysis_report

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        await _run_full_analysis()
        return

    try:
        portfolio_data["refreshing"] = True

        # 1. Preise updaten
        await _quick_price_refresh()

        # 2. Fear&Greed neu laden
        try:
            fear_greed = await fetch_fear_greed_index()
        except Exception:
            fear_greed = summary.fear_greed

        # 3. Technical Indicators + Scores neu berechnen
        import asyncio
        for stock in summary.stocks:
            if stock.position.ticker == "CASH":
                continue

            try:
                tech = await fetch_technical_indicators(stock.position.ticker)
                stock.technical = tech
                if tech and tech.rsi_14 is not None:
                    stock.data_sources.technical = True

                # Score neu berechnen
                stock.score = calculate_score(
                    ticker=stock.position.ticker,
                    name=stock.position.name,
                    fundamentals=stock.fundamentals,
                    analyst=stock.analyst,
                    current_price=stock.position.current_price,
                    fmp_rating=stock.fmp_rating,
                    yfinance_data=stock.yfinance,
                    fear_greed=fear_greed,
                    technical=tech,
                )
            except Exception as e:
                logger.debug(f"Mid-Analyse für {stock.position.ticker} teilweise fehlgeschlagen: {e}")

        # 4. Report generieren
        report = build_analysis_report(
            stocks_with_scores=summary.stocks,
            analysis_level="mid",
            total_portfolio_value=summary.total_value,
        )
        portfolio_data["last_analysis"] = report
        logger.info(f"📊 Mid-Analyse abgeschlossen: Portfolio-Score {report.portfolio_score:.1f}")

    except Exception as e:
        logger.error(f"Mid-Analyse fehlgeschlagen: {e}")
    finally:
        portfolio_data["refreshing"] = False


async def _run_light_analysis():
    """Light-Analyse: Nur Scores neu berechnen mit bestehenden Daten."""
    from engine.scorer import calculate_score
    from engine.analysis import build_analysis_report

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return None

    # Scores neu berechnen mit bestehenden gecachten Daten
    for stock in summary.stocks:
        if stock.position.ticker == "CASH" or not stock.score:
            continue

        stock.score = calculate_score(
            ticker=stock.position.ticker,
            name=stock.position.name,
            fundamentals=stock.fundamentals,
            analyst=stock.analyst,
            current_price=stock.position.current_price,
            fmp_rating=stock.fmp_rating,
            yfinance_data=stock.yfinance,
            fear_greed=summary.fear_greed,
            technical=stock.technical,
        )

    report = build_analysis_report(
        stocks_with_scores=summary.stocks,
        analysis_level="light",
        total_portfolio_value=summary.total_value,
    )
    portfolio_data["last_analysis"] = report
    return report


# ─────────────────────────────────────────────────────────────
# A1: Backtest-Endpoint
# ─────────────────────────────────────────────────────────────

@router.get("/api/backtest")
async def get_backtest(lookback_days: int = 30, forward_days: int = 14):
    """Score-Backtesting: Misst Prädiktivität der Scoring-Engine."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_backtest
        return get_demo_backtest()

    from engine.backtest import run_backtest
    return run_backtest(lookback_days=lookback_days, forward_days=forward_days)


# ─────────────────────────────────────────────────────────────
# A3: Sektor-Rotation-Endpoint
# ─────────────────────────────────────────────────────────────

@router.get("/api/sectors/rotation")
async def get_sector_rotation():
    """Sektor-Rotation-Analyse: Relative Sektor-Performance vs. S&P 500."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_sector_rotation
        return get_demo_sector_rotation()

    from engine.sector_rotation import get_sector_rotation

    # Portfolio-Sektoren extrahieren
    portfolio_sectors = None
    if summary and summary.stocks:
        portfolio_sectors = list(set(
            s.position.sector for s in summary.stocks
            if s.position.ticker != "CASH" and s.position.sector
        ))

    return await get_sector_rotation(portfolio_sectors)


# ─────────────────────────────────────────────────────────────
# AI Trade Advisor Endpoint
# ─────────────────────────────────────────────────────────────

@router.post("/api/advisor/evaluate")
async def evaluate_trade_endpoint(data: dict):
    """AI Trade Advisor: Evaluiert Kauf/Verkauf-Entscheidungen.

    Body:
        ticker: str — Aktienticker (z.B. "NVDA")
        action: str — "buy", "sell", "increase"
        amount_eur: float (optional) — Geplanter Betrag
        extra_context: str (optional) — Externe Quellen / Analystenkommentare
    """
    ticker = data.get("ticker", "").strip().upper()
    lang = data.get("lang", "zh")
    if not ticker:
        return {
            "error": "请输入一个代码（例如 NVDA、AAPL）"
            if lang == "zh" else "Please enter a ticker (e.g. NVDA, AAPL)"
        }

    action = data.get("action", "buy")
    amount_eur = data.get("amount_eur")
    extra_context = data.get("extra_context")

    if amount_eur:
        try:
            amount_eur = float(amount_eur)
        except (ValueError, TypeError):
            amount_eur = None

    from services.trade_advisor import evaluate_trade
    result = await evaluate_trade(
        ticker=ticker,
        action=action,
        amount_eur=amount_eur,
        extra_context=extra_context,
        lang=lang,
    )
    return result


@router.post("/api/advisor/chat")
async def advisor_chat_endpoint(data: dict):
    """AI Advisor Chat: Freie Konversation mit Portfolio-Kontext.

    Body:
        message: str — Frage oder Hypothese
        history: list (optional) — Bisheriger Chat-Verlauf
    """
    message = data.get("message", "").strip()
    lang = data.get("lang", "zh")
    if not message:
        return {
            "error": "请输入一条消息。"
            if lang == "zh" else "Please enter a message."
        }

    history = data.get("history", [])

    from services.trade_advisor import chat_with_advisor
    result = await chat_with_advisor(
        message=message,
        history=history,
        lang=lang,
    )
    return result


@router.get("/api/advisor/holding-recommendations")
async def holding_recommendations_endpoint(lang: str = "zh"):
    """AI Holding Advisor: Gibt Empfehlungen fuer alle aktuellen Positionen."""
    if lang not in ("zh", "en"):
        lang = "zh"

    from services.holding_recommendations import generate_holding_recommendations

    return await generate_holding_recommendations(lang=lang)
