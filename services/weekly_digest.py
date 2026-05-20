"""PortfolioPilot - Wöchentlicher Performance-Digest (Gemini 2.0 Flash).

Freitags-Report nach Börsenschluss mit Wochenrückblick:
  - Wochen-Performance (P&L)
  - Score-Veränderungen (Trends)
  - Trendwende-Aktien
  - KI-generierte Zusammenfassung

Wird via Cloud Run Job (Freitag 22:30 CET, nach US-Börsenschluss) oder manuell getriggert.
"""
import logging
from datetime import datetime, timedelta

from config import settings
from services.display_currency import format_display_money

logger = logging.getLogger(__name__)


async def send_weekly_digest():
    """Erstellt und sendet den wöchentlichen Portfolio-Digest via Telegram."""
    if not settings.telegram_configured:
        logger.info("Weekly Digest übersprungen (Telegram nicht konfiguriert)")
        return

    from state import portfolio_data
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        logger.info("Weekly Digest übersprungen (keine Portfolio-Daten)")
        return

    try:
        # 1. Score-Historie laden
        from engine.analysis import get_analysis_history
        history = get_analysis_history(days=7)

        # 2. Wochen-Daten berechnen
        digest_data = _build_digest_data(summary, history)

        # 3. KI-Zusammenfassung generieren
        ai_summary = ""
        if settings.gemini_configured:
            ai_summary = await _generate_ai_summary(digest_data)

        # 4. Report formatieren
        report = _format_digest(digest_data, ai_summary)

        # 5. Via Telegram senden
        from services.telegram import send_message
        await send_message(report, chat_id=settings.TELEGRAM_CHAT_ID)
        logger.info(f"📧 Wöchentlicher Digest gesendet ({len(report)} Zeichen)")

    except Exception as e:
        logger.error(f"Weekly Digest fehlgeschlagen: {e}")


def _build_digest_data(summary, history: list[dict]) -> dict:
    """Berechnet die Kennzahlen für den Wochen-Digest."""
    data = {
        "total_value": summary.total_value,
        "total_cost": summary.total_cost,
        "total_pnl": summary.total_pnl,
        "total_pnl_pct": summary.total_pnl_percent,
        "num_positions": summary.num_positions,
        "score_changes": [],
        "best_performer": None,
        "worst_performer": None,
        "summary": summary,
    }

    # Score-Veränderungen aus Historie berechnen
    if len(history) >= 2:
        first = history[0].get("scores", {})
        last = history[-1].get("scores", {})

        changes = []
        for ticker, latest in last.items():
            if ticker in first:
                diff = latest["score"] - first[ticker]["score"]
                if abs(diff) >= 2:  # Nur relevante Änderungen
                    changes.append({
                        "ticker": ticker,
                        "change": round(diff, 1),
                        "new_score": latest["score"],
                        "new_rating": latest["rating"],
                    })

        data["score_changes"] = sorted(changes, key=lambda x: abs(x["change"]), reverse=True)[:5]

    # Beste/schlechteste Position (nach P&L %)
    stocks = [s for s in summary.stocks if s.position.ticker != "CASH"]
    if stocks:
        best = max(stocks, key=lambda s: s.position.pnl_percent)
        worst = min(stocks, key=lambda s: s.position.pnl_percent)
        data["best_performer"] = {
            "ticker": best.position.ticker,
            "pnl_pct": best.position.pnl_percent,
        }
        data["worst_performer"] = {
            "ticker": worst.position.ticker,
            "pnl_pct": worst.position.pnl_percent,
        }

    return data


async def _generate_ai_summary(digest_data: dict) -> str:
    """Generiert eine KI-Zusammenfassung via Gemini 2.5 Flash."""
    try:
        from services.vertex_ai import get_client

        client = get_client()

        changes_text = ""
        if digest_data["score_changes"]:
            lines = []
            for c in digest_data["score_changes"]:
                arrow = "↑" if c["change"] > 0 else "↓"
                lines.append(f"  {c['ticker']}: {arrow}{abs(c['change']):.0f} → {c['new_score']:.0f} ({c['new_rating']})")
            changes_text = "\n".join(lines)

        prompt = (
            "Du bist ein professioneller Finanzanalyst. Erstelle eine Wochenzusammenfassung "
            "(5-6 Sätze, max 800 Zeichen) auf Deutsch für folgendes Portfolio:\n\n"
            f"Portfoliowert: {format_display_money(digest_data['total_value'], digest_data.get('summary'), digits=0)}\n"
            f"Einstandskosten: {format_display_money(digest_data.get('total_cost', 0), digest_data.get('summary'), digits=0)}\n"
            f"Gesamt-P&L: {format_display_money(digest_data['total_pnl'], digest_data.get('summary'), digits=0, signed=True)} ({digest_data['total_pnl_pct']:+.1f}%)\n"
        )
        if digest_data["best_performer"]:
            prompt += f"Bester: {digest_data['best_performer']['ticker']} ({digest_data['best_performer']['pnl_pct']:+.1f}%)\n"
        if digest_data["worst_performer"]:
            prompt += f"Schwächster: {digest_data['worst_performer']['ticker']} ({digest_data['worst_performer']['pnl_pct']:+.1f}%)\n"
        if changes_text:
            prompt += f"\nScore-Veränderungen:\n{changes_text}\n"

        prompt += (
            "\nStrukturiere so:\n"
            "1. WOCHENRÜCKBLICK: Was ist diese Woche passiert?\n"
            "2. STÄRKEN/SCHWÄCHEN: Was läuft gut/schlecht im Portfolio?\n"
            "3. AUSBLICK: Worauf sollte nächste Woche geachtet werden?\n\n"
            "Fokus auf Trends, Auffälligkeiten und konkrete Handlungsempfehlungen. "
            "Kein Markdown, nur Plain Text mit Emojis."
        )

        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        return response.text.strip() if response.text else ""

    except Exception as e:
        logger.warning(f"AI-Summary für Digest fehlgeschlagen: {e}")
        return ""


def _format_digest(data: dict, ai_summary: str) -> str:
    """Formatiert den Wochen-Digest als Telegram-Nachricht."""
    from models import Rating

    lines = [
        "📊 *PortfolioPilot Wochen-Digest*",
        f"_{datetime.now().strftime('%d.%m.%Y')}_\n",
        f"💰 Portfoliowert: {format_display_money(data['total_value'], data.get('summary'))}",
        f"💵 Einstandskosten: {format_display_money(data.get('total_cost', 0), data.get('summary'))}",
        f"📈 Gesamt-P&L: {format_display_money(data['total_pnl'], data.get('summary'), signed=True)} ({data['total_pnl_pct']:+.1f}%)",
        f"📋 Positionen: {data['num_positions']}",
    ]

    if data["best_performer"]:
        lines.append(f"\n🏆 Bester: {data['best_performer']['ticker']} ({data['best_performer']['pnl_pct']:+.1f}%)")
    if data["worst_performer"]:
        lines.append(f"📉 Schwächster: {data['worst_performer']['ticker']} ({data['worst_performer']['pnl_pct']:+.1f}%)")

    # ── Portfolio Score + Rating (neu, aus Daily übernommen) ──
    summary = data.get("summary")
    if summary:
        try:
            from state import portfolio_data
            report = portfolio_data.get("last_analysis")
            if report:
                score_emoji = {"buy": "🟢", "hold": "🟡", "sell": "🔴"}.get(report.portfolio_rating.value, "⚪")
                lines.append(f"\n🎯 *Portfolio Score: {report.portfolio_score:.1f}/100* {score_emoji}")
        except Exception:
            pass

    # ── Score-Veränderungen der Woche ──
    if data["score_changes"]:
        lines.append("\n📊 *Score-Veränderungen der Woche:*")
        for c in data["score_changes"][:5]:
            arrow = "↑" if c["change"] > 0 else "↓"
            emoji = "🟢" if c["change"] > 0 else "🔴"
            lines.append(f"  {emoji} {c['ticker']}: {arrow}{abs(c['change']):.0f} → {c['new_score']:.0f}")

    # ── Sektor-Attribution (neu, aus Daily übernommen) ──
    if summary:
        try:
            from engine.attribution import calculate_attribution
            attr = calculate_attribution(summary.stocks)
            if attr["sectors"]:
                lines.append("\n🏢 *P&L nach Sektor*")
                for s in attr["sectors"][:5]:
                    emoji = "🟢" if s["pnl_eur"] >= 0 else "🔴"
                    lines.append(f"  {emoji} {s['sector']}: {format_display_money(s['pnl_eur'], summary, digits=0, signed=True)}")
                conc = attr["concentration"]
                lines.append(
                    f"  🎯 Konzentration: {conc['risk_level']} "
                    f"(Top-3 = {conc['top3_pnl_share']:.0f}%)"
                )
        except Exception:
            pass

    # ── Alle Positionen (Ticker | Score | Rating | P&L%) ──
    if summary and summary.stocks:
        lines.append("\n📋 *Alle Positionen*")
        rating_icons = {Rating.BUY: "🟢", Rating.HOLD: "🟡", Rating.SELL: "🔴"}
        stocks_sorted = sorted(
            [s for s in summary.stocks if s.position.ticker != "CASH"],
            key=lambda s: s.score.total_score if s.score else 0,
            reverse=True,
        )
        for stock in stocks_sorted:
            score_val = stock.score.total_score if stock.score else 0
            icon = rating_icons.get(stock.score.rating, "⚪") if stock.score else "⚪"
            pnl = stock.position.pnl_percent
            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(
                f"  {icon} {stock.position.ticker}"
                f" | {score_val:.0f}/100"
                f" | {pnl_sign}{pnl:.1f}%"
            )

    # ── Rebalancing Empfehlung (neu, aus Daily übernommen) ──
    if summary and summary.rebalancing and summary.rebalancing.actions:
        actions_buy = [a for a in summary.rebalancing.actions if a.action == "Kaufen"]
        actions_sell = [a for a in summary.rebalancing.actions if a.action == "Verkaufen"]
        if actions_buy or actions_sell:
            lines.append("\n💡 *Rebalancing Empfehlung*")
            for a in actions_buy[:3]:
                lines.append(f"  🟢 {a.ticker}: {format_display_money(a.amount_eur, summary, digits=0, signed=True)} ({a.shares_delta:+.2f} Stk)")
            for a in actions_sell[:3]:
                lines.append(f"  🔴 {a.ticker}: {format_display_money(a.amount_eur, summary, digits=0)} ({a.shares_delta:+.2f} Stk)")

    # ── KI-Einschätzung ──
    if ai_summary:
        lines.append(f"\n🤖 *KI-Wochenanalyse:*\n{ai_summary}")

    # Footer
    lines.append("\n" + "─" * 30)
    lines.append("_PortfolioPilot Weekly Digest • Freitag 22:30_")

    return "\n".join(lines)
