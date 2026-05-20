"""PortfolioPilot - FastAPI Backend

Hauptserver: App-Erstellung, Lifespan-Management und Router-Einbindung.
Die gesamte Geschäftslogik lebt in services/ und routes/.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from config import settings
from cache_manager import CacheManager
from state import portfolio_data
from logging_config import setup_logging

from services.refresh import _refresh_data, _quick_price_refresh, _update_parqet
from routes.portfolio import router as portfolio_router
from routes.refresh import router as refresh_router
from routes.streaming import router as streaming_router
from routes.analysis import router as analysis_router
from routes.analytics import router as analytics_router
from routes.telegram import router as telegram_router
from routes.parqet_oauth import router as parqet_oauth_router
from routes.demo import router as demo_router
from routes.shadow_portfolio import router as shadow_portfolio_router
from routes.research import router as research_router

# Structured Logging (JSON in production, colored console in dev)
setup_logging(settings.ENVIRONMENT)
logger = logging.getLogger(__name__)


def subscribe_portfolio_tickers():
    """Subscribt Portfolio-Ticker beim yFinance WebSocket-Streamer.

    Kann jederzeit aufgerufen werden — z.B. nach Startup oder Token-Renewal.
    """
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        logger.warning("Streamer-Subscribe: Keine Portfolio-Positionen vorhanden")
        return

    tickers = [s.position.ticker for s in summary.stocks]
    logger.info(f"Subscribing {len(tickers)} Ticker bei yFinance WebSocket")

    # yfinance WS: Alle Ticker (US + Nicht-US)
    try:
        from fetchers.yfinance_ws import get_yf_streamer
        streamer = get_yf_streamer()
        streamer.subscribe(tickers)
    except Exception:
        pass



async def reload_portfolio_and_subscribe():
    """Lädt Portfolio von Parqet neu und subscribt Ticker bei WebSocket-Streamern.

    Wird nach erfolgreicher Token-Erneuerung (OAuth Callback) aufgerufen.
    """
    try:
        await _update_parqet()
        # yFinance Kurse laden
        try:
            from services.portfolio_builder import update_yfinance_prices
            await update_yfinance_prices()
        except Exception as e:
            logger.warning(f"yFinance nach Token-Renewal fehlgeschlagen: {e}")

        # Ticker bei Streamern subscriben
        subscribe_portfolio_tickers()
        logger.info("Portfolio nach Token-Renewal neu geladen und Streamer subscribed")
    except Exception as e:
        logger.error(f"Portfolio-Reload nach Token-Renewal fehlgeschlagen: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup/shutdown."""
    logger.info("\U0001f680 PortfolioPilot startet...")
    logger.info(f"   Environment: {settings.ENVIRONMENT}")
    logger.info(f"   Port: {settings.SERVER_PORT}")
    logger.info(f"   Demo-Mode: {settings.demo_mode}")


    # Volatile Caches beim Start löschen (Technicals)
    # Parqet-Positionen, Wechselkurse und Fear&Greed bleiben erhalten
    CacheManager.clear_volatile_caches()

    # Verwaiste Dateien aus JSON→SQLite Migration aufräumen
    CacheManager.cleanup_stale_files()

    # JSON → SQLite Migration (einmalig, idempotent)
    try:
        from database import migrate_json_to_sqlite
        # Ausführen im Thread-Pool, da dies synchron den Event-Loop blockieren kann
        await asyncio.to_thread(migrate_json_to_sqlite)
    except Exception as e:
        logger.debug(f"JSON-Migration übersprungen: {e}")

    # Fast startup: Parqet positions first, then yFinance prices
    _startup_done = asyncio.Event()

    async def _startup_load():
        try:
            await _update_parqet()
            # yFinance Kurse + Daily Changes separat laden (unabhängig von FMP)
            try:
                from services.portfolio_builder import update_yfinance_prices
                result = await update_yfinance_prices()
                logger.info(f"📈 yFinance-Startup: {result}")
            except Exception as e:
                logger.warning(f"yFinance-Startup fehlgeschlagen: {e}")
        finally:
            _startup_done.set()

    asyncio.create_task(_startup_load())

    # Verzögerter Full-Refresh: Lädt FMP, yFinance, Technical Daten im Hintergrund
    # → Wartet auf _startup_load, dann Full-Refresh
    # → User sieht nach ~90s vollständige Daten in der Detail-Ansicht
    async def _delayed_full_refresh():
        try:
            # Warte auf Parqet/yFinance-Init (180s für Cloud Run Cold Start + Token-Refresh)
            await asyncio.wait_for(_startup_done.wait(), timeout=180.0)
            await asyncio.sleep(10)  # Kurze Pause nach Startup
        except asyncio.TimeoutError:
            logger.warning("⚠️ Startup dauerte >180s — starte Full-Refresh trotzdem")
            # Lock hart zurücksetzen, falls er hängt
            from state import refresh_lock
            if refresh_lock.locked():
                refresh_lock.release()
            await asyncio.sleep(30)  # Warte noch etwas vor dem Retry

        try:
            logger.info("🔄 Auto-Refresh: Lade FMP/yFinance/Technical Daten...")
            await _refresh_data()
            logger.info("✅ Auto-Refresh abgeschlossen")
        except Exception as e:
            logger.warning(f"Auto-Refresh fehlgeschlagen: {e}")

    asyncio.create_task(_delayed_full_refresh())


    # Start yfinance WebSocket (kein API-Key nötig)
    yf_streamer = None
    try:
        from fetchers.yfinance_ws import get_yf_streamer
        yf_streamer = get_yf_streamer()
        await yf_streamer.start()
        logger.info("yfinance WebSocket gestartet")
    except Exception as e:
        logger.warning(f"yfinance WS-Start fehlgeschlagen: {type(e).__name__}: {e}")

    # Subscribe portfolio tickers after Parqet loads
    async def _subscribe_streamers():
        """Warte auf Portfolio-Daten, dann Ticker bei Streamern abonnieren."""
        try:
            # Warte auf Parqet-Load statt fixer 15s — max 60s Timeout
            await asyncio.wait_for(_startup_done.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("Streamer-Subscribe: Timeout beim Warten auf Portfolio-Daten")
            return

        subscribe_portfolio_tickers()

    if yf_streamer:
        asyncio.create_task(_subscribe_streamers())

    # Schedule: Einzige geplante Analyse um 16:15 CET
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()

        scheduler.add_job(
            _refresh_data, "cron",
            hour=16, minute=15,
            day_of_week="mon-fri",
            id="daily_analysis",
        )
        logger.info("\U0001f4ca Vollständige Analyse geplant um 16:15 CET (Mo-Fr)")

        # Weekly Digest (Sonntag 18:00 CET)
        async def _run_weekly_digest():
            try:
                from services.weekly_digest import send_weekly_digest
                await send_weekly_digest()
            except Exception as e:
                logger.warning(f"Weekly Digest fehlgeschlagen: {e}")

        scheduler.add_job(
            _run_weekly_digest, "cron",
            day_of_week="fri", hour=22, minute=30,
            id="weekly_digest",
        )
        logger.info("📧 Wöchentlicher Digest geplant: Freitag 22:30 CET (nach US-Börsenschluss)")

        # News-Kurator: Proaktive Portfolio-Alerts (alle 4h, Mo-Fr)
        async def _run_news_kurator():
            try:
                from services.news_kurator import check_portfolio_news
                await check_portfolio_news()
            except Exception as e:
                logger.debug(f"News-Kurator Check fehlgeschlagen: {e}")

        scheduler.add_job(
            _run_news_kurator, "cron",
            hour="9,13,17,21",
            day_of_week="mon-fri",
            id="news_kurator",
        )
        logger.info("📡 News-Kurator geplant: Mo-Fr um 09, 13, 17, 21 Uhr CET")

        # AI Finance Agent wird automatisch nach jeder Analyse in _do_refresh() getriggert
        if settings.telegram_configured:
            logger.info("🤖 AI Finance Agent: Wird nach Analyse automatisch getriggert (Telegram-Report)")
        else:
            logger.info("🤖 AI Finance Agent übersprungen (Telegram nicht konfiguriert)")

        # Shadow Portfolio Agent: Autonomer Zyklus taeglich Mo-Fr 17:00 CET
        async def _run_shadow_agent():
            try:
                from services.shadow_agent import run_shadow_agent_cycle
                result = await run_shadow_agent_cycle()
                logger.info(
                    f"🤖 Shadow Agent: Zyklus done — "
                    f"{result.get('trades_executed', []).__len__()} Trades"
                )
            except Exception as e:
                logger.warning(f"Shadow Agent Zyklus fehlgeschlagen: {e}")

        if settings.gemini_configured:
            scheduler.add_job(
                _run_shadow_agent, "cron",
                hour=17, minute=0,
                day_of_week="mon-fri",
                id="shadow_agent",
            )
            logger.info("🤖 Shadow Portfolio Agent geplant: Mo-Fr 17:00 CET")

        # Telegram Webhook registrieren (wenn auf Cloud Run)
        if settings.telegram_configured and settings.ENVIRONMENT == "production":
            async def _register_webhook():
                """Registriert den Telegram-Webhook bei App-Start."""
                await asyncio.sleep(5)  # Warte bis Server ready
                try:
                    import httpx
                    import os
                    # Cloud Run URL: Explizit gesetzt oder via K_SERVICE + Projekt-Nummer
                    service_url = os.getenv("CLOUD_RUN_URL", "").rstrip("/")
                    if not service_url:
                        k_service = os.getenv("K_SERVICE", "")
                        k_region = os.getenv("CLOUD_RUN_REGION", settings.GCP_LOCATION)
                        project_number = os.getenv("GOOGLE_CLOUD_PROJECT_NUMBER", "")
                        if k_service and project_number:
                            service_url = f"https://{k_service}-{project_number}.{k_region}.run.app"
                    
                    if service_url:
                        secret = settings.TELEGRAM_WEBHOOK_SECRET
                        webhook_url = f"{service_url}/api/telegram/webhook/{secret}"
                        api_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/setWebhook"
                        async with httpx.AsyncClient(timeout=10) as client:
                            r = await client.post(api_url, json={"url": webhook_url})
                            if r.status_code == 200 and r.json().get("ok"):
                                logger.info(f"🔗 Telegram-Webhook registriert: {webhook_url}")
                            else:
                                logger.warning(f"Telegram-Webhook fehlgeschlagen: {r.text}")
                    else:
                        logger.info("🔗 Telegram-Webhook: Keine Cloud Run URL — lokal Polling nutzen")
                except Exception as e:
                    logger.warning(f"Telegram-Webhook-Registrierung fehlgeschlagen: {e}")
            asyncio.create_task(_register_webhook())

        # Intraday Kurs-Updates (alle 15min während Marktzeiten, 0 FMP-Calls)
        # Nutzt die zentrale update_yfinance_prices() Funktion die:
        # - EUR-Konvertierung korrekt durchführt
        # - Daily Changes setzt
        # - Summary-Totals aktualisiert
        async def _intraday_price_update():
            """Aktualisiert Kurse + Daily Changes via update_yfinance_prices()."""
            try:
                from services.portfolio_builder import update_yfinance_prices
                result = await update_yfinance_prices()
                if result.get("status") == "done":
                    logger.info(
                        f"📈 Intraday-Update: {result.get('prices_updated', 0)} Kurse, "
                        f"{result.get('daily_changes', 0)} Daily Changes"
                    )
            except Exception as e:
                logger.debug(f"Intraday-Update fehlgeschlagen: {e}")

        scheduler.add_job(
            _intraday_price_update, "cron",
            minute=f"*/{settings.PRICE_UPDATE_INTERVAL_MIN}",
            hour="8-22",  # Nur während Marktzeiten (CET)
            day_of_week="mon-fri",  # Nur Werktage
            id="intraday_prices",
        )
        logger.info(f"📈 Intraday Kurs-Updates alle {settings.PRICE_UPDATE_INTERVAL_MIN}min (Mo-Fr 08-22 Uhr)")

        scheduler.start()
    except Exception as e:
        logger.warning(f"Scheduler konnte nicht gestartet werden: {e}")

    yield

    # Shutdown: WebSocket sauber schließen (mit Timeout gegen Deadlocks)
    try:
        if yf_streamer:
            await asyncio.wait_for(yf_streamer.stop(), timeout=3.0)
    except Exception as e:
        logger.debug(f"yFinance WS Shutdown ignoriert: {e}")
        
    logger.info("PortfolioPilot beendet.")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Aktienportfolio Dashboard & Advisor",
    version="1.0.0",
    lifespan=lifespan,
)

# GZip-Kompression für alle Responses (>500 Bytes)
app.add_middleware(GZipMiddleware, minimum_size=500)

# Passwortschutz (Basic Auth) — nur aktiv wenn DASHBOARD_USER/PASSWORD gesetzt
if settings.auth_configured:
    from middleware.auth import BasicAuthMiddleware
    app.add_middleware(BasicAuthMiddleware)
    logger.info(f"🔒 Dashboard-Passwortschutz aktiv (User: {settings.DASHBOARD_USER})")

# Static files mit Cache-Control Headers (1 Stunde)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# Include routers
app.include_router(portfolio_router)
app.include_router(refresh_router)
app.include_router(streaming_router)
app.include_router(analysis_router)
app.include_router(analytics_router)
app.include_router(telegram_router)
app.include_router(parqet_oauth_router)
app.include_router(demo_router)
app.include_router(shadow_portfolio_router)
app.include_router(research_router)


# Health Check (für Cloud Run Startup/Liveness Probes)
@app.get("/health")
async def health():
    """Sofortige Antwort — unabhängig vom Datenladestand."""
    return {"status": "ok"}

if __name__ == "__main__":
    import os
    import subprocess
    import uvicorn

    def _kill_port_occupants(port: int) -> None:
        """Killt alle Prozesse die den Port bereits belegen (inkl. Kind-Prozesse).

        Verhindert Whitescreen durch Zombie-Server-Instanzen die sich
        über die Zeit ansammeln (z.B. durch Ctrl+C das nicht sauber
        terminiert, IDE-Restarts, oder Agent-Sessions).

        Verwendet taskkill /T um den gesamten Prozessbaum zu killen,
        da der WatchFiles-Reloader Kind-Prozesse spawnt die sonst
        als Zombies übrig bleiben.
        """
        my_pid = os.getpid()
        killed = []

        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                local_addr = parts[1] if len(parts) >= 2 else ""
                if not local_addr.endswith(f":{port}"):
                    continue
                # Alle Verbindungsstatus (LISTENING, ESTABLISHED, TIME_WAIT)
                try:
                    pid = int(parts[-1])
                    if pid != my_pid and pid > 0 and pid not in killed:
                        # /T = Tree-Kill: Killt den Prozess UND alle Kinder
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=3,
                        )
                        killed.append(pid)
                except (ValueError, subprocess.TimeoutExpired):
                    pass
        except Exception:
            pass

        if killed:
            import time
            time.sleep(1)  # Etwas länger warten für Tree-Kill
            print(f"\033[1m🧹 {len(killed)} alte Server-Prozesse auf Port {port} beendet (inkl. Kinder): {killed}\033[0m")

    is_dev = settings.ENVIRONMENT == "development"

    # Alte Zombie-Prozesse auf dem Port killen bevor wir starten
    _kill_port_occupants(settings.SERVER_PORT)

    uvicorn.run(
        "main:app",
        host="127.0.0.1" if is_dev else settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        # Reload bleibt aktiv in dev: Nach dem Full-Refresh blockiert ein
        # Background-Task (yfinance WS / tech_radar_ai) den Event-Loop.
        # Der Reload-Neustart hebt die Blockade auf.
        # _kill_port_occupants() verhindert Zombie-Prozesse.
        reload=is_dev,
        reload_dirs=[
            "engine", "fetchers", "routes", "services",
            "middleware", "static",
        ] if is_dev else None,
        reload_includes=["*.py", "*.html", "*.js", "*.css"] if is_dev else None,
        reload_excludes=["*.cache", "*.sqlite3", "*.db", "*.db-journal", "*.pyc", "__pycache__/*"] if is_dev else None,
    )
