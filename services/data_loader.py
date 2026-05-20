"""PortfolioPilot - Data Loader (C1 Refactoring)

Lädt Daten für eine einzelne Position aus allen Quellen parallel:
  - FMP (Fundamentals, Analyst, Rating, Profile)
  - yFinance (Insider, ESG, Earnings, Fundamentals-Fallback)
  - Technical Indicators (RSI, SMA, Momentum)

Extrahiert aus services/refresh.py für bessere Modularität.
"""
import asyncio
import logging
from typing import Optional

from models import (
    AnalystData,
    DataSourceStatus,
    FearGreedData,
    FundamentalData,
    PortfolioPosition,
    StockFullData,
    TechnicalIndicators,
)
from state import YFINANCE_ALIASES

import yfinance as yf

logger = logging.getLogger(__name__)


async def load_position_data(
    pos: PortfolioPosition,
    fear_greed_data: Optional[FearGreedData] = None,
) -> StockFullData:
    """Lädt alle Daten für eine einzelne Position parallel.

    Orchestriert FMP, yFinance und Technical Indicators,
    berechnet den Score und gibt StockFullData zurück.

    Args:
        pos: Portfolio-Position
        fear_greed_data: Aktueller Fear&Greed Index

    Returns:
        StockFullData mit allen verfügbaren Daten und Score
    """
    from fetchers.fmp import fetch_all_fmp_data
    from fetchers.technical import fetch_technical_indicators
    from fetchers.yfinance_data import fetch_yfinance_data
    from engine.scorer import calculate_score

    # CASH braucht kein Scoring
    if pos.ticker == "CASH":
        return StockFullData(position=pos, data_sources=DataSourceStatus(parqet=True))

    if pos.asset_type == "prediction_market":
        return _build_prediction_market_position(pos)

    ds = DataSourceStatus(fear_greed=fear_greed_data is not None)
    try:
        # Alle Quellen parallel laden
        tasks = {
            "fmp": fetch_all_fmp_data(pos.ticker),
            "yfinance": fetch_yfinance_data(pos.ticker),
            "technical": fetch_technical_indicators(pos.ticker),
        }

        keys = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = {}
        for i, key in enumerate(keys):
            if isinstance(results[i], Exception):
                logger.warning(f"{key} fehlgeschlagen für {pos.ticker}: {results[i]}")
                data[key] = None
            else:
                data[key] = results[i]

        # FMP-Daten extrahieren
        fmp_data = data.get("fmp") or {}
        fund = fmp_data.get("fundamentals")
        analyst = fmp_data.get("analyst")
        fmp_rat = fmp_data.get("fmp_rating")
        profile = fmp_data.get("profile")

        # Track Record: Analyst-Ratings mit historischen Kursen bewerten
        analyst = await _enrich_analyst_track_record(pos.ticker, analyst)

        # Position mit Profile-Daten anreichern
        _enrich_position_from_profile(pos, profile)

        # yFinance Preis-Fallback
        await _yfinance_price_fallback(pos)

        # Weitere Datenquellen extrahieren
        tech_data = data.get("technical")
        yf_data = data.get("yfinance")

        # yFinance Fundamentals Fallback (returns fund, analyst, esg_risk_score)
        fund, analyst, esg_fallback = await _yfinance_fundamentals_fallback(pos, fund, analyst)

        # Merge ESG aus Fundamentals-Fallback in yfinance-Daten
        if esg_fallback is not None and yf_data and yf_data.esg_risk_score is None:
            yf_data.esg_risk_score = esg_fallback

        # Track data source status
        ds.fmp = fund is not None and any([
            fund.pe_ratio, fund.roe, fund.gross_margin,
            fund.debt_to_equity, fund.market_cap
        ]) if fund else False
        ds.technical = tech_data is not None and tech_data.rsi_14 is not None
        ds.yfinance = yf_data is not None and (
            yf_data.recommendation_trend is not None or
            yf_data.esg_risk_score is not None or
            yf_data.insider_buy_count > 0 or
            yf_data.earnings_growth_yoy is not None
        )

        score = calculate_score(
            ticker=pos.ticker,
            name=pos.name,
            fundamentals=fund,
            analyst=analyst,
            current_price=pos.current_price,
            fmp_rating=fmp_rat,
            yfinance_data=yf_data,
            fear_greed=fear_greed_data,
            technical=tech_data,
            sector=pos.sector or "",
            asset_type=pos.asset_type,
            pnl_percent=pos.pnl_percent,
            daily_change_pct=pos.daily_change_pct,
        )

        return StockFullData(
            position=pos,
            fundamentals=fund,
            analyst=analyst,
            technical=tech_data,
            yfinance=yf_data,
            fmp_rating=fmp_rat,
            score=score,
            data_sources=ds,
        )
    except Exception as e:
        logger.exception(f"FATAL Exception bei {pos.ticker}: {e}")
        return StockFullData(position=pos, data_sources=ds)


def _build_prediction_market_position(pos: PortfolioPosition) -> StockFullData:
    """Baut eine minimal analysierbare Prediction-Market-Position.

    Für Polymarket-Kontrakte existiert in diesem Projekt kein Fundamentaldaten-
    Feed. Wir erzeugen deshalb einen transparent vereinfachten Score, damit die
    Position im Dashboard, in Reports und in der Analyse sichtbar bleibt.
    """
    from engine.scorer import calculate_score

    score = calculate_score(
        ticker=pos.ticker,
        name=pos.name,
        fundamentals=None,
        analyst=None,
        current_price=pos.current_price,
        fear_greed=None,
        technical=None,
        asset_type=pos.asset_type,
        pnl_percent=pos.pnl_percent,
        daily_change_pct=pos.daily_change_pct,
        sector=pos.sector or "Prediction Market",
    )
    return StockFullData(
        position=pos,
        score=score,
        data_sources=DataSourceStatus(parqet=True),
    )


async def load_positions_batched(
    positions: list[PortfolioPosition],
    fear_greed_data: Optional[FearGreedData] = None,
    batch_size: int = 2,
) -> list[StockFullData]:
    """Lädt Daten für alle Positionen in Batches.

    Args:
        positions: Liste von Positionen
        fear_greed_data: Aktueller Fear&Greed Index
        batch_size: Anzahl paralleler Positionen pro Batch (4 für optimale FMP-Nutzung)

    Returns:
        Liste von StockFullData
    """
    stocks = []
    num_batches = (len(positions) + batch_size - 1) // batch_size

    for i in range(0, len(positions), batch_size):
        batch = positions[i:i + batch_size]
        logger.info(f"Batch {i // batch_size + 1}/{num_batches}: {[p.ticker for p in batch]}")
        results = await asyncio.gather(*[
            load_position_data(p, fear_greed_data) for p in batch
        ])
        stocks.extend(results)
        # Pause between batches to respect FMP rate limits
        if i + batch_size < len(positions):
            await asyncio.sleep(1.5)  # 1.5s reicht für FMP Free Tier (~4 req/s)

    # Flush all caches to disk (batch-level statt per-ticker)
    try:
        from fetchers.fmp import flush_cache as flush_fmp
        flush_fmp()
    except Exception:
        pass
    try:
        from cache_manager import CacheManager
        for name in ("yfinance", "technical"):
            if name in CacheManager._registry:
                CacheManager._registry[name].flush()
    except Exception:
        pass

    return stocks


# ─────────────────────────────────────────────────────────────
# Hilfsfunktionen (aus refresh.py extrahiert)
# ─────────────────────────────────────────────────────────────

async def _enrich_analyst_track_record(ticker: str, analyst) -> Optional[AnalystData]:
    """Reichert Analyst-Daten mit Track-Record an."""
    if analyst and analyst.individual_ratings:
        try:
            from fetchers.fmp import get_historical_prices
            from services.analyst_tracker import enrich_analyst_data
            hist = await get_historical_prices(ticker, period="1year")
            if hist:
                analyst = enrich_analyst_data(analyst, hist)
        except Exception as e:
            logger.debug(f"Analyst Track Record fehlgeschlagen für {ticker}: {e}")
    return analyst


def _enrich_position_from_profile(pos: PortfolioPosition, profile: Optional[dict]):
    """Reichert Position mit FMP-Profile-Daten an (Name, Sektor)."""
    if not profile:
        return

    original_price = pos.current_price
    original_currency = pos.price_currency

    if not pos.name or pos.name == pos.ticker:
        pos.name = profile.get("companyName", pos.name)
    pos.sector = profile.get("sector", pos.sector) or pos.sector

    # Preis nur setzen wenn noch keiner vorhanden
    if original_price <= 0:
        fmp_price = profile.get("price")
        if fmp_price and float(fmp_price) > 0:
            pos.current_price = float(fmp_price)
            fmp_currency = profile.get("currency")
            if fmp_currency:
                pos.price_currency = fmp_currency

    # Preis restaurieren wenn vorher vorhanden
    if original_price > 0:
        pos.current_price = original_price
        pos.price_currency = original_currency


async def _yfinance_price_fallback(pos: PortfolioPosition):
    """yFinance als Fallback wenn kein Preis vorhanden."""
    if pos.current_price > 0:
        return

    def _fetch_price():
        yf_ticker = YFINANCE_ALIASES.get(pos.ticker, pos.ticker)
        ticker_obj = yf.Ticker(yf_ticker)
        return ticker_obj.info or {}

    try:
        info = await asyncio.wait_for(asyncio.to_thread(_fetch_price), timeout=5.0)
        yf_price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if yf_price and float(yf_price) > 0:
            pos.current_price = float(yf_price)
            pos.price_currency = info.get("currency", "USD")
            if not pos.name or pos.name == pos.ticker:
                pos.name = info.get("shortName", pos.name)
            if not pos.sector or pos.sector == "Unknown":
                pos.sector = info.get("sector", pos.sector)
    except asyncio.TimeoutError:
        logger.debug(f"yFinance Preis-Fallback Timeout für {pos.ticker}")
    except Exception as e:
        logger.debug(f"yFinance Preis-Fallback fehlgeschlagen für {pos.ticker}: {e}")


async def _yfinance_fundamentals_fallback(pos, fund, analyst):
    """Fundamentals + Analyst-Daten von yfinance wenn FMP nicht liefert."""
    fund_has_data = fund and any([
        fund.pe_ratio, fund.roe, fund.gross_margin,
        fund.debt_to_equity, fund.market_cap
    ])
    esg_fallback = None
    if fund_has_data:
        return fund, analyst, esg_fallback

    try:
        from fetchers.yfinance_data import fetch_yfinance_fundamentals
        yf_fund = await fetch_yfinance_fundamentals(pos.ticker)
        if yf_fund:
            yf_fd = yf_fund.get("fundamentals")
            if yf_fd and any([yf_fd.pe_ratio, yf_fd.roe, yf_fd.gross_margin]):
                fund = yf_fd
                logger.info(f"yFinance Fundamentals-Fallback fuer {pos.ticker}")
            # Analyst-Daten auffüllen
            analyst_has_data = analyst and (analyst.num_analysts > 0 or analyst.target_price)
            if not analyst_has_data:
                yf_ad = yf_fund.get("analyst")
                if yf_ad and (yf_ad.num_analysts > 0 or yf_ad.target_price):
                    analyst = yf_ad
                    logger.info(f"yFinance Analyst-Fallback fuer {pos.ticker}")
            # ESG-Score aus ticker.info (wird in fetch_yfinance_fundamentals extrahiert)
            esg_fallback = yf_fund.get("esg_risk_score")
            # Name und Sektor
            if not pos.name or pos.name == pos.ticker:
                yf_name = yf_fund.get("name")
                if yf_name:
                    pos.name = yf_name
            if not pos.sector or pos.sector == "Unknown":
                yf_sector = yf_fund.get("sector")
                if yf_sector:
                    pos.sector = yf_sector
    except Exception as e:
        logger.debug(f"yFinance Fundamentals-Fallback fehlgeschlagen fuer {pos.ticker}: {e}")

    return fund, analyst, esg_fallback
