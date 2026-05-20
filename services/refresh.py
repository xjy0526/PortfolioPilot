"""PortfolioPilot - Daten-Refresh Services.

Enthält die gesamte Refresh-Logik:
- _refresh_data() / _do_refresh(): Voller Refresh aller Quellen
- _quick_price_refresh(): Schneller Kurs-Update (yfinance WS + Batch)
- _update_parqet(): Leichtgewichtiges Parqet-Update
"""
import asyncio
import logging
from datetime import datetime

from state import portfolio_data, refresh_lock, refresh_progress, YFINANCE_ALIASES, TZ_BERLIN
from config import settings
from cache_manager import CacheManager
from models import PortfolioSummary, StockFullData, DataSourceStatus

from fetchers.parqet import fetch_portfolio
from fetchers.fmp import (
    fetch_all_fmp_data, discover_tech_stocks,
)
from fetchers.technical import fetch_technical_indicators
from fetchers.demo_data import (
    get_demo_positions, get_demo_fundamentals,
    get_demo_analyst_data, get_demo_tech_picks,
    get_demo_fmp_ratings,
    get_demo_yfinance_data, get_demo_fear_greed,
)
from fetchers.yfinance_data import fetch_yfinance_data, quick_price_update
from fetchers.fear_greed import fetch_fear_greed_index
from fetchers.currency import fetch_eur_usd_rate, fetch_eur_dkk_rate
from services.currency_converter import CurrencyConverter
from services.display_currency import format_display_money
from engine.scorer import calculate_score
from engine.rebalancer import calculate_rebalancing
from database import save_snapshot
from engine.analysis import build_analysis_report
from services.portfolio_builder import calc_portfolio_totals

logger = logging.getLogger(__name__)


async def _refresh_data():
    """Aktualisiert alle Portfolio-Daten.

    Integriert Daten aus:
      - Parqet (Portfolio-Positionen)
      - FMP (Fundamentals, Analyst, Rating)
      - Technical Indicators (RSI, SMA, Momentum)
      - yFinance (Insider, ESG, Earnings)
      - Fear&Greed Index (Markt-Sentiment)
    """
    if not refresh_lock.locked():
        async with refresh_lock:
            await _do_refresh()
    else:
        logger.info("Refresh bereits aktiv - überspringe")


def _set_progress(step: str, percent: int):
    """Aktualisiert den Refresh-Fortschritt."""
    refresh_progress["step"] = step
    refresh_progress["percent"] = percent


async def _do_refresh():
    """Interne Refresh-Logik (aufgerufen innerhalb des Locks)."""
    portfolio_data["refreshing"] = True
    refresh_progress["started_at"] = datetime.now(tz=TZ_BERLIN).isoformat()
    _set_progress("Starte Refresh...", 0)
    logger.info("🔄 Starte Daten-Refresh...")

    # C4: Error-Aggregation pro Refresh-Zyklus
    _refresh_errors: dict[str, list[str]] = {
        "fmp": [], "yfinance": [], "technical": [],
        "parqet": [], "other": [],
    }

    try:
        # Datenquellen laden
        fear_greed_data = None
        try:
            fear_greed_data = await fetch_fear_greed_index()
            logger.info(f"Fear&Greed: {fear_greed_data.value} ({fear_greed_data.label})")
        except Exception as e:
            logger.warning(f"Fear&Greed nicht verfügbar: {e}")

        # Wechselkurse zentral laden
        _set_progress("Lade Wechselkurse...", 5)
        converter = await CurrencyConverter.create()
        eur_usd_rate = converter.rates.eur_usd
        eur_cny_rate = converter.rates.eur_cny

        # --- 1. Lade Portfolio (bevorzugt aus bestehendem Parqet-Update) ---
        _set_progress("Lade Portfolio...", 10)
        existing_summary = portfolio_data.get("summary")
        using_saved_csv = False
        if existing_summary and existing_summary.stocks:
            # Positionen aus dem letzten Parqet-Update wiederverwenden (spart API-Calls)
            positions = [s.position for s in existing_summary.stocks]
            logger.info(f"Verwende {len(positions)} bestehende Positionen (bereits geladen)")
        else:
            from fetchers.csv_reader import saved_csv_portfolio_exists
            if saved_csv_portfolio_exists():
                using_saved_csv = True
                from services.portfolio_builder import update_saved_csv_portfolio
                await update_saved_csv_portfolio()
                existing_summary = portfolio_data.get("summary")
                positions = [s.position for s in existing_summary.stocks] if existing_summary else []
                logger.info(f"Verwende {len(positions)} Positionen aus lokaler CSV")
            else:
                positions = await fetch_portfolio()

        is_demo = False
        if not positions:
            if using_saved_csv:
                logger.info("Lokale CSV enthält keine Positionen - Refresh beendet")
                return
            if settings.ENVIRONMENT == "production":
                # Production: NIEMALS Demo-Daten laden!
                # Stattdessen existierende Daten behalten oder leeres Portfolio
                logger.warning(
                    "⚠️ Production: Kein Portfolio von Parqet erhalten. "
                    "Bitte /api/parqet/authorize aufrufen fuer OAuth2-Login."
                )
                return
            else:
                logger.info("📋 Kein Portfolio gefunden - lade Demo-Daten (Entwicklungsmodus)")
                positions = get_demo_positions()
                is_demo = True

        # --- 2. Hole Daten für jede Position ---
        _set_progress(f"Analysiere {len(positions)} Positionen...", 20)
        stocks = []
        scores_dict = {}

        # FMP Rate-Limit zurücksetzen (neuer Refresh = frisches Budget)
        try:
            from fetchers.fmp import reset_rate_limit
            reset_rate_limit()
        except Exception:
            pass

        if is_demo:
            demo_fund = get_demo_fundamentals()
            demo_analyst = get_demo_analyst_data()
            demo_fmp = get_demo_fmp_ratings()
            demo_yf = get_demo_yfinance_data()
            if not fear_greed_data:
                fear_greed_data = get_demo_fear_greed()

            for pos in positions:
                fund = demo_fund.get(pos.ticker)
                analyst = demo_analyst.get(pos.ticker)
                fmp_rat = demo_fmp.get(pos.ticker)
                yf = demo_yf.get(pos.ticker)

                score = calculate_score(
                    ticker=pos.ticker,
                    name=pos.name,
                    fundamentals=fund,
                    analyst=analyst,
                    current_price=pos.current_price,
                    fmp_rating=fmp_rat,
                    yfinance_data=yf,
                    fear_greed=fear_greed_data,
                )

                stocks.append(StockFullData(
                    position=pos,
                    fundamentals=fund,
                    analyst=analyst,
                    fmp_rating=fmp_rat,
                    yfinance=yf,
                    score=score,
                ))
                scores_dict[pos.ticker] = score
        else:
            # C1: Daten parallel laden via data_loader Modul
            try:
                from services.data_loader import load_positions_batched
                stocks = await load_positions_batched(positions, fear_greed_data)
                logger.info(f"📊 data_loader: {len(stocks)} Positionen geladen")
            except Exception as e:
                logger.error(f"data_loader fehlgeschlagen: {e}")
                import traceback
                traceback.print_exc()
                stocks = [StockFullData(position=p) for p in positions]

            # Collect scores
            scores_dict = {s.position.ticker: s.score for s in stocks if s.score}

        # --- 3. Rebalancing ---
        _set_progress("Berechne Rebalancing...", 60)
        rebalancing = calculate_rebalancing(positions, scores_dict, stocks=stocks)

        # --- 4. Tech Picks via yFinance Screener (spart ~16 FMP-Calls) ---
        if is_demo:
            tech_picks = get_demo_tech_picks()
        elif portfolio_data.get("tech_picks"):
            tech_picks = portfolio_data["tech_picks"]
            logger.info(f"Tech Picks aus Cache: {len(tech_picks)} Empfehlungen")
        else:
            try:
                from fetchers.yfinance_screener import discover_stocks_yfinance
                tech_picks = await discover_stocks_yfinance(limit=8)
                if not tech_picks:
                    # Fallback auf FMP wenn yFinance leer
                    logger.info("yFinance Screener leer — FMP Fallback")
                    tech_picks = await discover_tech_stocks(limit=8)
                else:
                    logger.info(f"yFinance Screener: {len(tech_picks)} Empfehlungen")
                # AI-Analyse hinzufügen (optional, wenn Gemini konfiguriert)
                if settings.gemini_configured and tech_picks:
                    try:
                        from services.tech_radar_ai import enrich_with_ai_analysis
                        tech_picks = await asyncio.wait_for(
                            enrich_with_ai_analysis(tech_picks),
                            timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Tech-Radar AI-Analyse: Timeout (30s)")
                    except Exception as e:
                        logger.warning(f"Tech-Radar AI-Analyse fehlgeschlagen: {e}")
            except Exception as e:
                logger.error(f"Tech Picks Fehler: {e}")
                tech_picks = get_demo_tech_picks()


        # --- 5. Daily Changes + Preisanpassungen ---
        _set_progress("Lade Tagesänderungen...", 75)
        # Hole Daily Changes per yfinance Batch-Call
        # ABER: Skip wenn Preise gerade erst geladen wurden (< 2 Min alt)
        non_cash_tickers = [s.position.ticker for s in stocks if s.position.ticker != "CASH"]
        yf_tickers_map = {t: YFINANCE_ALIASES.get(t, t) for t in non_cash_tickers}
        yf_tickers_unique = list(set(yf_tickers_map.values()))
        daily_changes = {}

        # Prüfe ob Preise kürzlich aktualisiert wurden (Startup-Preise wiederverwenden)
        last_refresh = portfolio_data.get("last_refresh")
        prices_are_fresh = False
        if last_refresh:
            age_seconds = (datetime.now(tz=TZ_BERLIN) - last_refresh).total_seconds()
            prices_are_fresh = age_seconds < 120  # < 2 Minuten

        if prices_are_fresh:
            # Startup-Preise sind frisch — nur Daily Changes von bestehenden Daten übernehmen
            logger.info("Daily Changes: Startup-Preise frisch (< 2 Min) — Batch-Download uebersprungen")
            for s in stocks:
                if s.position.daily_change_pct is not None:
                    daily_changes[s.position.ticker] = s.position.daily_change_pct
        else:
            try:
                _, daily_raw = await quick_price_update(yf_tickers_unique)
                for orig, yf_t in yf_tickers_map.items():
                    if yf_t in daily_raw:
                        daily_changes[orig] = daily_raw[yf_t]
            except Exception as e:
                logger.debug(f"Daily Changes konnten nicht geladen werden: {e}")

        # Setze daily_change_pct + konvertiere alle Preise nach EUR
        for s in stocks:
            pos = s.position
            if pos.ticker == "CASH":
                pos.price_currency = "EUR"
                continue

            # Daily change setzen
            if pos.ticker in daily_changes:
                pos.daily_change_pct = daily_changes[pos.ticker]

            # Wenn Preis bereits in EUR ist (z.B. von vorherigem Parqet-Update),
            # KEINE erneute Konvertierung durchführen (verhindert Doppelkonvertierung)
            if pos.price_currency == "EUR":
                continue

            # Zentrale Währungskonvertierung
            pos.current_price = converter.to_eur(pos.current_price, pos.ticker)
            pos.price_currency = "EUR"

        # --- 6. Build Summary (alle Werte jetzt in EUR) ---
        _set_progress("Erstelle Zusammenfassung...", 85)
        t = calc_portfolio_totals(stocks)

        summary = PortfolioSummary(
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
            is_demo=is_demo,
            eur_usd_rate=eur_usd_rate,
            eur_cny_rate=eur_cny_rate,
            daily_total_change=t["daily_total_eur"],
            daily_total_change_pct=t["daily_total_pct"],
        )

        portfolio_data["summary"] = summary
        portfolio_data["last_refresh"] = datetime.now(tz=TZ_BERLIN)

        # Analytics-Cache invalidieren (Korrelation, Risk, Benchmark zeigen sofort neue Daten)
        try:
            from routes.analytics import _analytics_cache
            _analytics_cache.clear()
            logger.debug("Analytics-Cache nach Refresh invalidiert")
        except Exception:
            pass

        # Activities cachen (für Attribution, Earnings, Portfolio-History)
        try:
            from fetchers.parqet import fetch_portfolio_activities_raw
            portfolio_data["activities"] = await fetch_portfolio_activities_raw()
        except Exception:
            pass

        # Save daily portfolio snapshot
        try:
            save_snapshot(
                total_value=t["total_value"],
                total_cost=t["total_cost"],
                total_pnl=t["total_pnl"],
                num_positions=len(stocks),
                eur_usd_rate=eur_usd_rate,
            )
        except Exception as e:
            logger.warning(f"Snapshot-Speicherung fehlgeschlagen: {e}")

        _set_progress("Erstelle Analyse-Report...", 90)
        logger.info(
            f"✅ Refresh abgeschlossen: {len(stocks)} Positionen, "
            f"Wert: {format_display_money(t['total_value'], summary)}"
        )

        # Analyse-Report generieren
        try:
            report = build_analysis_report(
                stocks_with_scores=stocks,
                analysis_level="full",
                total_portfolio_value=t["total_value"],
            )
            portfolio_data["last_analysis"] = report
            logger.info(f"📊 Analyse-Report: Portfolio-Score {report.portfolio_score:.1f} ({report.portfolio_rating.value.upper()})")

            # AI Score-Kommentare NUR in Production und zur Analysezeit (spart ~20 Gemini-Calls)
            if settings.gemini_configured and settings.ENVIRONMENT == "production":
                from datetime import datetime as _dt_sc
                import zoneinfo as _zi_sc
                _now_sc = _dt_sc.now(_zi_sc.ZoneInfo("Europe/Berlin"))
                if 15 <= _now_sc.hour <= 16:
                    try:
                        from services.score_commentary import generate_score_commentaries
                        commentaries = await asyncio.wait_for(
                            generate_score_commentaries(stocks),
                            timeout=60.0
                        )
                        for stock in stocks:
                            if stock.score and stock.position.ticker in commentaries:
                                stock.score.ai_comment = commentaries[stock.position.ticker]
                        if commentaries:
                            logger.info(f"🤖 AI-Kommentare: {len(commentaries)} Aktien kommentiert")
                    except asyncio.TimeoutError:
                        logger.warning("Score-Kommentare: Timeout (60s)")
                    except Exception as e:
                        logger.warning(f"Score-Kommentare fehlgeschlagen: {e}")
                else:
                    logger.info(f"🤖 Score-Kommentare übersprungen (außerhalb Analysezeit)")

            # AI Agent Telegram-Report in Production senden
            # Der Scheduler steuert WANN der Refresh läuft (16:15 CET),
            # daher kein zusätzliches Zeitfenster-Gate nötig.
            # Manuelle Refreshes senden ebenfalls einen Report.
            if settings.telegram_configured and settings.ENVIRONMENT == "production":
                try:
                    from services.ai_agent import run_daily_report
                    await run_daily_report()
                except Exception as e:
                    logger.warning(f"AI Agent Report fehlgeschlagen: {e}")
            elif settings.telegram_configured:
                logger.info("🤖 AI Agent übersprungen (nur in Production — manuell via Dashboard verfügbar)")
        except Exception as e:
            logger.warning(f"Analyse-Report Generierung fehlgeschlagen: {e}")

        # Vertex AI Context Cache aktualisieren (spart Token-Kosten)
        if settings.gemini_configured:
            try:
                from services.vertex_ai import cache_portfolio_context
                await asyncio.wait_for(
                    cache_portfolio_context(summary),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                logger.warning("Context Caching: Timeout (30s)")
            except Exception as e:
                logger.debug(f"Context Caching übersprungen: {e}")

    except Exception as e:
        logger.error(f"❌ Refresh fehlgeschlagen: {e}")
        import traceback
        traceback.print_exc()
        _refresh_errors["other"].append(str(e))
    finally:
        portfolio_data["refreshing"] = False
        _set_progress("Fertig", 100)
        # C4: Error-Summary loggen
        total_errors = sum(len(v) for v in _refresh_errors.values())
        if total_errors > 0:
            error_summary = {k: len(v) for k, v in _refresh_errors.items() if v}
            logger.warning(f"⚠️ Refresh-Fehler: {total_errors} gesamt — {error_summary}")
            for source, errors in _refresh_errors.items():
                for err in errors[:3]:  # Max 3 Fehler pro Quelle loggen
                    logger.debug(f"  [{source}] {err}")
        else:
            logger.info("✅ Refresh fehlerfrei abgeschlossen")
        portfolio_data["refresh_errors"] = {
            k: len(v) for k, v in _refresh_errors.items() if v
        }


async def _quick_price_refresh():
    """Schneller Kurs-Update: yfinance WS (Echtzeit) + yfinance Batch (Fallback + Daily Changes)."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return

    if portfolio_data["refreshing"]:
        logger.debug("Voller Refresh aktiv - überspringe Kurs-Update")
        return

    try:
        tickers = [s.position.ticker for s in summary.stocks]
        prices = {}
        daily_changes = {}


        # 1. yfinance WebSocket (Echtzeit-Preise für alle Ticker)
        yf_ws_count = 0
        try:
            from fetchers.yfinance_ws import get_yf_streamer
            yf_streamer = get_yf_streamer()
            if yf_streamer.is_connected:
                yf_ws_prices = yf_streamer.get_all_prices()
                for t in tickers:
                    if t != "CASH":
                        yf_alias = YFINANCE_ALIASES.get(t, t)
                        p = yf_ws_prices.get(yf_alias) or yf_ws_prices.get(t)
                        if p and p > 0:
                            prices[t] = p
                            yf_ws_count += 1
        except Exception as e:
            logger.debug(f"yfinance WS-Preise nicht verfügbar: {e}")

        # 2. yfinance Fallback NUR für Ticker ohne WS-Preis
        # Wenn WS >= 80% abdeckt, keinen Batch-Download starten (spart API-Calls)
        non_cash_count = len([t for t in tickers if t != "CASH"])
        ws_coverage = yf_ws_count / non_cash_count if non_cash_count > 0 else 0
        remaining = [t for t in tickers if t not in prices and t != "CASH"]

        if remaining and ws_coverage < 0.8:
            # WS deckt weniger als 80% ab — Batch-Fallback für restliche Ticker
            ticker_to_yf = {t: YFINANCE_ALIASES.get(t, t) for t in remaining}
            yf_tickers = list(set(ticker_to_yf.values()))
            yf_prices, yf_daily = await quick_price_update(yf_tickers)
            # Map zurück auf Original-Ticker
            for orig, yf_t in ticker_to_yf.items():
                if yf_t in yf_prices:
                    prices[orig] = yf_prices[yf_t]
                if yf_t in yf_daily:
                    daily_changes[orig] = yf_daily[yf_t]
        elif remaining:
            logger.debug(
                f"Quick-Update: WS deckt {ws_coverage:.0%} ab — "
                f"Batch-Download fuer {len(remaining)} Ticker uebersprungen"
            )

        if not prices:
            return

        updated = 0
        daily_updated = 0
        # Zentrale Währungskonvertierung
        converter = await CurrencyConverter.create(
            eur_usd_override=summary.eur_usd_rate if summary.eur_usd_rate > 0 else None
        )

        for stock in summary.stocks:
            ticker = stock.position.ticker
            if ticker == "CASH":
                continue

            if ticker in prices:
                raw_price = prices[ticker]
                stock.position.current_price = converter.to_eur(raw_price, ticker)
                stock.position.price_currency = "EUR"
                updated += 1

            # Daily Changes setzen (aus yfinance)
            if ticker in daily_changes:
                stock.position.daily_change_pct = daily_changes[ticker]
                daily_updated += 1

        # Update summary totals
        t = calc_portfolio_totals(list(summary.stocks))
        summary.total_value = t["total_value"]
        summary.total_cost = t["total_cost"]
        summary.total_pnl = t["total_pnl"]
        summary.total_pnl_percent = t["total_pnl_pct"]
        summary.daily_total_change = t["daily_total_eur"]
        summary.daily_total_change_pct = t["daily_total_pct"]
        summary.last_updated = datetime.now(tz=TZ_BERLIN)

        yf_batch_count = updated - yf_ws_count
        logger.info(
            f"⚡ Quick-Update: {updated}/{len(tickers)} Kurse aktualisiert "
            f"(yfinance-WS: {yf_ws_count}, yfinance-Batch: {yf_batch_count}), "
            f"{daily_updated} Daily Changes, "
            f"Wert: {format_display_money(t['total_value'], summary)}"
        )

        # Save snapshot
        try:
            save_snapshot(
                total_value=t["total_value"],
                total_cost=t["total_cost"],
                total_pnl=t["total_pnl"],
                num_positions=len(summary.stocks),
                eur_usd_rate=summary.eur_usd_rate,
            )
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"Quick-Price-Update fehlgeschlagen: {e}")


async def _update_parqet():
    """Delegiert an services.portfolio_builder (C1 Refactoring)."""
    from services.portfolio_builder import update_parqet
    return await update_parqet()
