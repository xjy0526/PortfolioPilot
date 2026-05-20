"""PortfolioPilot - Analytics API Routes.

Endpunkte für erweiterte Analysen:
  - Dividenden, Benchmark, Korrelation, Risk, Earnings, News,
    Score-History, Movers (Gewinner/Verlierer), Heatmap

Performance: Teuer berechnete Endpoints (Korrelation, Risk, Benchmark, Indices)
werden mit einem In-Memory-Cache (15min TTL) zwischengespeichert.
"""
import logging
import time
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from state import portfolio_data
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# In-Memory Cache für teure Analytics-Berechnungen (TTL-basiert)
_analytics_cache: dict[str, tuple[float, any]] = {}  # key -> (timestamp, data)
_CACHE_TTL_SECONDS = 900  # 15 Minuten


def _get_cached(key: str):
    """Holt gecachte Daten wenn TTL nicht abgelaufen."""
    if key in _analytics_cache:
        ts, data = _analytics_cache[key]
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return data
    return None


def _set_cached(key: str, data):
    """Speichert Daten im Analytics-Cache."""
    _analytics_cache[key] = (time.time(), data)


# ─────────────────────────────────────────────────────────────
# Market Indices (Nasdaq, S&P 500, DAX)
# ─────────────────────────────────────────────────────────────

@router.get("/api/market-indices")
async def get_market_indices():
    """Tagesaktuelle Werte der wichtigsten Indizes (gecacht 15min)."""
    # Demo-Modus: Synthetische Daten
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_market_indices
        return get_demo_market_indices()

    cached = _get_cached("market_indices")
    if cached is not None:
        return cached

    indices = [
        {"symbol": "^GSPC", "name": "S&P 500"},
        {"symbol": "^IXIC", "name": "Nasdaq"},
        {"symbol": "^GDAXI", "name": "DAX"},
    ]
    results = []

    try:
        import yfinance as yf
        from datetime import datetime as dt
        import math

        for idx in indices:
            try:
                # Use robust method: 5d history with 1h interval and pre/post market
                hist = yf.download(
                    tickers=idx["symbol"],
                    period="5d",
                    interval="1h",
                    prepost=True,
                    progress=False
                )
                
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    col = hist["Close"]
                    
                    # Handle MultiIndex if present
                    if hasattr(col, 'nlevels') and col.nlevels > 1:
                        if idx["symbol"] in col.columns:
                            col = col[idx["symbol"]]
                        else:
                            # Squeeze it
                            col = col.squeeze()
                            
                    col = col.dropna()
                    if len(col) >= 1:
                        curr = float(col.iloc[-1].item() if hasattr(col.iloc[-1], 'item') else col.iloc[-1])
                        
                        last_date = col.index[-1].date()
                        today = dt.now().date()
                        
                        if last_date >= today and len(col) >= 2:
                            prev = float(col.iloc[-2].item() if hasattr(col.iloc[-2], 'item') else col.iloc[-2])
                        else:
                            prev = float(col.iloc[-1].item() if hasattr(col.iloc[-1], 'item') else col.iloc[-1])
                            
                        if prev > 0 and not math.isnan(prev) and curr > 0 and not math.isnan(curr):
                            change = curr - prev
                            change_pct = (change / prev) * 100
                            results.append({
                                "name": idx["name"],
                                "symbol": idx["symbol"],
                                "price": round(curr, 2),
                                "change": round(change, 2),
                                "change_pct": round(change_pct, 2),
                            })
                            continue
                            
                logger.warning(f"Index {idx['name']} konnte nicht via History berechnet werden")
            except Exception as e:
                logger.warning(f"Index {idx['name']} fehlgeschlagen: {e}")
    except Exception as e:
        logger.error(f"Market-Indices fehlgeschlagen: {e}")

    if results:
        _set_cached("market_indices", results)
    return results


# ─────────────────────────────────────────────────────────────
# #1: Dividenden-Tracker
# ─────────────────────────────────────────────────────────────

@router.get("/api/dividends")
async def get_dividends():
    """Dividenden-Übersicht: Yield, jährliche Einnahmen, Prognose."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return JSONResponse({"error": "Keine Daten"}, status_code=503)

    from engine.analytics import calculate_dividend_summary
    return calculate_dividend_summary(summary.stocks)


# ─────────────────────────────────────────────────────────────
# #3: Benchmark-Vergleich
# ─────────────────────────────────────────────────────────────

@router.get("/api/benchmark")
async def get_benchmark(symbol: str = "SPY", period: str = "6month"):
    """Benchmark-Vergleich: Portfolio vs. Index (gecacht 15min)."""
    # Demo-Modus
    summary_check = portfolio_data.get("summary")
    if summary_check and summary_check.is_demo:
        from fetchers.demo_data import get_demo_benchmark
        days_map = {"1month": 30, "3month": 90, "6month": 180, "1year": 365}
        return get_demo_benchmark(symbol, days_map.get(period, 180))

    if period not in ("1month", "3month", "6month", "1year"):
        period = "6month"

    cache_key = f"benchmark_{symbol}_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    days_map = {"1month": 30, "3month": 90, "6month": 180, "1year": 365}
    days = days_map.get(period, 180)

    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        # Benchmark-Daten
        ticker = yf.Ticker(symbol)
        end = datetime.now()
        start = end - timedelta(days=days)
        hist = ticker.history(start=start, end=end)

        if hist is None or hist.empty:
            return {"error": f"Keine Daten für {symbol}"}

        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return {"error": "Zu wenig Datenpunkte"}

        first_price = float(closes.iloc[0])
        benchmark_data = []
        for date, price in closes.items():
            pct = ((float(price) - first_price) / first_price) * 100
            benchmark_data.append({
                "date": date.strftime("%Y-%m-%d"),
                "price": round(float(price), 2),
                "return_pct": round(pct, 2),
            })

        # Portfolio-Performance aus History (Demo Mode wird in der DB via Summary abgefangen)
        from database import load_snapshots as load_history
        portfolio_history = load_history(days=days)

        portfolio_data_series = []
        if portfolio_history and len(portfolio_history) >= 2:
            first_val = portfolio_history[0].get("total_value", 1)
            for entry in portfolio_history:
                val = entry.get("total_value", 0)
                pct = ((val - first_val) / first_val) * 100 if first_val > 0 else 0
                portfolio_data_series.append({
                    "date": entry["date"],
                    "value": val,
                    "return_pct": round(pct, 2),
                })

        result = {
            "benchmark_symbol": symbol,
            "benchmark_name": _benchmark_name(symbol),
            "period": period,
            "benchmark": benchmark_data,
            "portfolio": portfolio_data_series,
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Benchmark-Vergleich fehlgeschlagen: {e}")
        return {"error": str(e)}


def _benchmark_name(symbol: str) -> str:
    names = {
        "SPY": "S&P 500",
        "IWDA.AS": "MSCI World",
        "QQQ": "Nasdaq 100",
        "^GDAXI": "DAX",
    }
    return names.get(symbol, symbol)


# ─────────────────────────────────────────────────────────────
# #4: Korrelationsmatrix
# ─────────────────────────────────────────────────────────────

@router.get("/api/correlation")
async def get_correlation():
    """Korrelationsmatrix und Diversifikations-Score (gecacht 15min)."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_correlation
        return get_demo_correlation()

    cached = _get_cached("correlation")
    if cached is not None:
        return cached

    if not summary or not summary.stocks:
        return JSONResponse({"error": "Keine Daten"}, status_code=503)

    try:
        import yfinance as yf
        from datetime import datetime, timedelta
        from state import YFINANCE_ALIASES

        tickers = [
            s.position.ticker for s in summary.stocks
            if s.position.ticker != "CASH"
        ]

        # Historische Preise für Korrelation laden
        end = datetime.now()
        start = end - timedelta(days=120)

        price_data = {}
        for ticker in tickers:
            yf_ticker = YFINANCE_ALIASES.get(ticker, ticker)
            try:
                t = yf.Ticker(yf_ticker)
                hist = t.history(start=start, end=end)
                if hist is not None and not hist.empty:
                    closes = hist["Close"].dropna().tolist()
                    if len(closes) >= 20:
                        price_data[ticker] = closes
            except Exception:
                continue

        from engine.analytics import calculate_correlation_matrix
        result = calculate_correlation_matrix(price_data)
        _set_cached("correlation", result)
        return result

    except Exception as e:
        logger.error(f"Korrelationsmatrix fehlgeschlagen: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────
# #6: Earnings-Kalender
# ─────────────────────────────────────────────────────────────

@router.get("/api/earnings-calendar")
async def get_earnings_calendar():
    """Nächste Earnings-Termine der Portfolio-Aktien."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return JSONResponse({"error": "Keine Daten"}, status_code=503)

    earnings = []
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            continue
        if stock.yfinance and stock.yfinance.next_earnings_date:
            entry = {
                "ticker": stock.position.ticker,
                "name": stock.position.name,
                "date": stock.yfinance.next_earnings_date,
                "beat_rate": stock.yfinance.earnings_beat_rate,
                "surprise_avg": stock.yfinance.earnings_surprise_avg,
                "eps_estimated": None, # Nicht von yfinance verfuegbar, aber UI erwartet evtl das Feld
            }
            earnings.append(entry)

    # Sortieren nach Datum (nächster zuerst)
    earnings.sort(key=lambda x: x["date"])
    return earnings


# ─────────────────────────────────────────────────────────────
# #7: News pro Aktie
# ─────────────────────────────────────────────────────────────

@router.get("/api/stock/{ticker}/news")
async def get_stock_news(ticker: str, limit: int = 5):
    """News für eine einzelne Aktie."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_stock_news
        return get_demo_stock_news(ticker.upper())[:limit]

    try:
        from fetchers.fmp import fetch_stock_news
        return await fetch_stock_news(ticker.upper(), limit=limit)
    except Exception as e:
        logger.error(f"News fehlgeschlagen für {ticker}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# #8: Portfolio-Risiko
# ─────────────────────────────────────────────────────────────

@router.get("/api/risk")
async def get_risk():
    """Portfolio-Risikokennzahlen: Beta, VaR, Max Drawdown (gecacht 15min)."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_risk
        return get_demo_risk()

    cached = _get_cached("risk")
    if cached is not None:
        return cached

    if not summary or not summary.stocks:
        return JSONResponse({"error": "Keine Daten"}, status_code=503)

    try:
        import yfinance as yf
        from datetime import datetime, timedelta
        from state import YFINANCE_ALIASES

        # Berechne Portfolio-Returns für VaR
        tickers = [
            s.position.ticker for s in summary.stocks
            if s.position.ticker != "CASH"
        ]
        total_value = sum(
            s.position.current_value for s in summary.stocks
            if s.position.ticker != "CASH"
        )

        end = datetime.now()
        start = end - timedelta(days=180)

        # Gewichtete Portfolio-Returns berechnen
        all_returns = {}
        for s in summary.stocks:
            if s.position.ticker == "CASH":
                continue
            yf_ticker = YFINANCE_ALIASES.get(s.position.ticker, s.position.ticker)
            try:
                t = yf.Ticker(yf_ticker)
                hist = t.history(start=start, end=end)
                if hist is not None and not hist.empty:
                    closes = hist["Close"].dropna()
                    if len(closes) >= 20:
                        rets = closes.pct_change().dropna().tolist()
                        weight = s.position.current_value / total_value if total_value > 0 else 0
                        all_returns[s.position.ticker] = (rets, weight)
            except Exception:
                continue

        # Gewichtete Portfolio-Returns
        portfolio_returns = []
        if all_returns:
            min_len = min(len(r[0]) for r in all_returns.values())
            for i in range(min_len):
                daily_ret = sum(
                    rets[i] * weight
                    for rets, weight in all_returns.values()
                )
                portfolio_returns.append(daily_ret)

        from engine.analytics import calculate_portfolio_risk
        result = calculate_portfolio_risk(summary.stocks, portfolio_returns)
        _set_cached("risk", result)
        return result

    except Exception as e:
        logger.error(f"Risiko-Berechnung fehlgeschlagen: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────
# #9: Score-Verlauf pro Aktie
# ─────────────────────────────────────────────────────────────

@router.get("/api/stock/{ticker}/score-history")
async def get_score_history(ticker: str, days: int = 30):
    """Score-Verlauf aus der bestehenden Score-Historie."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_score_history
        return get_demo_score_history(ticker.upper(), days)

    from engine.analysis import get_score_trend
    trend = get_score_trend(ticker.upper(), days=days)
    return trend


# ─────────────────────────────────────────────────────────────
# #13: Gewinner/Verlierer des Tages
# ─────────────────────────────────────────────────────────────

@router.get("/api/movers")
async def get_movers():
    """Top 3 Gewinner und Verlierer des Tages."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return {"winners": [], "losers": []}

    stocks_with_daily = [
        s for s in summary.stocks
        if s.position.ticker != "CASH"
        and s.position.daily_change_pct is not None
    ]

    if not stocks_with_daily:
        return {"winners": [], "losers": []}

    sorted_by_daily = sorted(
        stocks_with_daily,
        key=lambda s: s.position.daily_change_pct or 0,
        reverse=True,
    )

    def _to_mover(s):
        pos = s.position
        daily_eur = pos.current_value * (pos.daily_change_pct or 0) / (100 + (pos.daily_change_pct or 0))
        return {
            "ticker": pos.ticker,
            "name": pos.name,
            "daily_pct": round(pos.daily_change_pct or 0, 2),
            "daily_eur": round(daily_eur, 2),
            "current_price": round(pos.current_price, 2),
            "score": round(s.score.total_score, 1) if s.score else 0,
            "rating": s.score.rating.value if s.score else "hold",
        }

    winners = [_to_mover(s) for s in sorted_by_daily[:3] if (s.position.daily_change_pct or 0) > 0]
    losers = [_to_mover(s) for s in sorted_by_daily[-3:] if (s.position.daily_change_pct or 0) < 0]
    losers.reverse()  # schlechtester zuerst

    return {"winners": winners, "losers": losers}


# ─────────────────────────────────────────────────────────────
# #14: Portfolio-Heatmap
# ─────────────────────────────────────────────────────────────

@router.get("/api/heatmap")
async def get_heatmap():
    """Treemap-Daten: Ticker, Gewicht, Tagesperformance, Score."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return []

    total_value = summary.total_value
    if total_value <= 0:
        return []

    result = []
    for s in summary.stocks:
        if s.position.ticker == "CASH":
            continue
        pos = s.position
        weight = (pos.current_value / total_value * 100) if total_value > 0 else 0
        result.append({
            "ticker": pos.ticker,
            "name": pos.name,
            "sector": pos.sector or "Unknown",
            "weight": round(weight, 2),
            "value": round(pos.current_value, 2),
            "daily_pct": round(pos.daily_change_pct or 0, 2),
            "score": round(s.score.total_score, 1) if s.score else 0,
            "rating": s.score.rating.value if s.score else "hold",
            "pnl_pct": round(pos.pnl_percent, 1),
        })

    # Sort: biggest daily winner first, biggest loser last
    result.sort(key=lambda x: x["daily_pct"], reverse=True)
    return result


# ─────────────────────────────────────────────────────────────
# #15: Performance Attribution
# ─────────────────────────────────────────────────────────────

@router.get("/api/attribution")
async def get_attribution():
    """Performance Attribution: P&L-Zerlegung nach Position, Sektor, Dividenden."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.stocks:
        return JSONResponse({"error": "Keine Daten"}, status_code=503)

    # Activities aus State lesen (bereits beim Refresh gecacht)
    activities = portfolio_data.get("activities")
    if not activities:
        try:
            from fetchers.parqet import fetch_portfolio_activities_raw
            activities = await fetch_portfolio_activities_raw()
        except Exception:
            pass

    from engine.attribution import calculate_attribution
    return calculate_attribution(summary.stocks, activities)


# ─────────────────────────────────────────────────────────────
# #16: Portfolio History Detail (Stacked Area Chart)
# ─────────────────────────────────────────────────────────────

@router.get("/api/portfolio/history-detail")
async def get_portfolio_history_detail(period: str = "6month"):
    """Historische Portfolio-Werte pro Aktie + Gesamt für Stacked Area Chart.

    Nutzt SQLite-Cache für bereits geladene Kurse (inkrementell).
    """
    period_map = {
        "1month": 30, "3month": 90, "6month": 180,
        "1year": 365, "max": 9999,
    }
    days = period_map.get(period, 180)

    # Demo-Modus: Synthetische Verlaufsdaten (VOR Cache-Check!)
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_portfolio_history
        demo_history = get_demo_portfolio_history(days=days)
        # Format anpassen für Stacked Area Chart
        return {
            "dates": [d["date"] for d in demo_history],
            "total_values": [d["total_value"] for d in demo_history],
            "invested_values": [d["invested_capital"] for d in demo_history],
            "positions": {},
            "is_demo": True,
        }

    cache_key = f"history_detail_{period}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # Activities laden
    activities = portfolio_data.get("activities")
    if not activities:
        try:
            from fetchers.parqet import fetch_portfolio_activities_raw
            activities = await fetch_portfolio_activities_raw()
        except Exception:
            pass

    if not activities:
        return JSONResponse({"error": "Keine Activities verfügbar"}, status_code=503)

    # Raw Activities für Cash-Rekonstruktion laden (inkl. Cash-Einträge)
    raw_activities = None
    try:
        import json
        from pathlib import Path
        raw_cache = Path(settings.CACHE_DIR) / "parqet_activities.json"
        if raw_cache.exists():
            raw_activities = json.loads(raw_cache.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"Raw Activities Cache nicht verfügbar: {e}")

    # Aktuellen Cash-Bestand aus Portfolio-Daten holen (Parqet-Ankerwert)
    current_cash = 0.0
    summary = portfolio_data.get("summary")
    if summary and summary.stocks:
        for s in summary.stocks:
            if s.position.ticker == "CASH":
                current_cash = s.position.current_value or s.position.current_price or 0.0
                break

    try:
        from engine.portfolio_history import build_portfolio_history
        result = await build_portfolio_history(
            activities, period_days=days,
            raw_activities=raw_activities,
            current_cash=current_cash,
        )
        if result and result.get("dates"):
            _set_cached(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Portfolio History Detail fehlgeschlagen: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


@router.get("/api/performance")
async def get_portfolio_performance():
    """Liefert die komplette Portfolio-Performance über die Parqet Connect API."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        from fetchers.demo_data import get_demo_performance
        return get_demo_performance()

    try:
        from fetchers.parqet import fetch_portfolio_performance
        result = await fetch_portfolio_performance()
        if not result:
            return JSONResponse(
                {"error": "Performance-Daten nicht verfügbar"},
                status_code=503,
            )
        return result
    except Exception as e:
        logger.error(f"Performance Endpoint Fehler: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
