"""PortfolioPilot - Telegram Bot Command Handler.

Verarbeitet eingehende Telegram-Nachrichten (Webhook).
Unterstützte Befehle:
  /portfolio — Portfolio-Übersicht mit Scores
  /score AAPL — Score einer einzelnen Aktie
  /refresh — Full Refresh triggern
  /news   — Freie Marktanalyse durch Gemini 2.5 Pro
  /earnings — Earnings-Analyse für Portfolio-Aktien
  /risk [szenario] — Risiko-Szenario-Analyse
  /wissen — Tägliche Lern-Tipps & Projekt-Wissen
  /start  — Willkommensnachricht
  /help   — Befehlsübersicht
  (Freitext) — Portfolio-Chat mit KI
"""
import logging
from typing import Optional

from config import settings
from services.display_currency import format_display_money

logger = logging.getLogger(__name__)


async def handle_update(update: dict) -> None:
    """Verarbeitet ein Telegram-Update (Webhook-Payload).

    Args:
        update: Telegram Update-Objekt (JSON)
    """
    from services.telegram import send_message

    # Debug: Was kommt rein?
    update_keys = list(update.keys())
    logger.info(f"Update keys: {update_keys}")

    message = update.get("message", {})
    if not message:
        logger.info(f"Kein 'message' im Update. Keys: {update_keys}")
        return

    msg_keys = list(message.keys())
    logger.info(f"Message keys: {msg_keys}")

    chat_id = str(message.get("chat", {}).get("id", ""))

    if not chat_id:
        logger.info("Keine chat_id gefunden")
        return

    # Nur erlaubte Chat-ID (Sicherheit)
    if chat_id != settings.TELEGRAM_CHAT_ID:
        logger.warning(f"Unbekannte Chat-ID: {chat_id} (erwartet: {settings.TELEGRAM_CHAT_ID})")
        return

    logger.info(f"Chat-ID OK: {chat_id}")

    # Voice-Nachricht? -> Audio-Handler
    voice = message.get("voice")
    if voice:
        logger.info(f"Voice erkannt: {voice}")
        await _handle_voice_memo(chat_id, voice, message.get("caption", ""))
        return

    text = message.get("text", "").strip()
    if not text:
        logger.info(f"Kein text und kein voice. Message keys: {msg_keys}")
        return

    logger.info(f"Text-Nachricht: {text[:50]}")

    # Command-Router
    cmd = text.split()[0].lower()
    args = text.split()[1:] if len(text.split()) > 1 else []

    if cmd == "/portfolio":
        await _cmd_portfolio(chat_id)
    elif cmd == "/score":
        ticker = args[0].upper() if args else None
        await _cmd_score(chat_id, ticker)
    elif cmd == "/refresh":
        await _cmd_refresh(chat_id)
    elif cmd == "/news":
        await _cmd_news(chat_id)
    elif cmd == "/news-alerts":
        await _cmd_news_alerts(chat_id)
    elif cmd == "/earnings":
        await _cmd_earnings(chat_id)
    elif cmd == "/risk":
        scenario = " ".join(args) if args else None
        await _cmd_risk(chat_id, scenario)
    elif cmd == "/wissen":
        await _cmd_wissen(chat_id, args)
    elif cmd == "/attribution":
        await _cmd_attribution(chat_id)
    elif cmd == "/start":
        await _cmd_start(chat_id)
    elif cmd == "/help":
        await _cmd_help(chat_id)
    elif cmd.startswith("/"):
        await send_message(
            "❓ Unbekannter Befehl. Tippe /help für eine Übersicht.",
            chat_id=chat_id,
        )
    else:
        # Freitext → Portfolio-Chat
        await _cmd_chat(chat_id, text)


# ─────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────

async def _cmd_start(chat_id: str):
    """Willkommensnachricht."""
    from services.telegram import send_message
    await send_message(
        "🚀 *PortfolioPilot AI Agent*\n\n"
        "Ich bin dein persönlicher Finanzanalyst.\n\n"
        "Verfügbare Befehle:\n"
        "  /portfolio — Portfolio-Übersicht\n"
        "  /score AAPL — Score einer Aktie\n"
        "  /refresh — Daten aktualisieren\n"
        "  /news — Marktanalyse (Gemini Pro)\n"
        "  /news-alerts — Portfolio-News prüfen\n"
        "  /wissen — Lern-Tipps & Projekt-Wissen\n"
        "  🎙️ Sprachnachricht — Voice-to-Action\n"
        "  /help — Befehlsübersicht\n",
        chat_id=chat_id,
    )


async def _cmd_help(chat_id: str):
    """Befehlsübersicht."""
    from services.telegram import send_message
    await send_message(
        "📋 *PortfolioPilot Befehle*\n\n"
        "  /portfolio — Portfolio mit Scores und P&L\n"
        "  /score AAPL — Detail-Score einer Aktie\n"
        "  /attribution — Performance-Attribution\n"
        "  /earnings — Earnings-Analyse\n"
        "  /risk — Risiko-Szenarien\n"
        "  /news — Marktanalyse\n"
        "  /news-alerts — Portfolio-News prüfen\n"
        "  /wissen — Lern-Tipps & Projekt-Wissen\n"
        "  /refresh — Full Refresh starten\n"
        "  /help — Diese Übersicht\n\n"
        "🎙️ Sende eine Sprachnachricht für Voice-to-Action!\n"
        "💬 Oder einfach eine Frage schreiben!",
        chat_id=chat_id,
    )


async def _cmd_portfolio(chat_id: str):
    """Portfolio-Übersicht mit Scores."""
    from services.telegram import send_message
    from state import portfolio_data

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        await send_message("⚠️ Keine Portfolio-Daten. Bitte /refresh starten.", chat_id=chat_id)
        return

    lines = ["💰 *Portfolio Übersicht*\n"]
    lines.append(f"Gesamtwert: {format_display_money(summary.total_value, summary)}")

    pnl_emoji = "📈" if summary.total_pnl >= 0 else "📉"
    lines.append(f"{pnl_emoji} P&L: {format_display_money(summary.total_pnl, summary, signed=True)} ({summary.total_pnl_percent:+.1f}%)")

    if summary.daily_total_change != 0:
        day_emoji = "🟢" if summary.daily_total_change >= 0 else "🔴"
        lines.append(f"{day_emoji} Heute: {format_display_money(summary.daily_total_change, summary, signed=True)} ({summary.daily_total_change_pct:+.1f}%)")

    if summary.fear_greed:
        fg = summary.fear_greed
        lines.append(f"😐 Fear&Greed: {fg.value}/100 ({fg.label})")

    # Cash-Bestand
    cash_stock = next((s for s in summary.stocks if s.position.ticker == "CASH"), None)
    if cash_stock:
        lines.append(f"💵 Cash: {format_display_money(cash_stock.position.current_price, summary)}")

    # FMP Usage
    try:
        from fetchers.fmp import get_fmp_usage
        usage = get_fmp_usage()
        lines.append(f"📡 FMP: {usage['requests_today']}/{usage['daily_limit']} Requests")
    except Exception:
        pass

    lines.append("\n📊 *Positionen*")

    # Sortiert nach Score
    stocks_sorted = sorted(
        [s for s in summary.stocks if s.position.ticker != "CASH"],
        key=lambda s: s.score.total_score if s.score else 0,
        reverse=True,
    )

    for stock in stocks_sorted:
        score_val = stock.score.total_score if stock.score else 0
        rating_icons = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}
        icon = rating_icons.get(stock.score.rating.value, "⚪") if stock.score else "⚪"
        pnl = stock.position.pnl_percent
        daily = stock.position.daily_change_pct or 0
        daily_str = f" ({daily:+.1f}%)" if daily != 0 else ""
        lines.append(
            f"  {icon} {stock.position.ticker}: {score_val:.0f}/100"
            f" | P&L: {pnl:+.1f}%{daily_str}"
        )

    await send_message("\n".join(lines), chat_id=chat_id)


async def _cmd_score(chat_id: str, ticker: Optional[str] = None):
    """Detail-Score einer einzelnen Aktie."""
    from services.telegram import send_message
    from state import portfolio_data

    if not ticker:
        await send_message("❓ Bitte Ticker angeben: /score AAPL", chat_id=chat_id)
        return

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        await send_message("⚠️ Keine Portfolio-Daten. Bitte /refresh starten.", chat_id=chat_id)
        return

    # Aktie finden
    stock = None
    for s in summary.stocks:
        if s.position.ticker.upper() == ticker:
            stock = s
            break

    if not stock:
        await send_message(f"❓ {ticker} nicht im Portfolio gefunden.", chat_id=chat_id)
        return

    if not stock.score:
        await send_message(f"⚠️ Kein Score für {ticker} verfügbar.", chat_id=chat_id)
        return

    sc = stock.score
    bd = sc.breakdown
    rating_icons = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}
    icon = rating_icons.get(sc.rating.value, "⚪")

    lines = [
        f"📊 *{ticker} — Detail-Score*\n",
        f"{icon} *Gesamt: {sc.total_score:.1f}/100* ({sc.rating.value.upper()})",
        f"Confidence: {sc.confidence:.0%}\n",
        "📋 *Score-Breakdown*",
        f"  Quality:      {bd.quality_score:.0f}/100 (19%)",
        f"  Analyst:      {bd.analyst_score:.0f}/100 (15%)",
        f"  Valuation:    {bd.valuation_score:.0f}/100 (14%)",
        f"  Technical:    {bd.technical_score:.0f}/100 (13%)",
        f"  Growth:       {bd.growth_score:.0f}/100 (11%)",
        f"  Quantitative: {bd.quantitative_score:.0f}/100 (10%)",
        f"  Sentiment:    {bd.sentiment_score:.0f}/100 (7%)",
        f"  Momentum:     {bd.momentum_score:.0f}/100 (6%)",
        f"  Insider:      {bd.insider_score:.0f}/100 (3%)",
        f"  ESG:          {bd.esg_score:.0f}/100 (2%)",
    ]

    # Position-Infos
    pos = stock.position
    lines.append(f"\n💰 *Position*")
    lines.append(f"  Kurs: {pos.current_price:.2f} {pos.price_currency}")
    lines.append(f"  P&L: {pos.pnl_percent:+.1f}%")
    if pos.daily_change_pct:
        lines.append(f"  Heute: {pos.daily_change_pct:+.1f}%")

    if sc.summary:
        lines.append(f"\n📝 {sc.summary}")

    if sc.ai_comment:
        lines.append(f"\n🤖 {sc.ai_comment}")

    await send_message("\n".join(lines), chat_id=chat_id)


async def _cmd_refresh(chat_id: str):
    """Triggert einen Full Refresh."""
    from services.telegram import send_message
    from state import refresh_lock

    if refresh_lock.locked():
        await send_message("🔄 Refresh läuft bereits...", chat_id=chat_id)
        return

    await send_message("🔄 Full Refresh gestartet... Dies dauert ~2-3 Minuten.", chat_id=chat_id)

    try:
        from services.refresh import _refresh_data
        await _refresh_data()

        # Ergebnis melden
        from state import portfolio_data
        summary = portfolio_data.get("summary")
        if summary:
            from fetchers.fmp import get_fmp_usage
            usage = get_fmp_usage()
            await send_message(
                f"✅ Refresh abgeschlossen!\n"
                f"📊 {summary.num_positions} Positionen geladen\n"
                f"💰 Portfoliowert: {format_display_money(summary.total_value, summary)}\n"
                f"📡 FMP: {usage['requests_today']}/{usage['daily_limit']} Requests",
                chat_id=chat_id,
            )
        else:
            await send_message("⚠️ Refresh abgeschlossen, aber keine Daten geladen.", chat_id=chat_id)
    except Exception as e:
        logger.error(f"/refresh fehlgeschlagen: {e}")
        await send_message(f"❌ Refresh fehlgeschlagen: {e}", chat_id=chat_id)


# Rate Limiting: max 5 /news pro Stunde pro User
_news_cooldown: dict[str, list[float]] = {}
_MAX_NEWS_PER_HOUR = 5


async def _cmd_news(chat_id: str):
    """Freie Marktanalyse durch Gemini 2.5 Pro."""
    import time as _time
    from services.telegram import send_message

    if not settings.gemini_configured:
        await send_message(
            "⚠️ Gemini API-Key nicht konfiguriert.\n"
            "Bitte GEMINI_API_KEY in .env setzen.",
            chat_id=chat_id,
        )
        return

    # Rate Limiting prüfen
    now = _time.time()
    recent = [t for t in _news_cooldown.get(chat_id, []) if now - t < 3600]
    if len(recent) >= _MAX_NEWS_PER_HOUR:
        await send_message(
            f"⏳ Max {_MAX_NEWS_PER_HOUR} /news Anfragen pro Stunde. "
            "Bitte warte etwas.",
            chat_id=chat_id,
        )
        return
    _news_cooldown[chat_id] = recent + [now]

    # Lade-Hinweis senden
    await send_message("🔍 Analysiere aktuelle Marktlage mit Gemini Pro...", chat_id=chat_id)

    # Portfolio-Kontext sammeln (wenn vorhanden)
    portfolio_context = _get_portfolio_context()

    # Gemini 2.5 Pro Anfrage
    try:
        from services.vertex_ai import get_client, get_grounded_config, get_cached_content

        client = get_client()

        prompt_parts = [
            "Du bist ein erfahrener Finanzanalyst und Marktexperte. "
            "Gib eine aktuelle Marktanalyse auf Deutsch. Strukturiere deine Antwort so:\n\n"
            "1. 📰 MARKTNACHRICHTEN: Die 3-5 wichtigsten aktuellen Ereignisse an den Finanzmärkten "
            "(Zinsentscheidungen, Earnings, Geopolitik, Währungen, Rohstoffe)\n\n"
            "2. 📊 MARKTTRENDS: Aktuelle Trends bei den großen Indizes (S&P 500, NASDAQ, DAX), "
            "Sektorrotation, Volatilität\n\n"
            "3. 🔮 AUSBLICK: Kurze Einschätzung für die kommenden Tage/Wochen\n\n",
        ]

        if portfolio_context:
            prompt_parts.append(
                "4. 💼 PORTFOLIO-RELEVANZ: Wie wirken sich die aktuellen Entwicklungen "
                "auf folgendes Portfolio aus? Gib konkrete Hinweise zu einzelnen Positionen.\n\n"
                f"Portfolio:\n{portfolio_context}\n\n"
            )

        prompt_parts.append(
            "Halte dich prägnant (max 2000 Zeichen). Nutze Emojis für Übersichtlichkeit. "
            "Kein Markdown, nur Plain Text. Datum heute: "
            + __import__("datetime").datetime.now().strftime("%d.%m.%Y")
        )

        prompt = "".join(prompt_parts)

        # Search Grounding + Context Cache für echte Marktdaten
        config = get_grounded_config()
        cached = get_cached_content()
        if cached:
            config["cached_content"] = cached

        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=config,
        )

        result = response.text.strip() if response.text else ""

        # Fallback: Wenn Pro rate-limited ist, versuche Flash
        if not result:
            raise Exception("Leere Antwort von Gemini Pro")

        model_used = "Gemini 2.5 Pro"

    except Exception as e:
        logger.warning(f"Gemini Pro fehlgeschlagen ({e}), versuche Flash Fallback...")

        try:
            import asyncio as _aio
            await _aio.sleep(2)  # Kurze Pause

            client_fb = get_client()
            response = await client_fb.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            result = response.text.strip() if response.text else "Keine Analyse verfügbar."
            model_used = "Gemini 2.0 Flash"

        except Exception as e2:
            logger.error(f"/news komplett fehlgeschlagen: {e2}")
            await send_message(
                f"❌ Analyse fehlgeschlagen. Bitte später erneut versuchen.\n({e2})",
                chat_id=chat_id,
            )
            return

    # Header + Antwort senden
    full_message = f"📰 *PortfolioPilot News & Analyse*\n_{model_used}_\n\n{result}"
    await send_message(full_message, chat_id=chat_id)

    logger.info(f"✅ /news Befehl ausgeführt ({len(result)} Zeichen, {model_used})")


# ─────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────

def _get_portfolio_context() -> str:
    """Holt Portfolio-Daten als Text-Kontext für Gemini."""
    try:
        from state import portfolio_data
        summary = portfolio_data.get("summary")
        if not summary or not summary.stocks:
            return ""

        lines = []
        for stock in summary.stocks:
            if stock.position.ticker == "CASH":
                continue
            score_val = stock.score.total_score if stock.score else 0
            rating = stock.score.rating.value if stock.score else "?"
            pnl = stock.position.pnl_percent
            lines.append(
                f"  {stock.position.ticker} ({stock.position.name}) | "
                f"Score: {score_val:.0f} | {rating} | P&L: {pnl:+.1f}% | {stock.position.sector}"
            )

        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# Performance Attribution
# ─────────────────────────────────────────────────────────────

async def _cmd_attribution(chat_id: str):
    """Performance Attribution: P&L-Zerlegung."""
    from services.telegram import send_message
    from state import portfolio_data

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        await send_message("⚠️ Keine Portfolio-Daten. Bitte /refresh starten.", chat_id=chat_id)
        return

    # Activities aus State lesen (bereits beim Refresh gecacht)
    activities = portfolio_data.get("activities")
    if not activities:
        try:
            from fetchers.parqet import fetch_portfolio_activities_raw
            activities = await fetch_portfolio_activities_raw()
        except Exception:
            pass

    from engine.attribution import calculate_attribution
    attr = calculate_attribution(summary.stocks, activities)

    lines = ["📊 *Performance Attribution*\n"]

    # Gesamt
    pnl_emoji = "📈" if attr["total_pnl_eur"] >= 0 else "📉"
    lines.append(
        f"{pnl_emoji} Gesamt-P&L: {format_display_money(attr['total_pnl_eur'], summary, signed=True)} "
        f"({attr['total_pnl_pct']:+.1f}%)"
    )

    # Sektor-Beitrag
    if attr["sectors"]:
        lines.append("\n🏢 *Sektor-Beitrag:*")
        for s in attr["sectors"][:5]:
            emoji = "🟢" if s["pnl_eur"] >= 0 else "🔴"
            lines.append(
                f"  {emoji} {s['sector']}: {format_display_money(s['pnl_eur'], summary, digits=0, signed=True)} "
                f"({s['contribution_pct']:+.1f}pp)"
            )

    # Top/Flop
    if attr["top_performers"]:
        lines.append("\n🏆 *Top-Performer:*")
        for p in attr["top_performers"][:3]:
            lines.append(
                f"  🟢 {p['ticker']}: {format_display_money(p['pnl_eur'], summary, digits=0, signed=True)} "
                f"({p['pnl_pct']:+.1f}%)"
            )

    if attr["worst_performers"]:
        lines.append("\n📉 *Flop-Performer:*")
        for p in attr["worst_performers"][:3]:
            lines.append(
                f"  🔴 {p['ticker']}: {format_display_money(p['pnl_eur'], summary, digits=0, signed=True)} "
                f"({p['pnl_pct']:+.1f}%)"
            )

    # Dividenden
    div = attr["dividends"]
    if div["total_eur"] > 0:
        lines.append(f"\n💵 Dividenden (gesamt): {format_display_money(div['total_eur'], summary)}")

    # Konzentration
    conc = attr["concentration"]
    lines.append(
        f"\n🎯 *Konzentration:* {conc['risk_level']} "
        f"(Top-3 = {conc['top3_pnl_share']:.0f}% des P&L)"
    )

    await send_message("\n".join(lines), chat_id=chat_id)
    logger.info("✅ /attribution ausgeführt")


# ─────────────────────────────────────────────────────────────
# Feature 2: /earnings — Earnings-Analyse
# ─────────────────────────────────────────────────────────────

async def _cmd_earnings(chat_id: str):
    """Earnings-Analyse für Portfolio-Aktien via Gemini 2.5 Pro."""
    from services.telegram import send_message
    from state import portfolio_data

    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        await send_message("⚠️ Keine Portfolio-Daten. Bitte /refresh starten.", chat_id=chat_id)
        return

    if not settings.gemini_configured:
        await send_message("⚠️ Qwen nicht konfiguriert.", chat_id=chat_id)
        return

    await send_message("📅 Analysiere Earnings... (dauert ~10s)", chat_id=chat_id)

    tickers = [s.position.ticker for s in summary.stocks if s.position.ticker != "CASH"]

    try:
        from services.earnings_ai import analyze_earnings
        results = await analyze_earnings(tickers)

        if not results:
            await send_message("ℹ️ Keine Earnings-Daten gefunden.", chat_id=chat_id)
            return

        lines = ["📅 *Earnings-Übersicht*\n"]

        # Gruppiert nach Status
        reported = [r for r in results if r.status == "reported"]
        upcoming = [r for r in results if r.status == "upcoming"]

        if reported:
            lines.append("📊 *Kürzlich berichtet:*")
            for r in reported:
                beat_icon = "✅" if r.beat else "❌" if r.beat is False else "➡️"
                lines.append(f"  {beat_icon} *{r.ticker}* ({r.quarter}): {r.key_takeaway}")
            lines.append("")

        if upcoming:
            lines.append("🔜 *Bald anstehend:*")
            for r in upcoming:
                lines.append(f"  📅 *{r.ticker}* ({r.quarter}): {r.key_takeaway}")

        await send_message("\n".join(lines), chat_id=chat_id)
        logger.info(f"✅ /earnings: {len(results)} Aktien analysiert")

    except Exception as e:
        logger.error(f"/earnings fehlgeschlagen: {e}")
        await send_message(f"❌ Earnings-Analyse fehlgeschlagen: {e}", chat_id=chat_id)


# ─────────────────────────────────────────────────────────────
# Feature 3: Portfolio-Chat (Freitext)
# ─────────────────────────────────────────────────────────────

async def _cmd_chat(chat_id: str, question: str):
    """Freier Portfolio-Chat mit Gemini 2.5 Pro."""
    from services.telegram import send_message

    if not settings.gemini_configured:
        await send_message(
            "💬 Chat ist nicht verfügbar (Qwen nicht konfiguriert).\n"
            "Nutze /help für verfügbare Befehle.",
            chat_id=chat_id,
        )
        return

    try:
        from services.vertex_ai import get_client, get_grounded_config, get_cached_content

        client = get_client()
        portfolio_context = _get_portfolio_context()

        # URLs aus der Nachricht extrahieren und Inhalte laden
        url_context = ""
        from services.url_fetcher import extract_urls, fetch_multiple_urls
        urls = extract_urls(question)
        if urls:
            await send_message(
                f"🌐 Lade {len(urls)} verlinkte Seite(n)...",
                chat_id=chat_id,
            )
            url_contents = await fetch_multiple_urls(urls, max_chars_per_url=3000)
            url_parts = []
            for url, content in url_contents.items():
                url_parts.append(f"--- Inhalt von {url} ---\n{content}\n---")
            url_context = "\n\n".join(url_parts)

        system_prompt = (
            "Du bist PortfolioPilot, ein intelligenter Portfolio-Assistent. "
            "Antworte kurz und prägnant auf Deutsch (max 800 Zeichen). "
            "Nutze Emojis sparsam. Sei direkt und hilfreich.\n\n"
        )
        if portfolio_context:
            system_prompt += (
                "Hier ist der aktuelle Portfolio-Status:\n"
                f"{portfolio_context}\n\n"
            )
        if url_context:
            system_prompt += (
                "Der User hat folgende externe Quellen geteilt:\n"
                f"{url_context}\n\n"
                "Beziehe diese Informationen in deine Antwort ein.\n\n"
            )

        # Versuche mit Search Grounding für aktuelle Daten
        config = get_grounded_config()

        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=f"{system_prompt}User-Frage: {question}",
            config=config,
        )

        result = response.text.strip() if response.text else "Leider konnte ich keine Antwort generieren."

        # Auf 4000 Zeichen begrenzen (Telegram-Limit)
        if len(result) > 4000:
            result = result[:3950] + "\n\n_(gekürzt)_"

        await send_message(f"💬 {result}", chat_id=chat_id)
        logger.info(f"✅ Chat beantwortet ({len(result)} Zeichen, {len(urls)} URLs)")

    except Exception as e:
        logger.error(f"Chat fehlgeschlagen: {e}")
        await send_message(
            "❌ Konnte deine Frage nicht beantworten. Versuche es später erneut.",
            chat_id=chat_id,
        )


# ─────────────────────────────────────────────────────────────
# Feature 5: /risk — Risiko-Szenario-Analyse
# ─────────────────────────────────────────────────────────────

_RISK_SCENARIOS = {
    "rezession": "Eine globale Rezession mit fallenden Unternehmensgewinnen, steigender Arbeitslosigkeit und einbrechendem Konsum.",
    "zinserhöhung": "Eine überraschende Zinserhöhung der Zentralbanken um 100+ Basispunkte.",
    "techcrash": "Ein Tech-Crash mit 30-40% Kurseinbruch bei Technologieaktien durch Bewertungskorrektur.",
    "inflation": "Stagflation: hartnäckig hohe Inflation (>6%) bei gleichzeitig stagnierendem Wirtschaftswachstum.",
    "geopolitik": "Eskalation geopolitischer Spannungen mit Handelskriegen und Lieferketten-Störungen.",
}


async def _cmd_risk(chat_id: str, scenario: Optional[str] = None):
    """Risiko-Szenario-Analyse via Gemini 2.5 Pro."""
    from services.telegram import send_message

    if not scenario:
        # Übersicht zeigen
        lines = ["🛡️ *Risiko-Szenarien*\n", "Verfügbare Szenarien:\n"]
        for key, desc in _RISK_SCENARIOS.items():
            lines.append(f"  `/risk {key}` — {desc[:60]}...")
        lines.append("\nBeispiel: `/risk rezession`")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    scenario_key = scenario.lower().strip()
    if scenario_key not in _RISK_SCENARIOS:
        await send_message(
            f"⚠️ Unbekanntes Szenario: `{scenario}`\n"
            "Tippe /risk für verfügbare Szenarien.",
            chat_id=chat_id,
        )
        return

    if not settings.gemini_configured:
        await send_message("⚠️ Qwen nicht konfiguriert.", chat_id=chat_id)
        return

    await send_message(f"🛡️ Analysiere Szenario: *{scenario_key.title()}*... (dauert ~15s)", chat_id=chat_id)

    try:
        from services.vertex_ai import get_client, get_grounded_config

        client = get_client()
        portfolio_context = _get_portfolio_context()

        if not portfolio_context:
            await send_message("⚠️ Keine Portfolio-Daten geladen.", chat_id=chat_id)
            return

        scenario_desc = _RISK_SCENARIOS[scenario_key]

        prompt = (
            "Du bist ein erfahrener Risikoanalyst. Analysiere wie folgendes Szenario "
            "das unten stehende Portfolio treffen würde.\n\n"
            f"SZENARIO: {scenario_desc}\n\n"
            f"PORTFOLIO:\n{portfolio_context}\n\n"
            "Erstelle eine kompakte Analyse auf Deutsch (max 1000 Zeichen):\n"
            "1. Geschätzter Portfolio-Impact (leicht/mittel/schwer)\n"
            "2. Die 3 am stärksten gefährdeten Positionen und warum\n"
            "3. Die 2-3 resilientesten Positionen\n"
            "4. Eine konkrete Handlungsempfehlung\n\n"
            "Nutze Emojis für Übersichtlichkeit."
        )

        config = get_grounded_config()

        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=config,
        )

        result = response.text.strip() if response.text else "Analyse nicht verfügbar."

        if len(result) > 4000:
            result = result[:3950] + "\n\n_(gekürzt)_"

        await send_message(
            f"🛡️ *Risiko-Analyse: {scenario_key.title()}*\n\n{result}",
            chat_id=chat_id,
        )
        logger.info(f"✅ /risk {scenario_key} ausgeführt ({len(result)} Zeichen)")

    except Exception as e:
        logger.error(f"/risk fehlgeschlagen: {e}")
        await send_message(f"❌ Risiko-Analyse fehlgeschlagen: {e}", chat_id=chat_id)


# ─────────────────────────────────────────────────────────────
# Feature: /wissen — Lern-Tipps & Projekt-Wissen
# ─────────────────────────────────────────────────────────────

async def _cmd_wissen(chat_id: str, args: list[str]):
    """Lern-Tipps und Projekt-Wissen.

    Sub-Commands:
      /wissen          → Tipp des Tages
      /wissen quiz     → KI-generierte Quiz-Frage
      /wissen PROJEKT  → Projekt-Zusammenfassung
      /wissen alle     → Übersicht aller Projekte
    """
    from services.telegram import send_message
    from services.knowledge_data import (
        get_daily_tip,
        get_project_summary,
        get_projects_overview,
    )

    sub = args[0].lower() if args else ""

    if sub == "quiz":
        await _cmd_wissen_quiz(chat_id)
    elif sub == "alle" or sub == "all":
        await send_message(get_projects_overview(), chat_id=chat_id)
    elif sub:
        # Projekt-Zusammenfassung
        summary = get_project_summary(sub)
        await send_message(summary, chat_id=chat_id)
    else:
        # Tipp des Tages
        tip = get_daily_tip()
        header = f"🧠 *Wissen des Tages*\n_{tip['category']}_ • {tip['title']}\n\n"
        await send_message(header + tip["text"], chat_id=chat_id)
        logger.info(f"✅ /wissen: Tipp '{tip['title']}' gesendet")


async def _cmd_wissen_quiz(chat_id: str):
    """Generiert eine Quiz-Frage basierend auf Projekttechnologien via Gemini."""
    import time as _time
    from services.telegram import send_message

    if not settings.gemini_configured:
        await send_message(
            "⚠️ Quiz benötigt Gemini API-Key.\n"
            "Nutze `/wissen` für den Tipp des Tages (ohne Gemini).",
            chat_id=chat_id,
        )
        return

    # Rate Limiting (teilt sich den Cooldown mit /news)
    now = _time.time()
    recent = [t for t in _news_cooldown.get(chat_id, []) if now - t < 3600]
    if len(recent) >= _MAX_NEWS_PER_HOUR:
        await send_message(
            "⏳ Rate-Limit erreicht. Bitte warte etwas.",
            chat_id=chat_id,
        )
        return
    _news_cooldown[chat_id] = recent + [now]

    await send_message("🧠 Generiere Quiz-Frage...", chat_id=chat_id)

    try:
        from services.knowledge_data import get_all_technologies, PROJECT_KNOWLEDGE
        from services.vertex_ai import get_client

        client = get_client()
        techs = get_all_technologies()
        projects = ", ".join(
            f"{p['name']} ({', '.join(p['technologies'][:5])})"
            for p in PROJECT_KNOWLEDGE.values()
        )

        prompt = (
            "Du bist ein freundlicher KI-Trainer. "
            "Erstelle EINE Quiz-Frage über folgende Technologien und Projekte. "
            "Die Frage soll für einen Programmier-Anfänger geeignet sein.\n\n"
            f"Projekte: {projects}\n"
            f"Technologien: {', '.join(techs[:20])}\n\n"
            "Format (Plain Text, kein Markdown):\n"
            "❓ [Frage]\n\n"
            "A) [Option 1]\n"
            "B) [Option 2]\n"
            "C) [Option 3]\n\n"
            "💡 Antwort: [Buchstabe]) [Erklärung in 1-2 Sätzen]\n\n"
            "Halte die Frage kurz und praxisbezogen."
        )

        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        result = response.text.strip() if response.text else "Quiz-Frage konnte nicht generiert werden."

        await send_message(f"🧠 *Wissen-Quiz*\n\n{result}", chat_id=chat_id)
        logger.info(f"✅ /wissen quiz ausgeführt ({len(result)} Zeichen)")

    except Exception as e:
        logger.error(f"/wissen quiz fehlgeschlagen: {e}")
        await send_message(f"❌ Quiz fehlgeschlagen: {e}", chat_id=chat_id)


# ─────────────────────────────────────────────────────────────
# Feature: Voice-to-Action (Sprachnachrichten)
# ─────────────────────────────────────────────────────────────

# Max. Dateigröße für Sprachnachrichten (20 MB Telegram-Limit)
_MAX_VOICE_SIZE = 20 * 1024 * 1024


async def _handle_voice_memo(chat_id: str, voice: dict, caption: str = ""):
    """Verarbeitet eine Telegram-Sprachnachricht.

    Lädt die Audio-Datei herunter und sendet sie nativ an Gemini
    zur Analyse mit Portfolio-Kontext.

    Args:
        chat_id: Telegram Chat-ID
        voice: Telegram Voice-Objekt (file_id, duration, file_size etc.)
        caption: Optionale Text-Caption zur Sprachnachricht
    """
    from services.telegram import send_message, download_telegram_file

    if not settings.gemini_configured:
        await send_message(
            "🎙️ Sprachnachrichten benötigen Gemini API.\n"
            "Bitte GEMINI_API_KEY in .env setzen.",
            chat_id=chat_id,
        )
        return

    # Dateigröße prüfen
    file_size = voice.get("file_size", 0)
    if file_size > _MAX_VOICE_SIZE:
        await send_message(
            "⚠️ Sprachnachricht zu groß. Bitte halte dich kürzer (max 2 Min).",
            chat_id=chat_id,
        )
        return

    duration = voice.get("duration", 0)
    await send_message(
        f"🎙️ Sprachnachricht empfangen ({duration}s). Analysiere...",
        chat_id=chat_id,
    )

    try:
        # Audio herunterladen
        file_id = voice["file_id"]
        audio_bytes = await download_telegram_file(file_id)

        # Gemini-Analyse
        result = await _process_voice_with_gemini(audio_bytes, caption)

        # Auf 4000 Zeichen begrenzen (Telegram-Limit)
        if len(result) > 4000:
            result = result[:3950] + "\n\n_(gekürzt)_"

        await send_message(f"🎙️ *Voice-Analyse*\n\n{result}", chat_id=chat_id)
        logger.info(f"✅ Voice-Memo verarbeitet ({duration}s, {len(audio_bytes)} Bytes)")

    except Exception as e:
        logger.error(f"Voice-Memo fehlgeschlagen: {e}")
        await send_message(
            f"❌ Sprachnachricht konnte nicht verarbeitet werden: {e}",
            chat_id=chat_id,
        )


async def _process_voice_with_gemini(audio_bytes: bytes, caption: str = "") -> str:
    """Verarbeitet Audio in 2 Stufen (beide Flash für Zuverlässigkeit):

    1. Gemini Flash transkribiert das Audio
    2. Gemini Flash beantwortet die Frage mit Portfolio-Kontext + Search Grounding

    Args:
        audio_bytes: OGG-Audio-Datei als Bytes
        caption: Optionaler Zusatztext

    Returns:
        KI-Antwort als Text
    """
    from services.vertex_ai import Content, Part, get_client, get_grounded_config

    client = get_client()

    # ── Schritt 1: Audio transkribieren ──
    logger.info(f"Voice Schritt 1: Transkription starten ({len(audio_bytes)} Bytes)")
    audio_part = Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
    instruction = Part(text=(
        "Hoere diese Sprachnachricht an und gib den Inhalt als Text wieder. "
        "Fasse zusammen was der User sagt/fragt. Antworte NUR mit dem Inhalt "
        "der Nachricht, keine eigene Analyse. Deutsch."
    ))

    transcript_response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=[Content(role="user", parts=[audio_part, instruction])],
    )

    transcript = transcript_response.text.strip() if transcript_response.text else ""
    if not transcript:
        logger.warning("Voice Schritt 1: Transkription leer")
        return "Sprachnachricht konnte nicht verstanden werden."

    logger.info(f"Voice Schritt 1 OK: '{transcript[:80]}...'")

    # ── Schritt 2: Transkript beantworten (Flash + Grounding) ──
    logger.info("Voice Schritt 2: Antwort generieren")
    portfolio_context = _get_portfolio_context()

    question = transcript
    if caption:
        question = f"{caption}: {transcript}"

    # Reichhaltiger Kontext fuer bessere Antworten
    system_prompt = (
        "Du bist PortfolioPilot, ein persoenlicher Portfolio-Assistent. "
        "Der User hat per Sprachnachricht eine Frage gestellt.\n\n"
        "WICHTIG:\n"
        "- Beziehe dich KONKRET auf die Positionen des Users wenn relevant\n"
        "- Nenne Ticker und aktuelle P&L wenn es zur Frage passt\n"
        "- Nutze Google Search fuer aktuelle Marktdaten und News\n"
        "- Antworte auf Deutsch, max 2000 Zeichen\n"
        "- Sei direkt, konkret und hilfreich\n\n"
    )
    if portfolio_context:
        # Portfolio-Summary hinzufuegen
        try:
            from state import portfolio_data
            summary = portfolio_data.get("summary")
            if summary:
                system_prompt += (
                    f"PORTFOLIO-UEBERSICHT:\n"
                    f"Gesamtwert: {format_display_money(summary.total_value, summary, digits=0)}\n"
                    f"Gesamt P&L: {format_display_money(summary.total_pnl, summary, digits=0, signed=True)} ({summary.total_pnl_percent:+.1f}%)\n"
                    f"Positionen: {summary.num_positions}\n\n"
                )
        except Exception:
            pass
        system_prompt += (
            "EINZELPOSITIONEN:\n"
            f"{portfolio_context}\n\n"
        )
    else:
        system_prompt += "HINWEIS: Keine Portfolio-Daten geladen.\n\n"

    config = get_grounded_config()

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{system_prompt}USER-FRAGE: {question}",
        config=config,
    )

    answer = response.text.strip() if response.text else "Keine Antwort erhalten."
    logger.info(f"Voice Schritt 2 OK: {len(answer)} Zeichen")

    return f"Transkript: _{transcript}_\n\n{answer}"


# ─────────────────────────────────────────────────────────────
# Feature: /news-alerts — Proaktive Portfolio-News
# ─────────────────────────────────────────────────────────────

async def _cmd_news_alerts(chat_id: str):
    """Prüft auf portfoliorelevante Breaking News."""
    from services.telegram import send_message

    if not settings.gemini_configured:
        await send_message(
            "⚠️ News-Alerts benötigen Gemini API-Key.",
            chat_id=chat_id,
        )
        return

    await send_message("📡 Prüfe Portfolio-News...", chat_id=chat_id)

    try:
        from services.news_kurator import check_portfolio_news
        sent = await check_portfolio_news(force=True)
        if not sent:
            await send_message(
                "✅ Keine neuen portfoliorelevanten Nachrichten gefunden.",
                chat_id=chat_id,
            )
    except Exception as e:
        logger.error(f"/news-alerts fehlgeschlagen: {e}")
        await send_message(f"❌ News-Check fehlgeschlagen: {e}", chat_id=chat_id)
