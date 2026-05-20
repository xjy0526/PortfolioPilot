"""PortfolioPilot - Yahoo Finance Fetcher

Holt Analyst Recommendations, Insider-Transaktionen, ESG Risk Scores
und Earnings/Dividenden-Daten über die yfinance Library.
Kein API-Key nötig, keine offiziellen Rate Limits.

Optimierung: 5s Timeout pro Ticker, ISIN-Symbole werden übersprungen.
"""
import asyncio
import concurrent.futures
import logging
import re
from typing import Optional

from cache_manager import CacheManager
from config import settings

logger = logging.getLogger(__name__)

_cache = CacheManager("yfinance", ttl_hours=24)

# Reusable thread pool (avoid per-call overhead)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf")

# Pattern to detect ISINs (12 chars, 2 letter country + 10 alphanumeric)
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")


def _is_valid_ticker(symbol: str) -> bool:
    """Check if symbol looks like a real ticker (not an ISIN)."""
    if not symbol:
        return False
    if _ISIN_PATTERN.match(symbol):
        return False
    return len(symbol) <= 10


async def fetch_yfinance_data(ticker_symbol: str):
    """Holt alle relevanten Yahoo Finance Daten für einen Ticker."""
    from models import YFinanceData

    # Skip ISINs – yfinance kann damit nichts anfangen
    if not _is_valid_ticker(ticker_symbol):
        logger.debug(f"yfinance: Überspringe ISIN/ungültigen Ticker: {ticker_symbol}")
        return YFinanceData()

    cache_key = f"yf_{ticker_symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return YFinanceData(**cached)

    try:
        loop = asyncio.get_running_loop()
        # 8s Timeout für den gesamten yfinance-Call
        data = await asyncio.wait_for(
            loop.run_in_executor(_executor, _fetch_yf_sync, ticker_symbol),
            timeout=8.0
        )
        if data:
            _cache.set(cache_key, data.model_dump())
            return data  # flush am Batch-Ende in data_loader
    except asyncio.TimeoutError:
        logger.debug(f"yfinance Timeout für {ticker_symbol}")
    except Exception as e:
        logger.debug(f"yfinance fehlgeschlagen für {ticker_symbol}: {e}")

    return YFinanceData()


def _fetch_yf_sync(ticker_symbol: str):
    """Synchrone yfinance Datenabfrage mit kurzen Timeouts."""
    from models import YFinanceData
    import yfinance as yf

    try:
        ticker = yf.Ticker(ticker_symbol)
    except Exception:
        return YFinanceData()

    result = YFinanceData()

    # --- 1. Analyst Recommendations ---
    try:
        recs = ticker.recommendations
        if recs is not None and not recs.empty:
            # yfinance >= 1.2.0: aggregated columns [period, strongBuy, buy, hold, sell, strongSell]
            if "strongBuy" in recs.columns:
                row = recs.iloc[0]  # Aktueller Monat (period="0m")
                strong_buy = int(row.get("strongBuy", 0) or 0)
                buy_count = int(row.get("buy", 0) or 0)
                hold_count = int(row.get("hold", 0) or 0)
                sell_count = int(row.get("sell", 0) or 0)
                strong_sell = int(row.get("strongSell", 0) or 0)
                total = strong_buy + buy_count + hold_count + sell_count + strong_sell
                if total > 0:
                    buy_total = strong_buy + buy_count
                    sell_total = sell_count + strong_sell
                    if buy_total > sell_total and buy_total > hold_count:
                        result.recommendation_trend = "Buy"
                    elif sell_total > buy_total and sell_total > hold_count:
                        result.recommendation_trend = "Sell"
                    else:
                        result.recommendation_trend = "Hold"
            else:
                # Legacy yfinance (<1.0): individual analyst ratings with toGrade
                recent = recs.tail(10)
                grades = []
                for _, row in recent.iterrows():
                    grade = ""
                    if "toGrade" in row:
                        grade = str(row["toGrade"]).lower()
                    elif "To Grade" in row:
                        grade = str(row["To Grade"]).lower()
                    if grade:
                        grades.append(grade)
                if grades:
                    buy_keywords = {"buy", "strong buy", "outperform", "overweight", "positive"}
                    sell_keywords = {"sell", "strong sell", "underperform", "underweight", "negative"}
                    buy_count = sum(1 for g in grades if g in buy_keywords)
                    hold_count = sum(1 for g in grades if g not in buy_keywords and g not in sell_keywords)
                    sell_count = sum(1 for g in grades if g in sell_keywords)
                    total = buy_count + hold_count + sell_count
                    if total > 0:
                        if buy_count >= hold_count and buy_count >= sell_count:
                            result.recommendation_trend = "Buy"
                        elif sell_count >= buy_count and sell_count >= hold_count:
                            result.recommendation_trend = "Sell"
                        else:
                            result.recommendation_trend = "Hold"
    except Exception:
        pass

    # --- 2. Insider Transactions ---
    try:
        insiders = ticker.insider_transactions
        if insiders is not None and not insiders.empty:
            buy_count = 0
            sell_count = 0
            for _, row in insiders.iterrows():
                # yfinance 1.2.0: Transaction column is often empty,
                # actual text is in Text column (e.g. "Sale at price 409.52")
                text = str(row.get("Text", "") or "").lower()
                transaction = str(row.get("Transaction", "") or "").lower()
                combined = text or transaction  # Prefer Text, fallback Transaction
                if "purchase" in combined or "buy" in combined or "acquisition" in combined:
                    buy_count += 1
                elif "sale" in combined or "sell" in combined or "disposition" in combined:
                    sell_count += 1
            result.insider_buy_count = buy_count
            result.insider_sell_count = sell_count
    except Exception:
        pass

    # --- 3. ESG Risk Score ---
    try:
        # Primary: ticker.sustainability (may be discontinued by Yahoo)
        sustainability = ticker.sustainability
        if sustainability is not None and not sustainability.empty:
            if "totalEsg" in sustainability.index:
                esg_val = sustainability.loc["totalEsg"].values[0]
                if esg_val and float(esg_val) > 0:
                    result.esg_risk_score = float(esg_val)
            elif "Total ESG Risk score" in sustainability.columns:
                esg_val = sustainability["Total ESG Risk score"].iloc[0]
                if esg_val and float(esg_val) > 0:
                    result.esg_risk_score = float(esg_val)
    except Exception:
        pass

    # Fallback: ESG aus ticker.info wird in fetch_yfinance_fundamentals() geholt,
    # wo ticker.info ohnehin geladen wird. Hier NICHT aufrufen wegen Performance
    # (ticker.info = extra HTTP Request ~3-5s pro Ticker).

    # --- 4. Earnings Growth YoY ---
    try:
        income = ticker.income_stmt
        if income is not None and not income.empty and income.shape[1] >= 2:
            if "Net Income" in income.index:
                recent = income.loc["Net Income"].iloc[0]
                prev = income.loc["Net Income"].iloc[1]
                if prev and prev != 0:
                    growth = ((recent - prev) / abs(prev)) * 100
                    result.earnings_growth_yoy = round(growth, 2)
    except Exception:
        pass

    # --- 5. Earnings Surprise (Beat/Miss Rate) ---
    try:
        eh = ticker.earnings_history
        if eh is not None and not eh.empty:
            beats = 0
            total = 0
            surprises = []
            for _, row in eh.iterrows():
                estimate = row.get("epsEstimate")
                actual = row.get("epsActual")
                surprise_pct = row.get("surprisePercent")
                if estimate is not None and actual is not None:
                    total += 1
                    if actual > estimate:
                        beats += 1
                    if surprise_pct is not None:
                        import math
                        if not math.isnan(float(surprise_pct)):
                            surprises.append(float(surprise_pct))
            if total > 0:
                result.earnings_beat_rate = round(beats / total * 100, 1)
            if surprises:
                result.earnings_surprise_avg = round(
                    sum(surprises) / len(surprises), 2
                )
    except Exception:
        pass

    # --- 6. Next Earnings Date ---
    try:
        ed = ticker.earnings_dates
        if ed is not None and not ed.empty:
            from datetime import datetime as dt
            now = dt.now()
            # Filter für zukünftige Termine
            future = [d for d in ed.index if d.to_pydatetime().replace(tzinfo=None) > now]
            if future:
                next_date = min(future)
                result.next_earnings_date = next_date.strftime("%Y-%m-%d")
    except Exception:
        pass

    return result



async def quick_price_update(tickers: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Schneller Batch-Kurs-Update für alle Ticker.

    Nutzt zwei yf.download() Calls:
    1. period="5d", interval="1d" → Vortagsschluss (für daily change)
    2. period="5d", interval="15m", prepost=True → aktuellster Kurs (Pre-Market/Live)

    Returns:
        Tuple of (prices, daily_changes):
        - prices: {ticker: aktuellster_preis}
        - daily_changes: {ticker: change_vs_prev_close_percent}
    """
    valid_tickers = [t for t in tickers if _is_valid_ticker(t)]
    if not valid_tickers:
        return {}, {}

    def _get_close_series(df, ticker):
        """Extract Close series for a ticker from yf.download() DataFrame.

        yfinance >= 1.2.0 always returns MultiIndex columns (Price, Ticker),
        even for single-ticker downloads. This helper handles both formats.
        """
        import math
        try:
            if df is None or df.empty:
                return None
            # yfinance 1.2.0+: MultiIndex (Price, Ticker)
            if hasattr(df.columns, 'nlevels') and df.columns.nlevels > 1:
                if ("Close", ticker) in df.columns:
                    return df[("Close", ticker)].dropna()
                # Fallback: try df["Close"][ticker]
                try:
                    return df["Close"][ticker].dropna()
                except (KeyError, TypeError):
                    pass
            # Legacy yfinance (<1.0): flat columns
            if "Close" in df.columns:
                col = df["Close"].dropna()
                # If it's a DataFrame (single ticker MultiIndex), squeeze to Series
                if hasattr(col, 'squeeze'):
                    col = col.squeeze()
                return col
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def _batch_download():
        import yfinance as yf
        import math
        prices = {}
        daily_changes = {}
        prev_closes = {}

        # In kleineren Batches laden (Cloud Run hat begrenztes Netzwerk/CPU)
        CHUNK_SIZE = 5
        chunks = [valid_tickers[i:i + CHUNK_SIZE]
                  for i in range(0, len(valid_tickers), CHUNK_SIZE)]

        for chunk in chunks:
            try:
                logger.info(f"[YF-BATCH] Downloading chunk: {chunk}")
                # Schritt 1: Tageskerzen für Vortagsschluss
                daily_data = yf.download(
                    chunk,
                    period="5d",
                    interval="1d",
                    progress=False,
                    threads=False,  # threads=True kann auf Cloud Run hängen
                    timeout=10,     # Verhindert dass Threads bei Connection Drops für immer im Pool hängen bleiben
                )
                if daily_data is not None and not daily_data.empty:
                    logger.info(
                        f"[YF-BATCH] daily_data shape={daily_data.shape}, "
                        f"nlevels={getattr(daily_data.columns, 'nlevels', 1)}"
                    )
                    for ticker in chunk:
                        try:
                            col = _get_close_series(daily_data, ticker)
                            if col is not None and len(col) > 0:
                                last_close = float(col.iloc[-1])
                                if last_close > 0 and not math.isnan(last_close):
                                    prices[ticker] = round(last_close, 2)
                                # Vortagsschluss für Daily-Change-Berechnung
                                if len(col) >= 1:
                                    from datetime import datetime as dt
                                    last_date = col.index[-1].date()
                                    today = dt.now().date()
                                    
                                    if last_date >= today and len(col) >= 2:
                                        prev = float(col.iloc[-2])
                                    else:
                                        prev = float(col.iloc[-1])
                                        
                                    if prev > 0 and not math.isnan(prev):
                                        prev_closes[ticker] = prev
                        except (KeyError, IndexError, TypeError, ValueError) as e:
                            logger.debug(f"[YF-BATCH] ticker {ticker} parse error: {e}")
                else:
                    logger.warning(f"[YF-BATCH] daily_data is empty for chunk {chunk}")

                # Schritt 2: Intraday + Pre-Market für aktuellsten Kurs
                try:
                    intraday = yf.download(
                        chunk,
                        period="5d",
                        interval="15m",
                        prepost=True,
                        progress=False,
                        threads=False,
                        timeout=10,
                    )
                    if intraday is not None and not intraday.empty:
                        for ticker in chunk:
                            try:
                                col = _get_close_series(intraday, ticker)
                                if col is not None and len(col) > 0:
                                    latest = float(col.iloc[-1])
                                    if latest > 0 and not math.isnan(latest):
                                        prices[ticker] = round(latest, 2)
                            except (KeyError, IndexError, TypeError, ValueError):
                                pass
                except Exception as e:
                    logger.debug(f"yfinance Intraday fehlgeschlagen für Batch {chunk}: {e}")

            except Exception as e:
                logger.warning(f"[YF-BATCH] EXCEPTION for chunk {chunk}: {type(e).__name__}: {e}")
                continue

        # Schritt 3: Daily Change = (aktueller Preis - Vortagsschluss) / Vortagsschluss
        for ticker in valid_tickers:
            if ticker in prices and ticker in prev_closes:
                prev = prev_closes[ticker]
                current = prices[ticker]
                if prev > 0:
                    pct = ((current - prev) / prev) * 100
                    # Sanity cap: daily changes >50% are almost always data artifacts
                    # (stale prev_close from weekends, stock splits, currency issues)
                    if abs(pct) > 50:
                        logger.warning(
                            f"[YF-SANITY] {ticker}: daily change {pct:.1f}% capped "
                            f"(current={current}, prev_close={prev})"
                        )
                        continue  # Skip this ticker's daily change
                    daily_changes[ticker] = round(pct, 2)

        # Debug: Warum fehlen Daily Changes?
        if not daily_changes and valid_tickers:
            missing_prices = [t for t in valid_tickers if t not in prices]
            missing_prev = [t for t in valid_tickers if t in prices and t not in prev_closes]
            logger.warning(
                f"[YF-DEBUG] No daily changes! "
                f"prices={len(prices)}/{len(valid_tickers)}, "
                f"prev_closes={len(prev_closes)}, "
                f"missing_prices={missing_prices[:3]}, "
                f"missing_prev={missing_prev[:3]}"
            )
        else:
            logger.info(f"[YF-BATCH] Result: {len(prices)} prices, {len(daily_changes)} daily changes")

        return prices, daily_changes

    try:
        loop = asyncio.get_running_loop()
        prices, daily_changes = await asyncio.wait_for(
            loop.run_in_executor(_executor, _batch_download),
            timeout=90.0,
        )
        logger.info(f"📊 yfinance Kurs-Update: {len(prices)}/{len(valid_tickers)} Ticker, {len(daily_changes)} Daily Changes")
        return prices, daily_changes
    except asyncio.TimeoutError:
        logger.warning("yfinance batch download Timeout (90s)")
        return {}, {}


def _safe_stmt_val(df, key: str, col: int = 0):
    """Sichere Extraktion eines Werts aus yfinance Financial Statement DataFrame."""
    if df is None or df.empty:
        return None
    if key not in df.index:
        return None
    try:
        val = df.loc[key].iloc[col]
        if val is not None and not (isinstance(val, float) and __import__('math').isnan(val)):
            return float(val)
    except (IndexError, ValueError, TypeError):
        pass
    return None


def _calc_altman_z(ticker, info: dict = None) -> float | None:
    """Berechnet Altman Z-Score aus yfinance Financial Statements.

    Z = 1.2×(WC/TA) + 1.4×(RE/TA) + 3.3×(EBIT/TA) + 0.6×(MC/TL) + 1.0×(Rev/TA)

    Args:
        ticker: yfinance Ticker object
        info: Pre-loaded ticker.info dict (vermeidet Extra-Request)

    Returns:
        Z-Score als float oder None bei fehlenden Daten.
    """
    try:
        bs = ticker.balance_sheet
        inc = ticker.income_stmt
        if info is None:
            info = ticker.info or {}

        if bs is None or bs.empty or inc is None or inc.empty:
            return None

        total_assets = _safe_stmt_val(bs, "Total Assets")
        if not total_assets or total_assets <= 0:
            return None

        working_capital = _safe_stmt_val(bs, "Working Capital")
        retained_earnings = _safe_stmt_val(bs, "Retained Earnings")
        ebit = _safe_stmt_val(inc, "EBIT")
        total_revenue = _safe_stmt_val(inc, "Total Revenue")
        total_liabilities = _safe_stmt_val(bs, "Total Liabilities Net Minority Interest")
        market_cap = info.get("marketCap")

        # Mindestens 3 von 5 Faktoren muessen vorhanden sein
        available = sum(1 for v in [working_capital, retained_earnings, ebit, total_revenue, market_cap] if v is not None)
        if available < 3:
            return None

        z = 0.0
        if working_capital is not None:
            z += 1.2 * (working_capital / total_assets)
        if retained_earnings is not None:
            z += 1.4 * (retained_earnings / total_assets)
        if ebit is not None:
            z += 3.3 * (ebit / total_assets)
        if market_cap is not None and total_liabilities and total_liabilities > 0:
            z += 0.6 * (market_cap / total_liabilities)
        if total_revenue is not None:
            z += 1.0 * (total_revenue / total_assets)

        return round(z, 2)
    except Exception:
        return None


def _calc_piotroski(ticker) -> int | None:
    """Berechnet Piotroski F-Score (0-9) aus yfinance Financial Statements.

    9 binaere Kriterien: Profitabilitaet (4), Verschuldung (3), Effizienz (2).

    Returns:
        F-Score (0-9) als int oder None bei fehlenden Daten.
    """
    try:
        bs = ticker.balance_sheet
        inc = ticker.income_stmt
        cf = ticker.cashflow

        if bs is None or bs.empty or inc is None or inc.empty or cf is None or cf.empty:
            return None
        if bs.shape[1] < 2 or inc.shape[1] < 2:
            return None  # Brauchen mind. 2 Jahre fuer Vergleiche

        ta = _safe_stmt_val(bs, "Total Assets")
        ta_prev = _safe_stmt_val(bs, "Total Assets", 1)
        if not ta or ta <= 0:
            return None

        score = 0
        criteria_available = 0

        # --- Profitabilitaet ---
        # 1. ROA > 0
        net_income = _safe_stmt_val(inc, "Net Income")
        if net_income is not None:
            criteria_available += 1
            if net_income > 0:
                score += 1

        # 2. Operating Cashflow > 0
        ocf = _safe_stmt_val(cf, "Operating Cash Flow")
        if ocf is not None:
            criteria_available += 1
            if ocf > 0:
                score += 1

        # 3. ROA steigend (Net Income / Total Assets)
        net_income_prev = _safe_stmt_val(inc, "Net Income", 1)
        if net_income is not None and net_income_prev is not None and ta_prev and ta_prev > 0:
            criteria_available += 1
            roa_curr = net_income / ta
            roa_prev = net_income_prev / ta_prev
            if roa_curr > roa_prev:
                score += 1

        # 4. Accruals: OCF > Net Income (Cashflow-Qualitaet)
        if ocf is not None and net_income is not None:
            criteria_available += 1
            if ocf > net_income:
                score += 1

        # --- Verschuldung ---
        # 5. Long-term Debt sinkend
        ltd = _safe_stmt_val(bs, "Long Term Debt")
        ltd_prev = _safe_stmt_val(bs, "Long Term Debt", 1)
        if ltd is not None and ltd_prev is not None:
            criteria_available += 1
            if ltd <= ltd_prev:
                score += 1

        # 6. Current Ratio steigend
        cl = _safe_stmt_val(bs, "Current Liabilities")
        cl_prev = _safe_stmt_val(bs, "Current Liabilities", 1)
        ca = _safe_stmt_val(bs, "Current Assets")
        ca_prev = _safe_stmt_val(bs, "Current Assets", 1)
        if cl and cl > 0 and cl_prev and cl_prev > 0 and ca and ca_prev:
            criteria_available += 1
            if (ca / cl) > (ca_prev / cl_prev):
                score += 1

        # 7. Keine Verwaesserung (Aktienanzahl nicht gestiegen)
        shares = _safe_stmt_val(bs, "Share Issued")
        shares_prev = _safe_stmt_val(bs, "Share Issued", 1)
        if shares is not None and shares_prev is not None:
            criteria_available += 1
            if shares <= shares_prev:
                score += 1

        # --- Effizienz ---
        # 8. Gross Margin steigend
        gp = _safe_stmt_val(inc, "Gross Profit")
        gp_prev = _safe_stmt_val(inc, "Gross Profit", 1)
        rev = _safe_stmt_val(inc, "Total Revenue")
        rev_prev = _safe_stmt_val(inc, "Total Revenue", 1)
        if gp and rev and rev > 0 and gp_prev and rev_prev and rev_prev > 0:
            criteria_available += 1
            if (gp / rev) > (gp_prev / rev_prev):
                score += 1

        # 9. Asset Turnover steigend (Revenue / Total Assets)
        if rev and ta and ta > 0 and rev_prev and ta_prev and ta_prev > 0:
            criteria_available += 1
            if (rev / ta) > (rev_prev / ta_prev):
                score += 1

        # Mindestens 5 von 9 Kriterien muessen auswertbar sein
        if criteria_available < 5:
            return None

        return score
    except Exception:
        return None


async def fetch_yfinance_fundamentals(ticker_symbol: str) -> dict:
    """Holt Fundamentaldaten von yfinance als Fallback fuer FMP.

    Liefert FundamentalData + AnalystData + Sektor/Name aus yf.Ticker.info.
    Kein API-Key noetig, kein Rate-Limit (aber langsamer als FMP).

    Returns:
        dict mit keys: fundamentals (FundamentalData), analyst (AnalystData),
                       sector (str), name (str)
    """
    from models import FundamentalData, AnalystData

    if not _is_valid_ticker(ticker_symbol):
        return {}

    cache_key = f"yf_fund_{ticker_symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        if _cache.is_negative(cache_key):
            return {}
        return {
            "fundamentals": FundamentalData(**cached.get("fundamentals", {})),
            "analyst": AnalystData(**cached.get("analyst", {})),
            "sector": cached.get("sector", ""),
            "name": cached.get("name", ""),
        }

    def _fetch_sync():
        import yfinance as yf
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info or {}
            if not info or info.get("quoteType") == "NONE":
                return None

            fd = FundamentalData()
            # --- Bewertung ---
            fd.pe_ratio = _safe_float(info, "trailingPE")
            fd.pb_ratio = _safe_float(info, "priceToBook")
            fd.ps_ratio = _safe_float(info, "priceToSalesTrailing12Months")
            fd.peg_ratio = _safe_float(info, "pegRatio")
            ev_ebitda = _safe_float(info, "enterpriseToEbitda")
            fd.ev_to_ebitda = ev_ebitda

            # --- Qualitaet ---
            roe = _safe_float(info, "returnOnEquity")
            fd.roe = round(roe * 100, 2) if roe and abs(roe) < 10 else roe  # yf gibt 0.25 statt 25%
            roa = _safe_float(info, "returnOnAssets")
            fd.roa = round(roa * 100, 2) if roa and abs(roa) < 10 else roa
            fd.debt_to_equity = _safe_float(info, "debtToEquity")
            fd.current_ratio = _safe_float(info, "currentRatio")

            gm = _safe_float(info, "grossMargins")
            fd.gross_margin = round(gm * 100, 2) if gm and abs(gm) < 10 else gm
            om = _safe_float(info, "operatingMargins")
            fd.operating_margin = round(om * 100, 2) if om and abs(om) < 10 else om
            nm = _safe_float(info, "profitMargins")
            fd.net_margin = round(nm * 100, 2) if nm and abs(nm) < 10 else nm

            # --- Weitere ---
            fd.market_cap = _safe_float(info, "marketCap")
            fd.beta = _safe_float(info, "beta")
            fd.dividend_yield = _safe_float(info, "dividendYield")
            # yfinance dividendYield ist bereits in Prozent (z.B. 0.65 = 0.65%, 4.88 = 4.88%)
            # KEINE Konvertierung nötig!

            # --- Wachstum ---
            eg = _safe_float(info, "earningsGrowth")
            fd.earnings_growth = round(eg * 100, 2) if eg and abs(eg) < 100 else eg
            rg = _safe_float(info, "revenueGrowth")
            fd.revenue_growth = round(rg * 100, 2) if rg and abs(rg) < 100 else rg

            # FCF Yield berechnen (FCF / MarketCap)
            fcf = _safe_float(info, "freeCashflow")
            mcap = fd.market_cap
            if fcf and mcap and mcap > 0:
                fd.free_cashflow_yield = round(fcf / mcap, 4)

            # --- Quantitative Scores (Altman Z + Piotroski) ---
            # Nutzt balance_sheet/income_stmt/cashflow + bereits geladenes info
            fd.altman_z_score = _calc_altman_z(ticker, info)
            fd.piotroski_score = _calc_piotroski(ticker)

            # --- Analyst ---
            ad = AnalystData()
            tp = _safe_float(info, "targetMeanPrice")
            if tp:
                ad.target_price = tp
            rec = info.get("recommendationKey", "")
            if rec:
                ad.consensus = rec.capitalize()
            n_analysts = info.get("numberOfAnalystOpinions")
            if n_analysts:
                ad.num_analysts = int(n_analysts)

            # Buy/Hold/Sell Counts aus ticker.recommendations (v1.2.0 Format)
            try:
                recs = ticker.recommendations
                if recs is not None and not recs.empty and "strongBuy" in recs.columns:
                    r0 = recs.iloc[0]
                    ad.strong_buy_count = int(r0.get("strongBuy", 0) or 0)
                    ad.buy_count = int(r0.get("buy", 0) or 0)
                    ad.hold_count = int(r0.get("hold", 0) or 0)
                    ad.sell_count = int(r0.get("sell", 0) or 0)
                    ad.strong_sell_count = int(r0.get("strongSell", 0) or 0)
                    total = ad.strong_buy_count + ad.buy_count + ad.hold_count + ad.sell_count + ad.strong_sell_count
                    if total > 0:
                        ad.num_analysts = max(ad.num_analysts, total)
                        # Konsens aus Counts ableiten falls nicht vorhanden
                        if not ad.consensus:
                            buy_total = ad.strong_buy_count + ad.buy_count
                            sell_total = ad.sell_count + ad.strong_sell_count
                            if buy_total > sell_total and buy_total > ad.hold_count:
                                ad.consensus = "Buy"
                            elif sell_total > buy_total and sell_total > ad.hold_count:
                                ad.consensus = "Sell"
                            else:
                                ad.consensus = "Hold"
            except Exception:
                pass

            sector = info.get("sector", "")
            name = info.get("shortName") or info.get("longName") or ""

            # ESG aus ticker.info extrahieren (kein Extra-Request weil info schon geladen)
            esg_score = None
            for esg_key in ("esgScore", "totalEsg", "overallRisk"):
                val = info.get(esg_key)
                if val is not None:
                    import math
                    try:
                        fval = float(val)
                        if not math.isnan(fval) and fval > 0:
                            esg_score = fval
                            break
                    except (ValueError, TypeError):
                        pass

            return {
                "fundamentals": fd,
                "analyst": ad,
                "sector": sector,
                "name": name,
                "esg_risk_score": esg_score,
            }
        except Exception as e:
            logger.debug(f"yfinance Fundamentals fehlgeschlagen fuer {ticker_symbol}: {e}")
            return None

    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, _fetch_sync),
            timeout=10.0
        )
        if result:
            cache_data = {
                "fundamentals": result["fundamentals"].model_dump(),
                "analyst": result["analyst"].model_dump(),
                "sector": result["sector"],
                "name": result["name"],
                "esg_risk_score": result.get("esg_risk_score"),
            }
            _cache.set(cache_key, cache_data)
            _cache.flush()
            logger.info(f"yfinance Fundamentals geladen fuer {ticker_symbol}")
            return result
        else:
            _cache.set_negative(cache_key)
    except asyncio.TimeoutError:
        logger.debug(f"yfinance Fundamentals Timeout fuer {ticker_symbol}")
    except Exception as e:
        logger.debug(f"yfinance Fundamentals fehlgeschlagen fuer {ticker_symbol}: {e}")

    return {}


def _safe_float(data: dict, key: str) -> float | None:
    """Sichere Float-Extraktion aus dict (None/NaN-safe)."""
    import math
    val = data.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def clear_cache():
    """Löscht den yfinance Cache."""
    _cache.clear()
