"""PortfolioPilot - Shadow Portfolio Agent API-Routes.

REST-Endpunkte fuer den autonomen Shadow-Portfolio-Agent:
  GET  /api/shadow-portfolio          - Aktueller Portfolio-Stand
  POST /api/shadow-portfolio/run      - Agent-Zyklus ausfuehren
  GET  /api/shadow-portfolio/transactions  - Transaktionshistorie
  GET  /api/shadow-portfolio/performance   - Performance-Verlauf
  GET  /api/shadow-portfolio/decision-log  - AI-Entscheidungslog
  POST /api/shadow-portfolio/reset    - Portfolio zuruecksetzen
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/shadow-portfolio")
async def get_shadow_portfolio():
    """Aktueller Shadow-Portfolio-Stand mit Positionen, Performance und Metadaten."""
    try:
        from services.shadow_agent import get_shadow_portfolio_summary
        return get_shadow_portfolio_summary()
    except Exception as e:
        logger.error(f"Shadow Portfolio API Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/shadow-portfolio/run")
async def run_shadow_agent():
    """Loest einen Shadow-Agent-Zyklus manuell aus.

    Der Agent analysiert das Portfolio, trifft Entscheidungen und fuehrt
    Trades aus. Dauert 30-90 Sekunden (Gemini API + yFinance).
    """
    try:
        from services.shadow_agent import run_shadow_agent_cycle
        result = await run_shadow_agent_cycle()
        return result
    except Exception as e:
        logger.error(f"Shadow Agent Run Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/shadow-portfolio/transactions")
async def get_shadow_transactions(limit: int = 50):
    """Gibt die Transaktionshistorie des Shadow-Portfolios zurueck."""
    try:
        from database import shadow_get_transactions
        return shadow_get_transactions(limit=min(limit, 200))
    except Exception as e:
        logger.error(f"Shadow Transactions API Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/shadow-portfolio/performance")
async def get_shadow_performance(days: int = 90):
    """Gibt den Shadow-Performance-Verlauf zurueck (fuer Chart).

    Beinhaltet taeglich: total_value, cash, invested, pnl, real_portfolio_value.
    """
    try:
        from database import shadow_get_performance
        return shadow_get_performance(days=days)
    except Exception as e:
        logger.error(f"Shadow Performance API Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/shadow-portfolio/decision-log")
async def get_shadow_decision_log(limit: int = 20):
    """Gibt den AI-Entscheidungslog zurueck (letzte Zyklen)."""
    try:
        from database import shadow_get_decision_log
        return shadow_get_decision_log(limit=min(limit, 50))
    except Exception as e:
        logger.error(f"Shadow Decision Log API Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/shadow-portfolio/reset")
async def reset_shadow_portfolio():
    """Setzt das Shadow-Portfolio vollstaendig zurueck (inkl. aller Daten).

    Konfiguration (Agenten-Regeln) bleibt erhalten.
    Nach dem Reset wird beim naechsten Zyklus neu initialisiert.
    """
    try:
        from database import shadow_reset
        await __import__("asyncio").to_thread(shadow_reset)
        return {"status": "ok", "message": "Shadow-Portfolio wurde zurueckgesetzt. Naechster Agent-Lauf initialisiert neu."}
    except Exception as e:
        logger.error(f"Shadow Reset Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/shadow-portfolio/config")
async def get_shadow_config():
    """Gibt die aktuelle Agenten-Konfiguration zurueck."""
    try:
        from database import shadow_get_config, SHADOW_CONFIG_DEFAULTS
        config = shadow_get_config()
        return {
            "config": config,
            "defaults": SHADOW_CONFIG_DEFAULTS,
        }
    except Exception as e:
        logger.error(f"Shadow Config GET Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/shadow-portfolio/config")
async def save_shadow_config(payload: dict):
    """Speichert die Agenten-Konfiguration.

    Erwartet ein JSON-Objekt mit den zu aendernden Werten.
    Unbekannte Keys werden ignoriert, fehlende Keys behalten ihren aktuellen Wert.
    """
    try:
        from database import shadow_save_config, shadow_get_config
        shadow_save_config(payload)
        return {
            "status": "ok",
            "config": shadow_get_config(),
        }
    except Exception as e:
        logger.error(f"Shadow Config POST Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
