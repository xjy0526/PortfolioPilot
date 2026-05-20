"""PortfolioPilot - Cloud Run Job Entry Point.

Standalone-Script für den täglichen Cloud Run Job:
  1. Voller Portfolio-Refresh (Parqet, FMP, yfinance, Technicals, Scoring)
  2. AI Finance Agent Report (Gemini Research + Telegram)

Wird von Cloud Scheduler täglich um 16:15 CET getriggert.
"""
import asyncio
import logging
import sys

# Logging konfigurieren (Cloud Run loggt nach stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("portfoliopilot.job")


async def run_job():
    """Führt den kompletten Job-Zyklus aus."""
    logger.info("🚀 PortfolioPilot Cloud Run Job gestartet")

    from state import portfolio_data

    # 1. Voller Portfolio-Refresh (lädt ALLE Finanzdaten)
    logger.info("📊 Starte Full-Refresh (Parqet, FMP, yfinance, Technicals, Scoring)...")
    try:
        from services.refresh import _do_refresh
        await _do_refresh()
        logger.info("✅ Full-Refresh abgeschlossen")
    except Exception as e:
        logger.error(f"❌ Full-Refresh fehlgeschlagen: {e}")
        import traceback
        traceback.print_exc()

    # 2. Prüfe ob Finanzdaten vollständig geladen wurden
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        logger.error("❌ Keine Portfolio-Daten nach Refresh — Job fehlgeschlagen")
        sys.exit(1)

    scored = [s for s in summary.stocks if s.score and s.position.ticker != "CASH"]
    logger.info(
        f"📊 Daten geladen: {len(summary.stocks)} Positionen, "
        f"{len(scored)} mit Score, Wert: €{summary.total_value:,.2f}"
    )

    if not scored:
        logger.error("❌ Keine Scores berechnet — Job fehlgeschlagen")
        sys.exit(1)

    # Telegram-Report wird automatisch in _do_refresh() nach der Analyse getriggert.
    # Kein separater Aufruf nötig (verhindert Doppel-Versand).

    # 3. Weekly Digest (nur Freitags — nach Börsenschluss)
    from datetime import datetime as _dt
    import zoneinfo
    _now = _dt.now(zoneinfo.ZoneInfo("Europe/Berlin"))
    if _now.weekday() == 4:  # 4 = Freitag
        logger.info("📧 Freitag erkannt — sende Weekly Digest...")
        try:
            from services.weekly_digest import send_weekly_digest
            await send_weekly_digest()
        except Exception as e:
            logger.warning(f"Weekly Digest fehlgeschlagen: {e}")
    else:
        logger.info(f"📧 Weekly Digest übersprungen (nur Freitags, heute: {_now.strftime('%A')})")

    logger.info("🏁 PortfolioPilot Cloud Run Job beendet")


if __name__ == "__main__":
    asyncio.run(run_job())
