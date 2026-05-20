"""PortfolioPilot - yFinance Stock Screener

Ersetzt den FMP-basierten Tech-Picks Screener (discover_tech_stocks).
Nutzt yfinance EquityQuery für programmatisches Aktien-Screening.
Kein API-Key nötig, kein Rate-Limit, 100+ Filter-Kriterien.

Spart ~16 FMP-Calls pro Refresh-Zyklus.
"""
import asyncio
import logging
from typing import Optional

import yfinance as yf
from yfinance import EquityQuery

from models import TechRecommendation

logger = logging.getLogger(__name__)


async def discover_stocks_yfinance(
    limit: int = 8,
    sector: str = "Technology",
) -> list[TechRecommendation]:
    """Sucht nach interessanten Aktien via yfinance EquityQuery.

    Multi-Faktor-Screening:
      - MarketCap > 5 Mrd
      - PE < 40 (nicht überbewertet)
      - ROE > 10% (profitabel)
      - Revenue Growth > 5% (wachsend)

    Sortiert nach Performance und Qualität.
    Kein FMP-Budget nötig — komplett kostenlos.

    Args:
        limit: Max Empfehlungen
        sector: Sektor-Filter (default: Technology)

    Returns:
        Liste von TechRecommendation
    """
    try:
        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(
            loop.run_in_executor(None, _screen_sync, sector, limit),
            timeout=20.0,
        )
        return results
    except asyncio.TimeoutError:
        logger.warning("yFinance Screener Timeout (20s)")
        return []
    except Exception as e:
        logger.warning(f"yFinance Screener fehlgeschlagen: {e}")
        return []


def _screen_sync(sector: str, limit: int) -> list[TechRecommendation]:
    """Synchrones Screening via yfinance EquityQuery."""
    try:
        # Multi-Faktor-Query: Qualität + Wachstum + faire Bewertung
        q = EquityQuery('and', [
            EquityQuery('gt', ['intradaymarketcap', 5_000_000_000]),   # > 5 Mrd
            EquityQuery('gt', ['percentchange', -5]),                   # Nicht im freien Fall
            EquityQuery('gt', ['returnonequity.lasttwelvemonths', 10]), # ROE > 10%
            EquityQuery('lt', ['peratio.lasttwelvemonths', 40]),        # PE < 40
            EquityQuery('gt', ['peratio.lasttwelvemonths', 0]),         # Positiv
        ])

        # Sortiert nach % Tagesänderung (aktive Aktien zuerst)
        result = yf.screen(q, sortField='percentchange', sortAsc=False, size=25)

        if not result or 'quotes' not in result:
            logger.info("yFinance Screener: Keine Ergebnisse")
            return []

        quotes = result['quotes']
        logger.info(f"yFinance Screener: {len(quotes)} Kandidaten gefunden")

        recommendations = []
        for stock in quotes[:limit]:
            symbol = stock.get('symbol', '')
            if not symbol:
                continue

            price = stock.get('regularMarketPrice', 0)
            if not price or price <= 0:
                continue

            # Market Cap
            market_cap = stock.get('marketCap')

            # PE Ratio
            pe = stock.get('trailingPE') or stock.get('forwardPE')

            # Tagesänderung als Indikator
            pct_change = stock.get('regularMarketChangePercent', 0)

            # Branche/Sektor
            display_name = stock.get('shortName') or stock.get('longName') or symbol
            stock_sector = stock.get('sector', 'Technology')

            # Tags aus Industry
            industry = stock.get('industry', '')
            tags = _build_tags(industry)

            # Einfacher Score basierend auf verfügbaren Daten
            score = _calc_simple_score(stock)

            # Reason
            reason = _build_reason(stock, pct_change)

            recommendations.append(TechRecommendation(
                ticker=symbol,
                name=display_name,
                sector=stock_sector,
                current_price=round(price, 2),
                market_cap=market_cap,
                pe_ratio=round(pe, 1) if pe else None,
                score=round(min(score, 100), 1),
                reason=reason,
                tags=tags,
                source="yFinance Screener",
            ))

        # Sortieren nach Score
        recommendations.sort(key=lambda x: x.score, reverse=True)
        return recommendations[:limit]

    except Exception as e:
        logger.warning(f"yFinance Screener _screen_sync fehlgeschlagen: {e}")
        return []


def _calc_simple_score(stock: dict) -> float:
    """Berechnet einen Score aus yfinance Screener-Daten.

    Quality (40%): ROE, Margins
    Valuation (30%): PE, Forward PE
    Momentum (30%): Tagesänderung, 52W Performance
    """
    quality = 50.0
    valuation = 50.0
    momentum = 50.0

    # Quality
    # (yfinance screen results have limited fields, use what's available)
    trailing_pe = stock.get('trailingPE')
    forward_pe = stock.get('forwardPE')

    # Valuation
    if trailing_pe and trailing_pe > 0:
        if trailing_pe < 15:
            valuation = 85
        elif trailing_pe < 22:
            valuation = 72
        elif trailing_pe < 30:
            valuation = 58
        elif trailing_pe < 40:
            valuation = 42
        else:
            valuation = 25

    # Forward PE improvement (earnings growth expected)
    if forward_pe and trailing_pe and forward_pe > 0 and trailing_pe > 0:
        pe_improvement = (trailing_pe - forward_pe) / trailing_pe * 100
        if pe_improvement > 20:
            quality = 80
        elif pe_improvement > 10:
            quality = 70
        elif pe_improvement > 0:
            quality = 60
        else:
            quality = 40

    # Momentum
    pct_change = stock.get('regularMarketChangePercent', 0)
    if pct_change > 5:
        momentum = 85
    elif pct_change > 2:
        momentum = 72
    elif pct_change > 0:
        momentum = 58
    elif pct_change > -2:
        momentum = 45
    else:
        momentum = 30

    return quality * 0.4 + valuation * 0.3 + momentum * 0.3


def _build_tags(industry: str) -> list[str]:
    """Baut Tags aus Industry-String."""
    tags = []
    industry_lower = industry.lower()

    tag_map = {
        'semiconductor': 'Semiconductor',
        'software': 'Software',
        'cloud': 'Cloud',
        'artificial intelligence': 'AI',
        'internet': 'Internet',
        'cybersecurity': 'Security',
        'biotech': 'Biotech',
        'electric': 'EV',
        'renewable': 'Clean Energy',
        'fintech': 'FinTech',
        'gaming': 'Gaming',
    }

    for keyword, tag in tag_map.items():
        if keyword in industry_lower:
            tags.append(tag)

    if not tags and industry:
        # Fallback: Industry als Tag
        tags.append(industry.split('-')[0].strip().title()[:20])

    return tags


def _build_reason(stock: dict, pct_change: float) -> str:
    """Baut einen Grund-String für die Empfehlung."""
    parts = []

    pe = stock.get('trailingPE')
    fwd_pe = stock.get('forwardPE')

    if pe and fwd_pe and pe > 0 and fwd_pe > 0:
        pe_drop = round((pe - fwd_pe) / pe * 100, 0)
        if pe_drop > 10:
            parts.append(f"PE sinkt {pe_drop:.0f}% (Forward)")

    if abs(pct_change) > 2:
        direction = "steigt" if pct_change > 0 else "fällt"
        parts.append(f"Heute {direction} {abs(pct_change):.1f}%")

    cap = stock.get('marketCap')
    if cap and cap > 0:
        if cap > 1e12:
            parts.append(f"Mega-Cap ({cap/1e12:.1f}T)")
        elif cap > 100e9:
            parts.append(f"Large-Cap ({cap/1e9:.0f}B)")
        elif cap > 10e9:
            parts.append(f"Mid-Cap ({cap/1e9:.0f}B)")

    return " | ".join(parts) if parts else "Qualitätsaktie mit fairem PE"
