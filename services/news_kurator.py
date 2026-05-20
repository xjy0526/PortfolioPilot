"""PortfolioPilot - Personalisierter News-Kurator.

Proaktive, portfoliorelevante Nachrichten-Alerts:
  - Prüft alle 4h (Mo-Fr) via Gemini + Google Search Grounding
    nach Breaking News zu Portfolio-Aktien
  - Filtert nur investment-relevante Nachrichten
    (Earnings, CEO-Wechsel, FDA, M&A, Guidance)
  - Dedupliziert Headlines (kein Spam)
  - Sendet Alerts via Telegram

Structured Output garantiert valides JSON von Gemini.
"""
import json
import logging
from datetime import date, datetime
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Deduplizierung: bereits gesendete Headlines (Reset täglich)
_sent_headlines: set[str] = set()
_sent_date: Optional[date] = None

# Rate Limiting: max 6 Checks pro Tag
_MAX_CHECKS_PER_DAY = 6
_check_count = 0
_check_date: Optional[date] = None


# Structured Output Schema für Gemini
NEWS_ALERT_SCHEMA = {
    "type": "object",
    "properties": {
        "alerts": {
            "type": "array",
            "description": "Liste portfoliorelevanter Nachrichten (leer wenn nichts relevant)",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Betroffener Aktien-Ticker (z.B. AAPL)",
                    },
                    "headline": {
                        "type": "string",
                        "description": "Nachricht in einem Satz auf Deutsch",
                    },
                    "impact": {
                        "type": "string",
                        "enum": ["positiv", "negativ", "neutral"],
                        "description": "Erwartete Auswirkung auf den Aktienkurs",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["hoch", "mittel", "niedrig"],
                        "description": "Dringlichkeit der Nachricht",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "earnings", "guidance", "management",
                            "uebernahme", "regulierung", "produkt",
                            "konkurrenz", "makro", "sonstiges",
                        ],
                        "description": "Nachrichten-Kategorie",
                    },
                },
                "required": ["ticker", "headline", "impact", "urgency", "category"],
            },
        },
        "market_mood": {
            "type": "string",
            "description": "Kurze Einschätzung der aktuellen Marktstimmung (1 Satz)",
        },
    },
    "required": ["alerts", "market_mood"],
}


async def check_portfolio_news(force: bool = False) -> bool:
    """Prüft auf portfoliorelevante Breaking News via Gemini + Search Grounding.

    Args:
        force: Wenn True, Rate-Limiting und Deduplizierung überspringen

    Returns:
        True wenn Alerts gesendet wurden, False wenn nichts relevant war
    """
    global _sent_headlines, _sent_date, _check_count, _check_date

    if not settings.gemini_configured:
        logger.debug("News-Kurator übersprungen (Gemini nicht konfiguriert)")
        return False

    if not settings.telegram_configured:
        logger.debug("News-Kurator übersprungen (Telegram nicht konfiguriert)")
        return False

    # Täglicher Reset
    today = date.today()
    if _sent_date != today:
        _sent_headlines = set()
        _sent_date = today
    if _check_date != today:
        _check_count = 0
        _check_date = today

    # Rate Limiting
    if not force:
        _check_count += 1
        if _check_count > _MAX_CHECKS_PER_DAY:
            logger.debug(f"News-Kurator: Tägliches Limit ({_MAX_CHECKS_PER_DAY}) erreicht")
            return False

    # Portfolio-Ticker holen
    from state import portfolio_data
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        logger.debug("News-Kurator: Keine Portfolio-Daten")
        return False

    tickers = [
        s.position.ticker
        for s in summary.stocks
        if s.position.ticker != "CASH"
    ]
    if not tickers:
        return False

    # Gemini-Call
    try:
        alerts = await _fetch_news_alerts(tickers)
    except Exception as e:
        logger.error(f"News-Kurator Gemini-Call fehlgeschlagen: {e}")
        return False

    if not alerts.get("alerts"):
        logger.info("📰 News-Kurator: Keine relevanten Alerts")
        return False

    # Deduplizieren
    new_alerts = []
    for alert in alerts["alerts"]:
        headline_key = f"{alert['ticker']}:{alert['headline'][:50]}"
        if headline_key not in _sent_headlines:
            _sent_headlines.add(headline_key)
            new_alerts.append(alert)

    if not new_alerts and not force:
        logger.info("📰 News-Kurator: Alle Alerts bereits gesendet (Duplikate)")
        return False

    # Alerts an Telegram senden
    display_alerts = new_alerts if new_alerts else alerts["alerts"]
    message = _format_alerts(display_alerts, alerts.get("market_mood", ""))
    from services.telegram import send_message
    await send_message(message)

    logger.info(f"📰 News-Kurator: {len(display_alerts)} Alerts gesendet")
    return True


async def _fetch_news_alerts(tickers: list[str]) -> dict:
    """Ruft Gemini mit Search Grounding auf um aktuelle News zu finden.

    Args:
        tickers: Liste der Portfolio-Ticker

    Returns:
        dict mit "alerts" (Liste) und "market_mood" (str)
    """
    from services.vertex_ai import get_client, get_grounded_config

    client = get_client()

    ticker_list = ", ".join(tickers[:20])  # Max 20 Ticker
    today_str = datetime.now().strftime("%d.%m.%Y")

    prompt = (
        f"Datum heute: {today_str}\n\n"
        f"Suche nach den aktuellsten, wichtigsten Nachrichten zu folgenden Aktien: "
        f"{ticker_list}\n\n"
        "FILTER-REGELN (STRIKT EINHALTEN):\n"
        "- NUR Nachrichten der letzten 24 Stunden\n"
        "- NUR Nachrichten die einen DIREKTEN Einfluss auf den Aktienkurs haben\n"
        "  (Earnings, CEO-Wechsel, FDA-Entscheidung, Übernahme, Guidance-Änderung, "
        "Produktlaunch, Regulierung, Konkurrenz-Bedrohung)\n"
        "- IGNORIERE allgemeine Marktkommentare, Analystenmeinungen ohne News-Anlass, "
        "und generische Branchentrends\n"
        "- Wenn nichts wirklich Relevantes passiert ist, gib ein LEERES alerts-Array zurück\n"
        "- Maximal 5 Alerts\n"
        "- Headlines auf Deutsch, kurz und prägnant (max 100 Zeichen)\n"
    )

    # Google Search Grounding für echte aktuelle Daten
    config = get_grounded_config()
    config["response_mime_type"] = "application/json"
    config["response_schema"] = NEWS_ALERT_SCHEMA

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=config,
    )

    raw = response.text.strip() if response.text else "{}"

    # JSON parsen
    cleaned = raw
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
        if "alerts" not in result:
            result["alerts"] = []
        if "market_mood" not in result:
            result["market_mood"] = ""
        return result
    except json.JSONDecodeError:
        logger.warning(f"News-Kurator: JSON-Parsing fehlgeschlagen: {raw[:200]}")
        return {"alerts": [], "market_mood": ""}


def _format_alerts(alerts: list[dict], market_mood: str) -> str:
    """Formatiert News-Alerts als Telegram-Nachricht.

    Args:
        alerts: Liste der Alert-Dicts
        market_mood: Marktstimmung als Text

    Returns:
        Formatierter Telegram-Text
    """
    impact_icons = {
        "positiv": "🟢",
        "negativ": "🔴",
        "neutral": "🟡",
    }
    urgency_icons = {
        "hoch": "🔥",
        "mittel": "📌",
        "niedrig": "ℹ️",
    }
    category_icons = {
        "earnings": "📊",
        "guidance": "🎯",
        "management": "👔",
        "uebernahme": "🤝",
        "regulierung": "⚖️",
        "produkt": "🚀",
        "konkurrenz": "⚔️",
        "makro": "🌍",
        "sonstiges": "📰",
    }

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "📡 *PortfolioPilot News-Alert*",
        f"_{now}_\n",
    ]

    for alert in alerts:
        impact_icon = impact_icons.get(alert.get("impact", ""), "⚪")
        urgency_icon = urgency_icons.get(alert.get("urgency", ""), "")
        cat_icon = category_icons.get(alert.get("category", ""), "📰")

        lines.append(
            f"{impact_icon} *{alert.get('ticker', '?')}* {urgency_icon}\n"
            f"  {cat_icon} {alert.get('headline', '')}"
        )

    if market_mood:
        lines.append(f"\n😐 *Marktstimmung:* {market_mood}")

    lines.append(f"\n_Automatisch generiert • {len(alerts)} Alert(s)_")

    return "\n".join(lines)
