"""PortfolioPilot - Demo Mode API-Routes.

Expliziter Demo-Toggle: Lädt/entfernt fiktives Portfolio für
externe Präsentationen — unabhängig von konfigurierten API-Keys.
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from state import portfolio_data, TZ_BERLIN
from models import PortfolioSummary, StockFullData, DataSourceStatus

from fetchers.demo_data import (
    get_demo_positions, get_demo_fundamentals,
    get_demo_analyst_data, get_demo_tech_picks,
    get_demo_fmp_ratings, get_demo_yfinance_data,
    get_demo_fear_greed, get_demo_technical_indicators,
    get_demo_portfolio_history,
)
from engine.scorer import calculate_score
from engine.rebalancer import calculate_rebalancing
from services.portfolio_builder import calc_portfolio_totals

logger = logging.getLogger(__name__)

router = APIRouter()


def build_demo_portfolio() -> PortfolioSummary:
    """Baut ein komplettes Demo-Portfolio aus statischen Daten.

    Kein API-Call nötig — alle Daten kommen aus fetchers/demo_data.py.
    Enthält: 12 Positionen mit Fundamentals, Analysten, Technicals,
    Scores, Rebalancing und Tech Picks.
    """
    positions = get_demo_positions()
    demo_fund = get_demo_fundamentals()
    demo_analyst = get_demo_analyst_data()
    demo_fmp = get_demo_fmp_ratings()
    demo_yf = get_demo_yfinance_data()
    demo_tech = get_demo_technical_indicators()
    fear_greed_data = get_demo_fear_greed()

    stocks = []
    scores_dict = {}

    for pos in positions:
        fund = demo_fund.get(pos.ticker)
        analyst = demo_analyst.get(pos.ticker)
        fmp_rat = demo_fmp.get(pos.ticker)
        yf = demo_yf.get(pos.ticker)
        tech = demo_tech.get(pos.ticker)

        score = calculate_score(
            ticker=pos.ticker,
            name=pos.name,
            fundamentals=fund,
            analyst=analyst,
            current_price=pos.current_price,
            fmp_rating=fmp_rat,
            yfinance_data=yf,
            fear_greed=fear_greed_data,
            technical=tech,
        )

        stocks.append(StockFullData(
            position=pos,
            fundamentals=fund,
            analyst=analyst,
            technical=tech,
            fmp_rating=fmp_rat,
            yfinance=yf,
            score=score,
            data_sources=DataSourceStatus(
                parqet=True, fmp=True, technical=True,
                yfinance=True, fear_greed=True,
            ),
        ))
        scores_dict[pos.ticker] = score

    # Rebalancing berechnen
    rebalancing = calculate_rebalancing(positions, scores_dict, stocks=stocks)

    # Tech Picks
    tech_picks = get_demo_tech_picks()

    # Portfolio-Totals
    t = calc_portfolio_totals(stocks)

    return PortfolioSummary(
        total_value=t["total_value"],
        total_cost=t["total_cost"],
        total_pnl=t["total_pnl"],
        total_pnl_percent=t["total_pnl_pct"],
        num_positions=len(stocks),
        stocks=stocks,
        scores=[s.score for s in stocks if s.score],
        rebalancing=rebalancing,
        tech_picks=tech_picks,
        fear_greed=fear_greed_data,
        is_demo=True,
        eur_usd_rate=1.08,
        eur_cny_rate=7.80,
        daily_total_change=t["daily_total_eur"],
        daily_total_change_pct=t["daily_total_pct"],
    )


@router.post("/api/demo/activate")
async def activate_demo():
    """Aktiviert den Demo-Modus mit fiktiven Portfolio-Daten."""
    try:
        summary = build_demo_portfolio()
        portfolio_data["summary"] = summary
        portfolio_data["last_refresh"] = datetime.now(tz=TZ_BERLIN)

        # Analyse-Report generieren (für Analyse-Tab)
        try:
            from engine.analysis import build_analysis_report
            report = build_analysis_report(
                stocks_with_scores=summary.stocks,
                analysis_level="full",
                total_portfolio_value=summary.total_value,
            )
            portfolio_data["last_analysis"] = report
            logger.info(f"📊 Demo-Analyse generiert: Score {report.portfolio_score:.1f}")
        except Exception as e:
            logger.warning(f"Demo-Analyse konnte nicht generiert werden: {e}")

        # Activities speichern (für Activities-Tab)
        try:
            from fetchers.demo_data import get_demo_activities
            portfolio_data["activities"] = get_demo_activities()
        except Exception:
            pass

        logger.info(
            f"🎭 Demo-Modus aktiviert: {summary.num_positions} Positionen, "
            f"Wert: ${summary.total_value * summary.eur_usd_rate:,.2f} USD"
        )
        return {
            "status": "ok",
            "message": f"Demo-Portfolio geladen: {summary.num_positions} Positionen",
            "num_positions": summary.num_positions,
            "total_value": round(summary.total_value, 2),
        }
    except Exception as e:
        logger.error(f"Demo-Aktivierung fehlgeschlagen: {e}")
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500,
        )


@router.post("/api/demo/deactivate")
async def deactivate_demo():
    """Deaktiviert den Demo-Modus und startet einen echten Refresh."""
    portfolio_data["summary"] = None
    logger.info("🎭 Demo-Modus deaktiviert — starte echten Refresh...")

    # Echten Refresh im Hintergrund starten
    from services.refresh import _refresh_data
    portfolio_data["refreshing"] = True
    asyncio.create_task(_refresh_data())

    return {
        "status": "ok",
        "message": "Demo deaktiviert — echte Daten werden geladen...",
    }


@router.get("/api/demo/status")
async def demo_status():
    """Gibt den aktuellen Demo-Status zurück."""
    summary = portfolio_data.get("summary")
    is_demo = summary.is_demo if summary else False
    return {
        "is_demo": is_demo,
        "num_positions": summary.num_positions if summary else 0,
    }
