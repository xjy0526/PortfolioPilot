"""PortfolioPilot - Tech-Radar KI-Analyse (v2: Gemini 2.5 Pro).

Nutzt Google Gemini 2.5 Pro für tiefgehende Investment-Analysen
der Tech-Empfehlungen. Ein einziger API-Call für alle Empfehlungen.

Analyse umfasst:
  - Bull/Bear-Case
  - Wettbewerbs-Einordnung
  - Risikofaktoren
  - Konkrete Einschätzung

Fallback: Ohne Gemini-Key bleiben ai_summary-Felder leer.
"""
import logging
from typing import Optional

from config import settings
from models import TechRecommendation

logger = logging.getLogger(__name__)


async def enrich_with_ai_analysis(
    recommendations: list[TechRecommendation],
) -> list[TechRecommendation]:
    """Reichert Tech-Empfehlungen mit KI-generierten Investment-Analysen an.

    Nutzt Gemini 2.5 Pro für tiefgehende Analysen:
      - Bull/Bear-Case pro Aktie
      - Wettbewerbs-Kontext
      - Risikofaktoren
      - Bewertungs-Einschätzung

    Ein einziger API-Call für alle Empfehlungen.
    Fallback: Bei Fehler oder fehlendem Key → Empfehlungen unverändert.
    """
    if not settings.gemini_configured:
        logger.debug("Gemini nicht konfiguriert – Tech-Radar AI übersprungen")
        return recommendations

    if not recommendations:
        return recommendations

    try:
        from services.vertex_ai import get_client, get_grounded_config

        client = get_client()

        # Kontext aufbauen: Alle Empfehlungen mit verfügbaren Kennzahlen
        stock_lines = []
        for i, rec in enumerate(recommendations):
            parts = [f"{i+1}. {rec.ticker} ({rec.name})"]
            parts.append(f"Score: {rec.score:.0f}/100")
            if rec.current_price:
                parts.append(f"Kurs: ${rec.current_price:.2f}")
            if rec.market_cap:
                mcap_b = rec.market_cap / 1e9
                parts.append(f"MarketCap: ${mcap_b:.0f}B")
            if rec.pe_ratio:
                parts.append(f"P/E: {rec.pe_ratio:.1f}")
            if rec.revenue_growth is not None:
                parts.append(f"Revenue Growth: {rec.revenue_growth:+.1f}%")
            if rec.roe is not None:
                parts.append(f"ROE: {rec.roe:.1f}%")
            if rec.analyst_rating:
                parts.append(f"Analyst: {rec.analyst_rating}")
            if rec.upside_percent is not None:
                parts.append(f"Upside: {rec.upside_percent:+.1f}%")
            if rec.target_price:
                parts.append(f"Kursziel: ${rec.target_price:.2f}")
            if rec.tags:
                parts.append(f"Tags: {', '.join(rec.tags)}")
            stock_lines.append(" | ".join(parts))

        prompt = (
            "Du bist ein erfahrener Tech-Aktienanalyst mit Fokus auf fundamentale "
            "und wettbewerbsbasierte Analyse. Analysiere die folgenden Tech-Aktien "
            "und erstelle für jede eine kompakte aber fundierte Investment-Einschätzung "
            "auf Deutsch.\n\n"
            "Berücksichtige dabei:\n"
            "- Bull-Case: Was spricht für die Aktie? (Wachstumstreiber, Marktposition)\n"
            "- Bear-Case: Was sind die größten Risiken? (Bewertung, Wettbewerb, Regulierung)\n"
            "- Wettbewerber: Wie steht die Aktie im Vergleich zu Peers?\n"
            "- Fazit: Kurze, klare Einschätzung\n\n"
            "Aktien:\n"
            + "\n".join(stock_lines)
            + "\n\n"
            "WICHTIG: Antworte NUR im folgenden Format, eine Zeile pro Aktie. "
            "Jede Analyse soll 2-3 Sätze lang sein (max 300 Zeichen):\n\n"
            "TICKER: Deine fundierte Investment-Analyse\n\n"
            "Beispiel:\n"
            "NVDA: Bull: KI-Chip-Monopol mit 80% Datacenter-GPU-Markt, Umsatz +120% YoY. "
            "Bear: P/E >60 preist Perfektion ein, AMD/Custom-Chips als Risiko. "
            "Fazit: Qualitätstitel, aber Einstieg erst bei Korrektur.\n"
            "CRWD: Bull: #1 Endpoint-Security, 35% ARR-Wachstum, hohe Switching-Costs. "
            "Bear: Bewertung >80x FCF, Konkurrenz durch Microsoft Defender wächst. "
            "Fazit: Premium-Bewertung für Premium-Wachstum gerechtfertigt."
        )

        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=get_grounded_config(),
        )

        if not response.text:
            logger.warning("Gemini: Leere Antwort für Tech-Radar")
            return recommendations

        # Response parsen: "TICKER: Analyse" Format
        ai_map = _parse_ai_response(response.text)

        # ai_summary in Empfehlungen schreiben
        enriched = 0
        for rec in recommendations:
            if rec.ticker in ai_map:
                rec.ai_summary = ai_map[rec.ticker]
                enriched += 1

        logger.info(f"🤖 Tech-Radar AI (2.5-Pro): {enriched}/{len(recommendations)} Empfehlungen analysiert")
        return recommendations

    except Exception as e:
        logger.error(f"Tech-Radar AI fehlgeschlagen: {e}")
        return recommendations


def _parse_ai_response(text: str) -> dict[str, str]:
    """Parst Gemini-Antwort im Format 'TICKER: Analyse' in ein Dict.

    Unterstützt mehrzeilige Antworten: Zeilen ohne 'TICKER:' Prefix
    werden an die vorherige Analyse angehängt.
    """
    result = {}
    current_ticker = None

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Markdown-Prefix entfernen (*, -, 1., etc.)
        clean = line.lstrip("*- ")
        if clean and clean[0].isdigit():
            clean = clean.lstrip("0123456789. ")

        # Prüfe ob Zeile mit TICKER: beginnt
        if ":" in clean:
            potential_ticker = clean.partition(":")[0].strip().upper()
            if potential_ticker and len(potential_ticker) <= 6 and potential_ticker.isalpha():
                thesis = clean.partition(":")[2].strip().lstrip("*- ")
                if thesis:
                    result[potential_ticker] = thesis
                    current_ticker = potential_ticker
                    continue

        # Fortsetzungszeile: An vorherigen Ticker anhängen
        if current_ticker and current_ticker in result and clean:
            result[current_ticker] += " " + clean.lstrip("*- ")

    # Auf max 500 Zeichen begrenzen
    for ticker in result:
        result[ticker] = result[ticker][:500]

    return result
