"""PortfolioPilot - Portfolio & Daten API-Routes.

GET-Endpoints für Dashboard, Portfolio, Aktien, Rebalancing, etc.
"""
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from state import portfolio_data
from config import settings
from models import PortfolioSummary, SectorAllocation

logger = logging.getLogger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/")
async def index():
    """Serve the dashboard."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return FileResponse(str(html_file))
    return JSONResponse({"error": "Dashboard nicht gefunden"}, status_code=404)


@router.get("/api/portfolio")
async def get_portfolio():
    """Portfolio-Übersicht mit Scores."""
    summary = portfolio_data.get("summary")
    if not summary:
        return JSONResponse({"error": "Daten werden geladen...", "refreshing": True}, status_code=503)
    return summary.model_dump()


@router.get("/api/stock/{ticker}")
async def get_stock(ticker: str):
    """Detaildaten einer einzelnen Aktie."""
    summary = portfolio_data.get("summary")
    if not summary:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)

    for stock in summary.stocks:
        if stock.position.ticker.upper() == ticker.upper():
            return stock.model_dump()

    return JSONResponse({"error": f"Aktie {ticker} nicht im Portfolio"}, status_code=404)


@router.get("/api/stock/{ticker}/history")
async def get_stock_history(ticker: str, period: str = "3month"):
    """Historische Kursdaten für eine Aktie."""
    if period not in ("1month", "3month", "6month", "1year"):
        period = "3month"

    if settings.demo_mode:
        # Generate synthetic demo data
        import random
        from datetime import timedelta as td
        days = {"1month": 30, "3month": 90, "6month": 180, "1year": 365}.get(period, 90)
        base_price = 150.0
        data = []
        for i in range(days):
            date = (datetime.now() - td(days=days - i)).strftime("%Y-%m-%d")
            base_price *= (1 + random.uniform(-0.03, 0.035))
            data.append({"date": date, "close": round(base_price, 2)})
        return data

    from fetchers.fmp import get_historical_prices
    return await get_historical_prices(ticker, period)


@router.get("/api/portfolio/history")
async def get_portfolio_history(days: int = 90):
    """Portfolio-Verlauf: Investiertes Kapital + aktueller Wert ueber Zeit.

    Datenquellen (in Prioritaet):
    1. Parqet Activities -> rekonstruierte Investment-Timeline
    2. Lokale Snapshots aus vorherigen Refreshes
    3. Aktueller Portfoliowert als einzelner Datenpunkt
    """
    # --- 1. Versuche Investment-Timeline aus Parqet Activities ---
    try:
        # Activities aus State lesen (bereits beim Refresh gecacht)
        activities = portfolio_data.get("activities")
        if not activities:
            from fetchers.parqet import fetch_portfolio_activities_raw
            activities = await fetch_portfolio_activities_raw()
        from datetime import datetime as dt, timedelta
        if activities and len(activities) > 0:
            # Kumuliertes investiertes Kapital pro Tag berechnen
            daily_invested = {}
            cumulative = 0.0

            for act in activities:
                date = act.get("date", "")
                if not date:
                    continue
                act_type = act.get("type", "")
                amount = act.get("amount", 0)

                if act_type in ("buy", "kauf", "purchase"):
                    cumulative += amount
                elif act_type in ("sell", "verkauf", "sale"):
                    cumulative -= amount
                elif act_type in ("transferin", "transfer_in"):
                    cumulative += amount
                elif act_type in ("transferout", "transfer_out"):
                    cumulative -= amount

                daily_invested[date] = round(cumulative, 2)

            if daily_invested:
                # Cutoff anwenden
                if days < 9999:
                    cutoff = (dt.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                    filtered = {d: v for d, v in daily_invested.items() if d >= cutoff}
                else:
                    filtered = daily_invested

                if filtered:
                    # Aktuellen Portfoliowert als letzten Datenpunkt hinzufuegen
                    summary = portfolio_data.get("summary")
                    current_value = summary.total_value if summary else 0
                    today = dt.now().strftime("%Y-%m-%d")

                    result = [
                        {"date": d, "total_value": 0, "invested_capital": v}
                        for d, v in sorted(filtered.items())
                    ]
                    # Aktuellen Wert beim letzten Eintrag setzen
                    if result and current_value > 0:
                        result[-1]["total_value"] = round(current_value, 2)

                    return result

    except Exception as e:
        logger.warning(f"Portfolio Activities Timeline fehlgeschlagen: {e}")

    # --- 2. Fallback: Lokale Snapshots ---
    from database import load_snapshots as load_history
    local = load_history(days=days)
    if local:
        return local

    # --- 3. Fallback: Aktueller Portfoliowert ---
    summary = portfolio_data.get("summary")
    if summary and summary.total_value > 0:
        from datetime import datetime as dt
        return [{
            "date": dt.now().strftime("%Y-%m-%d"),
            "total_value": round(summary.total_value, 2),
            "invested_capital": round(summary.total_cost, 2),
        }]

    return []


@router.get("/api/portfolio/activities")
async def get_portfolio_activities():
    """Alle Kauf/Verkauf/Dividenden-Transaktionen von Parqet."""
    # Demo-Modus
    summary = portfolio_data.get("summary")
    if summary and summary.is_demo:
        activities = portfolio_data.get("activities")
        if activities:
            return activities
        from fetchers.demo_data import get_demo_activities
        return get_demo_activities()

    try:
        from fetchers.parqet import fetch_portfolio_activities_raw
        return await fetch_portfolio_activities_raw()
    except Exception as e:
        logger.error(f"Portfolio Activities Fehler: {e}")
        return []


@router.get("/api/rebalancing")
async def get_rebalancing():
    """Rebalancing-Empfehlungen."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.rebalancing:
        return JSONResponse({"error": "Keine Rebalancing-Daten"}, status_code=503)
    return summary.rebalancing.model_dump()


@router.get("/api/tech-picks")
async def get_tech_picks():
    """Tägliche Tech-Empfehlungen."""
    summary = portfolio_data.get("summary")
    if not summary:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)
    return [p.model_dump() for p in summary.tech_picks]


@router.get("/api/sectors")
async def get_sectors():
    """Sektor-Allokation."""
    summary = portfolio_data.get("summary")
    if not summary:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)

    sectors: dict[str, SectorAllocation] = {}
    total_value = summary.total_value

    for s in summary.stocks:
        sector = s.position.sector or "Unknown"
        if sector not in sectors:
            sectors[sector] = SectorAllocation(sector=sector)
        sa = sectors[sector]
        sa.value += s.position.current_value
        sa.count += 1

    for sa in sectors.values():
        sa.weight = round((sa.value / total_value * 100) if total_value > 0 else 0, 1)
        sa.value = round(sa.value, 2)

    return [sa.model_dump() for sa in sorted(sectors.values(), key=lambda x: x.value, reverse=True)]


@router.get("/api/asset-allocation")
async def get_asset_allocation():
    """Asset-/Markt-Allokation für globale Aktien, A-Shares und Polymarket."""
    summary = portfolio_data.get("summary")
    if not summary:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)

    total_value = summary.total_value or 0.0
    buckets: dict[str, dict] = {}

    for stock in summary.stocks:
        pos = stock.position
        if pos.ticker == "CASH":
            label = "Cash"
        elif pos.asset_type == "prediction_market":
            label = "Polymarket"
        elif pos.asset_type == "cn_equity":
            label = "China A-Shares"
        else:
            label = pos.market or "Global Equities"

        bucket = buckets.setdefault(label, {"label": label, "value": 0.0, "count": 0})
        bucket["value"] += pos.current_value
        bucket["count"] += 1

    result = []
    for bucket in buckets.values():
        bucket["value"] = round(bucket["value"], 2)
        bucket["weight"] = round((bucket["value"] / total_value * 100) if total_value > 0 else 0, 1)
        result.append(bucket)

    return sorted(result, key=lambda x: x["value"], reverse=True)


@router.get("/api/fear-greed")
async def get_fear_greed():
    """Fear & Greed Index."""
    summary = portfolio_data.get("summary")
    if not summary or not summary.fear_greed:
        return {"value": 50, "label": "Neutral", "source": "N/A"}
    return summary.fear_greed.model_dump()


@router.get("/api/status")
async def get_status():
    """App-Status."""
    from fetchers.fmp import get_fmp_usage
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "last_refresh": portfolio_data.get("last_refresh"),
        "refreshing": portfolio_data["refreshing"],
        "positions": portfolio_data["summary"].num_positions if portfolio_data["summary"] else 0,
        "ws_connected": _is_ws_connected(),
        "fmp_usage": get_fmp_usage(),
    }


def _is_ws_connected() -> bool:
    """Prüft ob yFinance WebSocket verbunden ist."""
    try:
        from fetchers.yfinance_ws import get_yf_streamer
        return get_yf_streamer().is_connected
    except Exception:
        return False


@router.get("/api/portfolio/csv-positions")
async def get_csv_positions():
    """List positions stored in the local portfolio CSV."""
    from fetchers.csv_reader import load_saved_csv_positions, resolve_csv_path

    path = resolve_csv_path()
    return {
        "exists": path.exists(),
        "csv_path": str(path),
        "positions": load_saved_csv_positions() if path.exists() else [],
    }


@router.post("/api/portfolio/csv-positions")
async def create_csv_position(data: dict):
    """Add or replace a single position in the local portfolio CSV."""
    from fetchers.csv_reader import resolve_csv_path, upsert_csv_position
    from services.portfolio_builder import update_saved_csv_portfolio

    position = data.get("position", data)
    try:
        positions, saved_position, replaced = upsert_csv_position(position)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    result = await update_saved_csv_portfolio()
    return {
        "status": "ok",
        "action": "updated" if replaced else "created",
        "position": saved_position,
        "positions": len(positions),
        "csv_path": str(resolve_csv_path()),
        "portfolio": result,
    }


@router.put("/api/portfolio/csv-positions/{ticker}")
async def update_csv_position(ticker: str, data: dict):
    """Update a single position in the local portfolio CSV."""
    from fetchers.csv_reader import resolve_csv_path, upsert_csv_position
    from services.portfolio_builder import update_saved_csv_portfolio

    position = data.get("position", data)
    try:
        positions, saved_position, replaced = upsert_csv_position(
            position,
            original_ticker=ticker,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    result = await update_saved_csv_portfolio()
    return {
        "status": "ok",
        "action": "updated" if replaced else "created",
        "position": saved_position,
        "positions": len(positions),
        "csv_path": str(resolve_csv_path()),
        "portfolio": result,
    }


@router.delete("/api/portfolio/csv-positions/{ticker}")
async def delete_csv_position_route(ticker: str):
    """Delete a single position from the local portfolio CSV."""
    from fetchers.csv_reader import delete_csv_position, resolve_csv_path
    from services.portfolio_builder import update_saved_csv_portfolio

    positions, deleted = delete_csv_position(ticker)
    if not deleted:
        return JSONResponse({"error": f"Position {ticker} not found"}, status_code=404)

    portfolio_result = {"status": "empty"}
    if positions:
        portfolio_result = await update_saved_csv_portfolio()
    else:
        portfolio_data["summary"] = PortfolioSummary(display_currency="USD")
        portfolio_data["source"] = "csv"
        portfolio_data["last_refresh"] = datetime.now()

    return {
        "status": "ok",
        "action": "deleted",
        "positions": len(positions),
        "csv_path": str(resolve_csv_path()),
        "portfolio": portfolio_result,
    }


@router.post("/api/portfolio/upload-csv")
async def upload_csv_portfolio(data: dict):
    """Import portfolio from CSV data uploaded by the frontend.

    Expects JSON body: {"positions": [{"ticker": "AAPL", "shares": 10, "buy_price": 150, ...}, ...]}
    """
    from fetchers.csv_reader import parse_csv_json, csv_positions_to_portfolio_format, save_csv_positions
    from services.portfolio_builder import build_portfolio_from_csv

    positions_raw = data.get("positions", [])
    if not positions_raw:
        return JSONResponse({"error": "No positions provided"}, status_code=400)

    # Parse and validate
    positions = parse_csv_json(positions_raw)
    if not positions:
        return JSONResponse({"error": "No valid positions found in CSV"}, status_code=400)

    saved_path = save_csv_positions(positions)

    # Fetch live prices + daily changes from yFinance
    tickers = [
        p['ticker']
        for p in positions
        if p.get("asset_type") != "prediction_market"
    ]
    prices = {}
    daily_changes = {}
    try:
        from fetchers.yfinance_data import quick_price_update
        prices, daily_changes = await quick_price_update(tickers)
    except Exception as e:
        logger.warning(f"Could not fetch live prices for CSV import: {e}")

    # Convert to portfolio format
    portfolio_positions = csv_positions_to_portfolio_format(positions, prices)

    # Build portfolio summary (same pipeline as Parqet)
    try:
        result = await build_portfolio_from_csv(portfolio_positions, daily_changes)
        return {
            "status": "ok",
            "positions_imported": len(portfolio_positions),
            "saved_to": str(saved_path),
            **result,
        }
    except Exception as e:
        logger.error(f"CSV import failed: {e}")
        return JSONResponse({"error": f"Import failed: {str(e)}"}, status_code=500)
