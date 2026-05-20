"""PortfolioPilot - Financial Modeling Prep API Fetcher (Stable API)

Holt Fundamentaldaten, Analyst-Ratings, Bewertungen und Kurse von FMP.
Nutzt die neue Stable API: https://financialmodelingprep.com/stable/
Free Tier: 250 Requests/Tag.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from cache_manager import CacheManager
from config import settings
from models import (
    AnalystData,
    AnalystRating,
    FmpRating,
    FundamentalData,
    PortfolioPosition,
    TechRecommendation,
)

logger = logging.getLogger(__name__)

# Zentraler Cache für alle FMP-Daten
_cache = CacheManager("fmp", ttl_hours=24)


# Track ob wir rate-limited sind (verhindert false-negative Caching)
_rate_limited = False


# Semaphore: maximal 3 gleichzeitige FMP-Requests (verhindert Burst-Overload)
_fmp_semaphore = asyncio.Semaphore(3)

# FMP Usage Tracker (DA4)
_fmp_request_count = 0
_fmp_request_date = None  # Wird beim ersten Request gesetzt


def reset_rate_limit():
    """Setzt Rate-Limiting zurück (z.B. bei neuem Tag oder manuellem Refresh)."""
    global _rate_limited, _fmp_request_count
    _rate_limited = False
    _fmp_request_count = 0
    logger.info("FMP Rate-Limit zurückgesetzt")

# Reusable HTTP-Client mit Connection-Pooling (vermeidet ~160 TCP-Handshakes pro Refresh)
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den wiederverwendbaren HTTP-Client zurück (Lazy-Init)."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


def get_fmp_usage() -> dict:
    """Gibt aktuelle FMP-Nutzungsstatistiken zurück."""
    from datetime import date
    today = date.today().isoformat()
    return {
        "requests_today": _fmp_request_count if _fmp_request_date == today else 0,
        "daily_limit": 250,
        "remaining": max(0, 250 - (_fmp_request_count if _fmp_request_date == today else 0)),
        "rate_limited": _rate_limited,
        "date": today,
    }


async def _fmp_request(endpoint: str, params: Optional[dict] = None) -> Optional[dict | list]:
    """Make a request to the FMP Stable API.

    Stable API Format: /stable/{endpoint}?symbol=AAPL&apikey=...
    Retries 1x on rate limit (429) with 15s backoff.
    When globally rate-limited (Tageslimit erschoepft), ueberspringt sofort.
    Nutzt einen wiederverwendbaren HTTP-Client mit Connection-Pooling.
    """
    global _rate_limited, _fmp_request_count, _fmp_request_date
    if settings.demo_mode:
        return None

    # Rate-Limit bei neuem Tag automatisch zurücksetzen
    from datetime import date
    today = date.today().isoformat()
    if _fmp_request_date and _fmp_request_date != today:
        _rate_limited = False
        _fmp_request_count = 0
        logger.info(f"FMP neuer Tag — Rate-Limit zurückgesetzt")

    # Wenn bereits global rate-limited: sofort ueberspringen
    # (verhindert minutenlanges Warten wenn Tageslimit aufgebraucht)
    if _rate_limited:
        logger.debug(f"FMP uebersprungen (rate-limited): {endpoint}")
        return None

    url = f"{settings.FMP_BASE_URL}/{endpoint}"
    params = params or {}
    params["apikey"] = settings.FMP_API_KEY

    max_retries = 1
    base_delay = 15  # Sekunden

    client = _get_http_client()
    for attempt in range(max_retries + 1):
        try:
            async with _fmp_semaphore:
                response = await client.get(url, params=params)
                response.raise_for_status()
                _rate_limited = False
                # Usage tracking
                from datetime import date
                today = date.today().isoformat()
                if _fmp_request_date != today:
                    _fmp_request_count = 0
                    _fmp_request_date = today
                _fmp_request_count += 1
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                _rate_limited = True
                if attempt < max_retries:
                    logger.info(f"FMP Rate-Limit bei {endpoint} — warte {base_delay}s (Retry)")
                    await asyncio.sleep(base_delay)
                    continue
                else:
                    logger.warning(f"FMP Rate-Limit erreicht: {endpoint} (Tageslimit erschoepft)")
            else:
                logger.error(f"FMP API Fehler [{e.response.status_code}]: {endpoint} - {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"FMP Request fehlgeschlagen: {e}")
            return None
    return None


def _extract_first(data: Optional[dict | list]) -> Optional[dict]:
    """Extrahiert erstes Element wenn Liste, sonst dict direkt."""
    if data is None:
        return None
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


# ─────────────────────────────────────────────────────────────
# Basis-Endpoints (mit Cache + Negative-Caching)
# ─────────────────────────────────────────────────────────────

async def _cached_fmp_call(cache_key: str, endpoint: str, params: dict) -> Optional[dict]:
    """Generischer FMP-Call mit Cache und Negative-Caching.

    Budget-Schutz: Wenn Cache < 6h alt ist, wird kein API-Call gemacht.
    Negative-Caching wird bei Rate-Limits (429) NICHT gesetzt,
    damit beim nächsten Refresh mit frischem Budget erneut gefragt wird.
    """
    # Budget-Schutz: Frische Daten wiederverwenden (< 6h)
    if _cache.is_fresh(cache_key, max_hours=6.0):
        return _cache.get(cache_key)

    cached = _cache.get(cache_key)
    if cached is not None:
        if _cache.is_negative(cache_key):
            return None
        return cached

    data = await _fmp_request(endpoint, params)
    result = _extract_first(data)

    if result:
        _cache.set(cache_key, result)
    elif not _rate_limited:
        # Nur negativ cachen wenn NICHT rate-limited
        _cache.set_negative(cache_key)

    return result


async def get_company_profile(ticker: str) -> Optional[dict]:
    """Holt Company Profile (Name, Sektor, Preis, Marktkapitalisierung)."""
    return await _cached_fmp_call(f"profile_{ticker}", "profile", {"symbol": ticker})


async def get_key_metrics(ticker: str) -> Optional[dict]:
    """Holt Key Metrics TTM (PE, PB, ROE, etc.)."""
    return await _cached_fmp_call(f"metrics_{ticker}", "key-metrics-ttm", {"symbol": ticker})


async def get_financial_ratios(ticker: str) -> Optional[dict]:
    """Holt Financial Ratios TTM."""
    return await _cached_fmp_call(f"ratios_{ticker}", "ratios-ttm", {"symbol": ticker})


async def get_stock_quote(ticker: str) -> Optional[dict]:
    """Holt aktuellen Kurs."""
    return await _cached_fmp_call(f"quote_{ticker}", "quote", {"symbol": ticker})


async def get_rating_snapshot(ticker: str) -> Optional[dict]:
    """Holt FMP Ratings Snapshot (Gesamtrating + Sub-Scores)."""
    return await _cached_fmp_call(f"rating_{ticker}", "rating", {"symbol": ticker})


async def get_financial_scores(ticker: str) -> Optional[dict]:
    """Holt Financial Scores (Altman Z-Score, Piotroski Score)."""
    return await _cached_fmp_call(f"score_{ticker}", "score", {"symbol": ticker})


async def get_financial_growth(ticker: str) -> Optional[dict]:
    """Holt Income Statement Growth (Revenue Growth, Net Income Growth).

    FMP Stable API: /income-statement-growth?symbol=AAPL
    Liefert echte YoY-Wachstumsraten als Dezimalwerte (z.B. 0.08 = 8%).
    """
    return await _cached_fmp_call(
        f"growth_{ticker}", "income-statement-growth", {"symbol": ticker}
    )


async def get_upgrades_downgrades_consensus(ticker: str) -> Optional[dict]:
    """Holt Upgrades & Downgrades Consensus."""
    return await _cached_fmp_call(
        f"ud_consensus_{ticker}", "upgrades-downgrades-consensus", {"symbol": ticker}
    )


async def get_price_target_summary(ticker: str) -> Optional[dict]:
    """Holt Price Target Summary."""
    return await _cached_fmp_call(f"pt_summary_{ticker}", "price-target-summary", {"symbol": ticker})


async def get_upgrades_downgrades(ticker: str) -> Optional[list]:
    """Holt individuelle Analyst Upgrades/Downgrades.

    Liefert eine Liste einzelner Analyst-Aktionen mit Firma, Datum,
    Von-/Bis-Rating. Wird für Track Record Analyse genutzt.
    """
    cache_key = f"ud_{ticker}"
    cached = _cache.get(cache_key)
    if cached is not None:
        if _cache.is_negative(cache_key):
            return None
        return cached

    data = await _fmp_request("upgrades-downgrades", {"symbol": ticker})
    if data and isinstance(data, list):
        _cache.set(cache_key, data)
        return data
    elif not _rate_limited:
        _cache.set_negative(cache_key)
    return None


# ─────────────────────────────────────────────────────────────
# High-Level Aggregations-Funktionen (parallelisiert)
# ─────────────────────────────────────────────────────────────

PERIOD_DAYS = {"1month": 30, "3month": 90, "6month": 180, "1year": 365}


async def get_historical_prices(ticker: str, period: str = "3month") -> list[dict]:
    """Holt historische Schlusskurse für einen Ticker."""
    cache_key = f"history_{ticker}_{period}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    data = await _fmp_request("historical-price-full", {"symbol": ticker})
    if not data:
        return []

    historical = []
    if isinstance(data, dict):
        historical = data.get("historical", [])
    elif isinstance(data, list) and len(data) > 0:
        historical = data[0].get("historical", []) if isinstance(data[0], dict) else []

    if not historical:
        return []

    days = PERIOD_DAYS.get(period, 90)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    result = [
        {"date": h["date"], "close": round(h.get("close", 0), 2)}
        for h in historical
        if h.get("date", "") >= cutoff and h.get("close") is not None
    ]
    result.sort(key=lambda x: x["date"])

    if result:
        _cache.set(cache_key, result)

    return result


async def fetch_dividend_info(ticker: str) -> dict | None:
    """Holt Dividenden-Informationen aus dem Company Profile."""
    profile = await get_company_profile(ticker)
    if not profile:
        return None

    div_yield = profile.get("lastDiv")
    price = profile.get("price", 0)

    if not div_yield or float(div_yield) <= 0:
        return None

    annual_div = float(div_yield)
    yield_pct = (annual_div / price * 100) if price > 0 else None

    return {
        "yield_percent": round(yield_pct, 2) if yield_pct else None,
        "annual_dividend": round(annual_div, 2),
        "ex_date": profile.get("exDividendDate"),
        "payment_date": None,
        "frequency": "Quarterly",
    }


async def fetch_fundamentals(ticker: str) -> FundamentalData:
    """Holt alle Fundamentaldaten für einen Ticker (parallelisiert)."""
    # Alle 4 API-Calls gleichzeitig statt sequentiell
    profile, ratios, metrics, fin_scores = await asyncio.gather(
        get_company_profile(ticker),
        get_financial_ratios(ticker),
        get_key_metrics(ticker),
        get_financial_scores(ticker),
    )

    fd = FundamentalData()

    if profile:
        fd.market_cap = profile.get("mktCap")
        fd.beta = profile.get("beta")

    if ratios:
        fd.pe_ratio = ratios.get("peRatioTTM")
        fd.pb_ratio = ratios.get("priceToBookRatioTTM")
        fd.ps_ratio = ratios.get("priceToSalesRatioTTM")
        fd.roe = ratios.get("returnOnEquityTTM")
        fd.roa = ratios.get("returnOnAssetsTTM")
        fd.debt_to_equity = ratios.get("debtEquityRatioTTM")
        fd.current_ratio = ratios.get("currentRatioTTM")
        fd.gross_margin = ratios.get("grossProfitMarginTTM")
        fd.operating_margin = ratios.get("operatingProfitMarginTTM")
        fd.net_margin = ratios.get("netProfitMarginTTM")
        dy = ratios.get("dividendYielTTM")
        fd.dividend_yield = (dy * 100) if dy is not None else None

    # Wachstum: Echte Growth Rates aus income-statement-growth (nicht per-share absolut)
    growth = await get_financial_growth(ticker)
    if growth:
        fd.revenue_growth = growth.get("growthRevenue")            # Dezimal, z.B. 0.08 = 8%
        fd.earnings_growth = growth.get("growthNetIncome")         # Dezimal, z.B. 0.12 = 12%

    if fin_scores:
        fd.altman_z_score = fin_scores.get("altmanZScore")
        fd.piotroski_score = fin_scores.get("piotroskiScore")

    return fd


async def fetch_analyst_data(ticker: str) -> AnalystData:
    """Holt Analyst-Daten für einen Ticker (parallelisiert)."""
    # Beide Calls gleichzeitig
    consensus_data, pt_data = await asyncio.gather(
        get_upgrades_downgrades_consensus(ticker),
        get_price_target_summary(ticker),
    )

    ad = AnalystData()

    if consensus_data:
        ad.consensus = consensus_data.get("consensus")
        ad.strong_buy_count = consensus_data.get("strongBuy", 0)
        ad.buy_count = consensus_data.get("buy", 0)
        ad.hold_count = consensus_data.get("hold", 0)
        ad.sell_count = consensus_data.get("sell", 0)
        ad.strong_sell_count = consensus_data.get("strongSell", 0)
        ad.num_analysts = (
            ad.strong_buy_count + ad.buy_count + ad.hold_count +
            ad.sell_count + ad.strong_sell_count
        )

    if pt_data:
        ad.target_price = (
            pt_data.get("lastMonthAvgPriceTarget")
            or pt_data.get("lastQuarterAvgPriceTarget")
        )

    return ad


async def fetch_fmp_rating(ticker: str) -> Optional[FmpRating]:
    """Holt FMP Rating Snapshot für einen Ticker."""
    data = await get_rating_snapshot(ticker)
    if not data:
        return None

    return FmpRating(
        rating=data.get("rating", ""),
        rating_score=data.get("ratingScore", 0),
        dcf_score=data.get("ratingDetailsDCFScore", 0),
        roe_score=data.get("ratingDetailsROEScore", 0),
        roa_score=data.get("ratingDetailsROAScore", 0),
        de_score=data.get("ratingDetailsDEScore", 0),
        pe_score=data.get("ratingDetailsPEScore", 0),
        pb_score=data.get("ratingDetailsPBScore", 0),
    )


async def update_position_price(position: PortfolioPosition) -> PortfolioPosition:
    """Aktualisiert den Kurs einer Position (parallelisiert).

    Priorisierung: quote > profile > bestehender Preis.
    Setzt price_currency aus FMP-Profile (EUR, USD, DKK etc.).
    """
    quote, profile = await asyncio.gather(
        get_stock_quote(position.ticker),
        get_company_profile(position.ticker),
    )

    # Profile-Daten zuerst (Sektor, Name, Währung, Preis als Basis)
    if profile:
        position.sector = profile.get("sector", position.sector) or position.sector
        if not position.name or position.name == position.ticker:
            position.name = profile.get("companyName", position.name)
        # Währung aus Profile setzen (kritisch für korrekte Wertberechnung)
        fmp_currency = profile.get("currency")
        if fmp_currency:
            position.price_currency = fmp_currency
        # Profile-Preis als Fallback
        profile_price = profile.get("price")
        if profile_price and float(profile_price) > 0 and position.current_price <= 0:
            position.current_price = float(profile_price)

    # Quote-Daten überschreiben (genauer, wenn verfügbar)
    if quote:
        quote_price = quote.get("price")
        if quote_price and float(quote_price) > 0:
            position.current_price = float(quote_price)
        position.name = quote.get("name", position.name) or position.name

    return position


async def fetch_all_fmp_data(ticker: str) -> dict:
    """Holt ALLE FMP-Daten in einem einzigen flachen asyncio.gather.

    Ersetzt die separaten Aufrufe von:
      - update_position_price() (profile + quote)
      - fetch_fundamentals()    (profile + ratios + metrics + scores)
      - fetch_analyst_data()    (consensus + price_target)
      - fetch_fmp_rating()      (rating)

    Vorher: 9 FMP-Calls pro Ticker (profile 2x + quote)
    Nachher: 7 FMP-Calls pro Ticker (kein Duplikat, kein quote)

    Returns:
        dict mit keys: fundamentals, analyst, fmp_rating, profile
    """
    # Ein einziger flacher Gather — profile wird nur 1x aufgerufen
    profile, ratios, metrics, fin_scores, growth, rating, consensus, pt, ud = await asyncio.gather(
        get_company_profile(ticker),
        get_financial_ratios(ticker),
        get_key_metrics(ticker),
        get_financial_scores(ticker),
        get_financial_growth(ticker),
        get_rating_snapshot(ticker),
        get_upgrades_downgrades_consensus(ticker),
        get_price_target_summary(ticker),
        get_upgrades_downgrades(ticker),
    )

    # --- Build FundamentalData ---
    fd = FundamentalData()
    if profile:
        fd.market_cap = profile.get("mktCap")
        fd.beta = profile.get("beta")
    if ratios:
        fd.pe_ratio = ratios.get("peRatioTTM")
        fd.pb_ratio = ratios.get("priceToBookRatioTTM")
        fd.ps_ratio = ratios.get("priceToSalesRatioTTM")
        fd.roe = ratios.get("returnOnEquityTTM")
        fd.roa = ratios.get("returnOnAssetsTTM")
        fd.debt_to_equity = ratios.get("debtEquityRatioTTM")
        fd.current_ratio = ratios.get("currentRatioTTM")
        fd.gross_margin = ratios.get("grossProfitMarginTTM")
        fd.operating_margin = ratios.get("operatingProfitMarginTTM")
        fd.net_margin = ratios.get("netProfitMarginTTM")
        dy = ratios.get("dividendYielTTM")
        fd.dividend_yield = (dy * 100) if dy is not None else None
    if metrics:
        # v3: Valuation & Growth Kennzahlen (bereits im key-metrics-ttm enthalten)
        fd.ev_to_ebitda = metrics.get("evToEBITDATTM")
        fd.free_cashflow_yield = metrics.get("freeCashFlowYieldTTM")
        fd.roic = metrics.get("returnOnInvestedCapitalTTM")
        # PEG Ratio direkt von FMP (korrekt berechnet)
        fd.peg_ratio = metrics.get("pegRatioTTM")
    # Wachstum: Echte Growth Rates aus income-statement-growth
    if growth:
        fd.revenue_growth = growth.get("growthRevenue")        # Dezimal, z.B. 0.08 = 8%
        fd.earnings_growth = growth.get("growthNetIncome")     # Dezimal, z.B. 0.12 = 12%
    if fin_scores:
        fd.altman_z_score = fin_scores.get("altmanZScore")
        fd.piotroski_score = fin_scores.get("piotroskiScore")

    # --- Build AnalystData ---
    ad = AnalystData()
    if consensus:
        ad.consensus = consensus.get("consensus")
        ad.strong_buy_count = consensus.get("strongBuy", 0)
        ad.buy_count = consensus.get("buy", 0)
        ad.hold_count = consensus.get("hold", 0)
        ad.sell_count = consensus.get("sell", 0)
        ad.strong_sell_count = consensus.get("strongSell", 0)
        ad.num_analysts = (
            ad.strong_buy_count + ad.buy_count + ad.hold_count +
            ad.sell_count + ad.strong_sell_count
        )
    if pt:
        ad.target_price = (
            pt.get("lastMonthAvgPriceTarget")
            or pt.get("lastQuarterAvgPriceTarget")
        )

    # Individuelle Analyst-Ratings aus Upgrades/Downgrades
    if ud and isinstance(ud, list):
        for item in ud[:30]:  # Max 30 Ratings für Performance
            ad.individual_ratings.append(AnalystRating(
                firm=item.get("gradingCompany", ""),
                action=item.get("newGrade", ""),
                from_grade=item.get("previousGrade", ""),
                to_grade=item.get("newGrade", ""),
                date=item.get("publishedDate", "")[:10] if item.get("publishedDate") else "",
                price_at_rating=item.get("priceWhenPosted"),
            ))

    # --- Build FmpRating ---
    fmp_rat = None
    if rating:
        fmp_rat = FmpRating(
            rating=rating.get("rating", ""),
            rating_score=rating.get("ratingScore", 0),
            dcf_score=rating.get("ratingDetailsDCFScore", 0),
            roe_score=rating.get("ratingDetailsROEScore", 0),
            roa_score=rating.get("ratingDetailsROAScore", 0),
            de_score=rating.get("ratingDetailsDEScore", 0),
            pe_score=rating.get("ratingDetailsPEScore", 0),
            pb_score=rating.get("ratingDetailsPBScore", 0),
        )

    return {
        "fundamentals": fd,
        "analyst": ad,
        "fmp_rating": fmp_rat,
        "profile": profile,
    }


async def fetch_light_fmp_data(ticker: str) -> dict:
    """Leichtgewichtige FMP-Daten für Tech Picks Screening.

    Nur 3 Calls statt 8: profile, ratios, price_target.
    Spart ~62% FMP-Budget pro Kandidat.
    """
    profile, ratios, pt = await asyncio.gather(
        get_company_profile(ticker),
        get_financial_ratios(ticker),
        get_price_target_summary(ticker),
    )

    fd = FundamentalData()
    if profile:
        fd.market_cap = profile.get("mktCap")
        fd.beta = profile.get("beta")
    if ratios:
        fd.pe_ratio = ratios.get("peRatioTTM")
        fd.pb_ratio = ratios.get("priceToBookRatioTTM")
        fd.roe = ratios.get("returnOnEquityTTM")
        fd.debt_to_equity = ratios.get("debtEquityRatioTTM")
        fd.gross_margin = ratios.get("grossProfitMarginTTM")
        fd.operating_margin = ratios.get("operatingProfitMarginTTM")
        fd.net_margin = ratios.get("netProfitMarginTTM")
        dy = ratios.get("dividendYielTTM")
        fd.dividend_yield = (dy * 100) if dy is not None else None

    ad = AnalystData()
    if pt:
        ad.target_price = pt.get("targetConsensus")
        ad.num_analysts = (pt.get("targetHigh") is not None) + (pt.get("targetLow") is not None)

    return {
        "fundamentals": fd,
        "analyst": ad,
        "profile": profile,
    }


async def discover_tech_stocks(limit: int = 10) -> list[TechRecommendation]:
    """Sucht nach interessanten Tech-Aktien via FMP Stock Screener.

    Tech-Radar v2: Multi-Faktor-Score statt nur Analyst + Upside.
    Score = Quality (30%) + Growth (30%) + Analyst (25%) + Valuation (15%).

    Optimiert v3: Nutzt fetch_light_fmp_data() (3 statt 8 Calls/Kandidat)
    und analysiert nur Top 5 statt 15. Spart ~105 FMP-Requests.
    """
    if settings.demo_mode:
        return []

    params = {
        "sector": "Technology",
        "marketCapMoreThan": 5_000_000_000,
        "limit": 20,
    }

    data = await _fmp_request("stock-screener", params)
    if not data or not isinstance(data, list):
        return []

    # Top-5 Kandidaten mit Light-Fetch analysieren (3 Calls statt 8)
    candidates = data[:5]
    tickers = [s.get("symbol", "") for s in candidates if s.get("symbol")]

    fmp_tasks = [fetch_light_fmp_data(t) for t in tickers]
    all_fmp = await asyncio.gather(*fmp_tasks, return_exceptions=True)

    recommendations = []
    for i, stock in enumerate(candidates):
        ticker = stock.get("symbol", "")
        if not ticker or i >= len(tickers):
            continue

        fmp_data = all_fmp[i] if i < len(all_fmp) and not isinstance(all_fmp[i], Exception) else {}
        if isinstance(fmp_data, Exception):
            fmp_data = {}

        fund = fmp_data.get("fundamentals")
        analyst_data = fmp_data.get("analyst")
        profile = fmp_data.get("profile")

        price = (profile or {}).get("price", 0)
        if not price or price <= 0:
            continue

        # Preisziel
        target = None
        if analyst_data and analyst_data.target_price:
            target = analyst_data.target_price
        upside = None
        if target and price > 0:
            upside = ((target - price) / price) * 100

        # Fundamentals extrahieren
        roe_val = None
        rev_growth_val = None
        gross_margin_val = None
        op_margin_val = None

        if fund:
            roe_val = _normalize_pct_value(fund.roe)
            rev_growth_val = _normalize_pct_value(fund.revenue_growth)
            gross_margin_val = _normalize_pct_value(fund.gross_margin)
            op_margin_val = _normalize_pct_value(fund.operating_margin)

        # Multi-Faktor-Score berechnen
        score = _calc_tech_radar_score(
            roe=roe_val,
            gross_margin=gross_margin_val,
            op_margin=op_margin_val,
            revenue_growth=rev_growth_val,
            analyst_data=analyst_data,
            upside=upside,
        )

        # Tags
        industry = (profile or {}).get("industry", "")
        tags = _build_tech_tags(industry)

        # Analyst Consensus
        analyst_consensus = None
        if analyst_data:
            analyst_consensus = analyst_data.consensus

        # Detaillierter Grund
        reason = _build_reason(analyst_data, upside, profile, fund)

        recommendations.append(TechRecommendation(
            ticker=ticker,
            name=stock.get("companyName", ticker),
            sector="Technology",
            current_price=price,
            market_cap=stock.get("marketCap"),
            pe_ratio=(profile or {}).get("pe"),
            analyst_rating=analyst_consensus,
            target_price=target,
            upside_percent=round(upside, 1) if upside else None,
            score=round(min(score, 100), 1),
            reason=reason,
            tags=tags,
            revenue_growth=round(rev_growth_val, 1) if rev_growth_val is not None else None,
            roe=round(roe_val, 1) if roe_val is not None else None,
            source="PortfolioPilot Tech-Radar",
        ))

    recommendations.sort(key=lambda x: x.score, reverse=True)

    # Flush cache nach Tech-Picks (alle Daten auf Disk schreiben)
    _cache.flush()

    return recommendations[:limit]


def _normalize_pct_value(val: float | None) -> float | None:
    """Normalisiert Prozentwerte: 0.25 -> 25, 25 -> 25."""
    if val is None:
        return None
    if abs(val) < 5:
        return val * 100
    return val


def _calc_tech_radar_score(
    roe: float | None = None,
    gross_margin: float | None = None,
    op_margin: float | None = None,
    revenue_growth: float | None = None,
    analyst_data: AnalystData | None = None,
    upside: float | None = None,
) -> float:
    """Multi-Faktor-Score für Tech-Radar Empfehlungen.

    Quality (30%): ROE, Gross Margin, Operating Margin
    Growth (30%): Revenue Growth
    Analyst (25%): Buy-Ratio, Consensus
    Valuation (15%): Upside zum Preisziel
    """
    quality_score = 50.0
    growth_score = 50.0
    analyst_score = 50.0
    valuation_score = 50.0

    # --- Quality (30%) ---
    quality_parts = []
    if roe is not None:
        if roe > 30: quality_parts.append(90)
        elif roe > 20: quality_parts.append(75)
        elif roe > 12: quality_parts.append(60)
        elif roe > 0: quality_parts.append(40)
        else: quality_parts.append(20)

    if gross_margin is not None:
        if gross_margin > 70: quality_parts.append(90)
        elif gross_margin > 50: quality_parts.append(75)
        elif gross_margin > 35: quality_parts.append(55)
        else: quality_parts.append(30)

    if op_margin is not None:
        if op_margin > 30: quality_parts.append(90)
        elif op_margin > 20: quality_parts.append(75)
        elif op_margin > 10: quality_parts.append(55)
        elif op_margin > 0: quality_parts.append(35)
        else: quality_parts.append(15)

    if quality_parts:
        quality_score = sum(quality_parts) / len(quality_parts)

    # --- Growth (30%) ---
    if revenue_growth is not None:
        if revenue_growth > 40: growth_score = 95
        elif revenue_growth > 25: growth_score = 82
        elif revenue_growth > 15: growth_score = 70
        elif revenue_growth > 8: growth_score = 58
        elif revenue_growth > 0: growth_score = 42
        elif revenue_growth > -10: growth_score = 28
        else: growth_score = 15

    # --- Analyst (25%) ---
    if analyst_data:
        total = (
            analyst_data.strong_buy_count + analyst_data.buy_count +
            analyst_data.hold_count +
            analyst_data.sell_count + analyst_data.strong_sell_count
        )
        if total > 0:
            buy_count = analyst_data.strong_buy_count + analyst_data.buy_count
            buy_ratio = buy_count / total
            analyst_score = 30 + buy_ratio * 65  # 30-95 Bereich
            # Bonus für starken Konsens
            if buy_ratio > 0.85:
                analyst_score = min(100, analyst_score * 1.05)
        elif analyst_data.consensus:
            c = analyst_data.consensus.lower()
            if c in ("strong_buy", "strongbuy", "strong buy"):
                analyst_score = 92
            elif c in ("buy", "outperform", "overweight"):
                analyst_score = 78
            elif c in ("hold", "neutral"):
                analyst_score = 50
            elif c in ("sell", "underperform"):
                analyst_score = 22

    # --- Valuation (15%) ---
    if upside is not None:
        if upside > 30: valuation_score = 95
        elif upside > 20: valuation_score = 82
        elif upside > 10: valuation_score = 68
        elif upside > 0: valuation_score = 55
        elif upside > -10: valuation_score = 38
        else: valuation_score = 18

    # Gewichteter Score
    total_score = (
        quality_score * 0.30 +
        growth_score * 0.30 +
        analyst_score * 0.25 +
        valuation_score * 0.15
    )

    return total_score


# Industrie-Keyword → Tag Mapping
_INDUSTRY_TAGS = {
    "semiconductor": "Semiconductor",
    "software": "Software",
    "cloud": "Cloud",
    "internet": "Cloud",
    "saas": "SaaS",
    "cybersecurity": "Cybersecurity",
    "security": "Cybersecurity",
    "artificial intelligence": "AI",
    " ai ": "AI",
    "machine learning": "AI",
    "fintech": "Fintech",
    "payment": "Fintech",
    "electric vehicle": "EV",
    "gaming": "Gaming",
    "e-commerce": "E-Commerce",
    "data": "Data",
    "analytics": "Data",
    "biotech": "Biotech",
    "hardware": "Hardware",
}


def _build_tech_tags(industry: str) -> list[str]:
    """Erstellt Tags basierend auf der Industrie-Bezeichnung."""
    tags = ["Tech"]
    if not industry:
        return tags

    industry_lower = industry.lower()
    seen = set()
    for keyword, tag in _INDUSTRY_TAGS.items():
        if keyword in industry_lower and tag not in seen:
            tags.append(tag)
            seen.add(tag)

    return tags


def _build_reason(
    analyst_data: AnalystData | None,
    upside: float | None,
    profile: dict | None,
    fund: FundamentalData | None = None,
) -> str:
    """Build a recommendation reason string with fundamentals."""
    parts = []

    # Fundamentals zuerst (wichtigste Info)
    if fund:
        roe = _normalize_pct_value(fund.roe)
        if roe is not None and roe > 10:
            parts.append(f"ROE {roe:.0f}%")
        rev_g = _normalize_pct_value(fund.revenue_growth)
        if rev_g is not None:
            parts.append(f"Revenue {'+'if rev_g>0 else ''}{rev_g:.0f}%")
        gm = _normalize_pct_value(fund.gross_margin)
        if gm is not None and gm > 40:
            parts.append(f"Marge {gm:.0f}%")

    if analyst_data and analyst_data.consensus:
        parts.append(f"Konsens: {analyst_data.consensus}")
    if upside is not None:
        direction = "Upside" if upside > 0 else "Downside"
        parts.append(f"{direction}: {abs(upside):.1f}%")
    if profile and profile.get("industry"):
        parts.append(f"{profile['industry']}")
    return " | ".join(parts) if parts else "Keine Details verfügbar"


async def fetch_earnings_calendar(tickers: list[str]) -> list[dict]:
    """Holt Earnings-Termine für eine Liste von Tickern.

    Returns:
        [{ticker, date, eps_estimated, eps_actual, revenue_estimated, revenue_actual}]
    """
    if settings.demo_mode or not tickers:
        return []

    results = []
    for ticker in tickers:
        cache_key = f"earnings_{ticker}"
        cached = _cache.get(cache_key)
        if cached is not None:
            if not _cache.is_negative(cache_key):
                results.extend(cached if isinstance(cached, list) else [cached])
            continue

        data = await _fmp_request("earning_calendar", {"symbol": ticker})
        if data and isinstance(data, list):
            entries = []
            for item in data[:2]:  # Nur nächste 2 Termine
                entries.append({
                    "ticker": ticker,
                    "date": item.get("date", ""),
                    "eps_estimated": item.get("epsEstimated"),
                    "revenue_estimated": item.get("revenueEstimated"),
                    "fiscal_period": item.get("fiscalDateEnding", ""),
                })
            _cache.set(cache_key, entries)
            results.extend(entries)
        elif not _rate_limited:
            _cache.set_negative(cache_key)

    # Sortiere nach Datum
    results.sort(key=lambda x: x.get("date", ""))
    return results


async def fetch_stock_news(ticker: str, limit: int = 5) -> list[dict]:
    """Holt aktuelle News für einen Ticker.

    Returns:
        [{title, url, published_date, site, snippet, sentiment}]
    """
    if settings.demo_mode:
        return []

    cache_key = f"news_{ticker}"
    cached = _cache.get(cache_key)
    if cached is not None:
        if _cache.is_negative(cache_key):
            return []
        return cached

    data = await _fmp_request("stock_news", {"tickers": ticker, "limit": limit})
    if not data or not isinstance(data, list):
        if not _rate_limited:
            _cache.set_negative(cache_key)
        return []

    results = []
    for item in data[:limit]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "published_date": item.get("publishedDate", ""),
            "site": item.get("site", ""),
            "snippet": (item.get("text", "") or "")[:200],
            "image": item.get("image", ""),
            "sentiment": item.get("sentiment", "Neutral"),
        })

    _cache.set(cache_key, results)
    return results


def flush_cache():
    """Schreibt den FMP Cache auf Disk."""
    _cache.flush()


def clear_cache():
    """Löscht den FMP Cache."""
    _cache.clear()
