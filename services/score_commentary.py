"""PortfolioPilot - AI Score Commentary (Gemini 2.0 Flash).

Generiert kurze KI-Kommentare für die auffälligsten Portfolio-Positionen.
Ein einziger API-Call für bis zu 5 Aktien → kosteneffizient.

Kommentare erklären den Score-Kontext in 1-2 Sätzen:
  - Warum ist der Score hoch/niedrig?
  - Was sind die treibenden Faktoren?
"""
import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Feature 1: Structured Output — Dynamisches Schema pro Anfrage
def _build_commentary_schema(tickers: list[str]) -> dict:
    """Erstellt ein JSON-Schema mit Ticker-Feldern."""
    props = {}
    for t in tickers:
        props[t] = {"type": "string", "description": f"Kommentar für {t} (max 150 Zeichen)"}
    return {
        "type": "object",
        "properties": props,
        "required": tickers,
    }


async def generate_score_commentaries(
    stocks: list,  # list[StockFullData]
    top_n: int = 5,
) -> dict[str, str]:
    """Generiert AI-Kommentare für die auffälligsten Positionen.

    Wählt die Top-N auffälligsten Aktien aus (höchster/niedrigster Score,
    größte Veränderung) und generiert einen Kommentar pro Aktie.

    Returns:
        Dict von Ticker → Kommentar-String
    """
    if not settings.gemini_configured:
        return {}

    # Nur Aktien mit Score (kein CASH)
    scored = [s for s in stocks if s.score and s.position.ticker != "CASH"]
    if not scored:
        return {}

    # Auffälligste Aktien auswählen
    candidates = _select_notable_stocks(scored, top_n)
    if not candidates:
        return {}

    try:
        from services.vertex_ai import get_client

        client = get_client()

        # Kontext für jede Aktie aufbauen
        stock_lines = []
        for s in candidates:
            sc = s.score
            bd = sc.breakdown
            pos = s.position
            line = (
                f"{pos.ticker} ({pos.name}): "
                f"Score {sc.total_score:.0f}/100 ({sc.rating.value.upper()}) | "
                f"Quality {bd.quality_score:.0f} | Valuation {bd.valuation_score:.0f} | "
                f"Analyst {bd.analyst_score:.0f} | Technical {bd.technical_score:.0f} | "
                f"Growth {bd.growth_score:.0f} | Momentum {bd.momentum_score:.0f} | "
                f"P&L {pos.pnl_percent:+.1f}%"
            )
            if pos.daily_change_pct:
                line += f" | Heute {pos.daily_change_pct:+.1f}%"
            stock_lines.append(line)

        prompt = (
            "Du bist ein erfahrener Finanzanalyst. Erstelle für jede der folgenden "
            "Aktien einen kurzen, prägnanten Kommentar (1-2 Sätze, max 150 Zeichen) "
            "auf Deutsch, der den Score einordnet und die wichtigsten Treiber nennt.\n\n"
            "Aktien:\n"
            + "\n".join(stock_lines)
        )

        # Structured Output: Schema mit exakten Ticker-Feldern
        commentary_tickers = [s.position.ticker for s in candidates]
        schema = _build_commentary_schema(commentary_tickers)

        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )

        if not response.text:
            return {}

        # JSON parsen
        return _parse_commentary_response(response.text)

    except Exception as e:
        logger.warning(f"Score-Kommentare fehlgeschlagen: {e}")
        return {}


def _select_notable_stocks(scored: list, top_n: int = 5) -> list:
    """Wählt die auffälligsten Aktien für Kommentare aus.

    Kriterien: Höchster Score, niedrigster Score, größte Tagesveränderung.
    Dedupliziert, max top_n Ergebnisse.
    """
    selected = {}

    # Höchste Scores
    by_score = sorted(scored, key=lambda s: s.score.total_score, reverse=True)
    for s in by_score[:2]:
        selected[s.position.ticker] = s

    # Niedrigste Scores
    for s in by_score[-2:]:
        selected[s.position.ticker] = s

    # Größte Tagesveränderung (absolut)
    with_daily = [s for s in scored if s.position.daily_change_pct]
    if with_daily:
        by_daily = sorted(with_daily, key=lambda s: abs(s.position.daily_change_pct or 0), reverse=True)
        for s in by_daily[:2]:
            selected[s.position.ticker] = s

    # Auf top_n begrenzen
    return list(selected.values())[:top_n]


def _parse_commentary_response(text: str) -> dict[str, str]:
    """Parst die Gemini-Antwort als JSON Dict."""
    # Markdown-Codeblock entfernen falls vorhanden
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1])

    try:
        result = json.loads(clean)
        if isinstance(result, dict):
            # Kommentare auf 200 Zeichen begrenzen
            return {k: v[:200] for k, v in result.items() if isinstance(v, str)}
    except json.JSONDecodeError:
        logger.debug(f"Score-Kommentar JSON-Parsing fehlgeschlagen: {text[:200]}")

    return {}
