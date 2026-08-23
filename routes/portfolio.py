"""PortfolioPilot - Portfolio & Daten API-Routes.

GET-Endpoints für Dashboard, Portfolio, Aktien, Rebalancing, etc.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal, require_writable
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from app.services.legacy_csv_import import LegacyCsvPortfolioImportService
from app.db.models import Security
from app.db.repositories import PortfolioValuationRepository, TransactionRepository
from state import portfolio_data
from config import settings
from models import SectorAllocation

logger = logging.getLogger(__name__)

router = APIRouter()
tech_radar_router = APIRouter()

STATIC_DIR = Path(__file__).parent.parent / "static"


@router.get("/")
async def index():
    """Serve the dashboard."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return FileResponse(str(html_file))
    return JSONResponse({"error": "Dashboard nicht gefunden"}, status_code=404)


@router.get("/api/portfolio")
async def get_portfolio(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Portfolio-Übersicht mit Scores."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return JSONResponse(
            {
                "error": "No PostgreSQL valuation snapshot available",
                "refreshing": False,
                "source": "postgresql",
            },
            status_code=503,
        )
    return context.summary.model_dump()


@router.get("/api/stock/{ticker}")
async def get_stock(
    ticker: str,
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Detaildaten einer einzelnen Aktie."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)
    summary = context.summary

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
async def get_portfolio_history(
    days: int = 90,
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Portfolio-Verlauf: Investiertes Kapital + aktueller Wert ueber Zeit.

    Datenquellen (in Prioritaet):
    1. Parqet Activities -> rekonstruierte Investment-Timeline
    2. Lokale Snapshots aus vorherigen Refreshes
    3. Aktueller Portfoliowert als einzelner Datenpunkt
    """
    explicit_demo = portfolio_data.get("summary")
    if explicit_demo and explicit_demo.is_demo:
        from fetchers.demo_data import get_demo_portfolio_history

        demo_days = 365 if days <= 0 else days
        return get_demo_portfolio_history(days=demo_days)

    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return []
    since = None if days <= 0 else datetime.now(UTC) - timedelta(days=days)
    snapshots = await PortfolioValuationRepository(session).list_for_portfolio(
        context.portfolio.id,
        since=since,
    )
    return [
        {
            "date": item.valuation_date.isoformat(),
            "total_value": (
                float(item.total_market_value)
                if item.total_market_value is not None
                else None
            ),
            "priced_market_value": float(item.priced_market_value),
            "invested_capital": (
                float(item.total_cost_basis + (item.cash_value or 0))
                if item.total_cost_basis is not None
                else None
            ),
            "valuation_status": item.valuation_status,
            "coverage_ratio": float(item.coverage_ratio),
            "as_of": item.as_of.isoformat(),
            "input_hash": item.input_hash,
            "source": "postgresql_valuation_snapshot",
        }
        for item in snapshots
    ]


@router.get("/api/portfolio/activities")
async def get_portfolio_activities(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Alle Kauf/Verkauf/Dividenden-Transaktionen von Parqet."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return []
    rows = await TransactionRepository(session).list_for_portfolio(context.portfolio.id)
    output = []
    for row in rows:
        security = await session.get(Security, row.security_id) if row.security_id else None
        output.append(
            {
                "id": str(row.id),
                "external_id": row.external_id,
                "date": row.occurred_at.date().isoformat(),
                "type": row.transaction_type,
                "ticker": security.canonical_symbol if security else None,
                "amount": float(row.gross_amount),
                "quantity": float(row.quantity),
                "price": float(row.price) if row.price is not None else None,
                "fees": float(row.fees),
                "taxes": float(row.taxes),
                "currency": row.currency,
                "source": row.source,
            }
        )
    return output


@router.get("/api/rebalancing")
async def get_rebalancing(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Rebalancing-Empfehlungen."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None or not context.summary.rebalancing:
        return JSONResponse({"error": "Keine Rebalancing-Daten"}, status_code=503)
    return context.summary.rebalancing.model_dump()


@tech_radar_router.get("/api/tech-picks")
async def get_tech_picks(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Tägliche Tech-Empfehlungen."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)
    return [p.model_dump() for p in context.summary.tech_picks]


@router.get("/api/sectors")
async def get_sectors(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Sektor-Allokation."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)
    summary = context.summary

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
async def get_asset_allocation(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Asset-/Markt-Allokation für globale Aktien, A-Shares und Polymarket."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None:
        return JSONResponse({"error": "Daten werden geladen..."}, status_code=503)
    summary = context.summary

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
async def get_fear_greed(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Fear & Greed Index."""
    context = await LegacyPortfolioAdapter(session).load(
        portfolio_id=portfolio_id, principal=principal
    )
    if context is None or not context.summary.fear_greed:
        return {"value": 50, "label": "Neutral", "source": "N/A"}
    return context.summary.fear_greed.model_dump()


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
async def get_csv_positions(
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """List the active PostgreSQL-backed dashboard holdings snapshot."""
    try:
        return await LegacyCsvPortfolioImportService(session).list_managed_positions(
            principal=principal,
            portfolio_id=portfolio_id,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.post("/api/portfolio/csv-positions")
async def create_csv_position(
    data: dict[str, object],
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Add or replace one row in the active dashboard holdings snapshot."""
    require_writable()
    principal.require_role("operator", "admin", "platform_admin")
    position = data.get("position", data)
    if not isinstance(position, dict):
        return JSONResponse({"error": "position must be an object"}, status_code=400)
    try:
        portfolio_id = _payload_portfolio_id(data)
        result, saved_position, replaced, position_count = (
            await LegacyCsvPortfolioImportService(session).upsert_position(
                position=position,
                principal=principal,
                portfolio_id=portfolio_id,
            )
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    payload = result.as_dict()
    if result.imported.status == "failed":
        return JSONResponse(payload, status_code=422)
    return {
        **payload,
        "status": "ok",
        "action": "updated" if replaced else "created",
        "position": saved_position,
        "positions": position_count,
        "portfolio": _legacy_mutation_portfolio_payload(payload),
    }


@router.put("/api/portfolio/csv-positions/{ticker}")
async def update_csv_position(
    ticker: str,
    data: dict[str, object],
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Update one row by replacing the active dashboard snapshot."""
    require_writable()
    principal.require_role("operator", "admin", "platform_admin")
    position = data.get("position", data)
    if not isinstance(position, dict):
        return JSONResponse({"error": "position must be an object"}, status_code=400)
    try:
        requested_portfolio = portfolio_id or _payload_portfolio_id(data)
        result, saved_position, replaced, position_count = (
            await LegacyCsvPortfolioImportService(session).upsert_position(
                position=position,
                principal=principal,
                portfolio_id=requested_portfolio,
                original_ticker=ticker,
            )
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    payload = result.as_dict()
    if result.imported.status == "failed":
        return JSONResponse(payload, status_code=422)
    return {
        **payload,
        "status": "ok",
        "action": "updated" if replaced else "created",
        "position": saved_position,
        "positions": position_count,
        "portfolio": _legacy_mutation_portfolio_payload(payload),
    }


@router.delete("/api/portfolio/csv-positions/{ticker}")
async def delete_csv_position_route(
    ticker: str,
    portfolio_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Delete one row by replacing the active dashboard snapshot."""
    require_writable()
    principal.require_role("operator", "admin", "platform_admin")
    try:
        result, deleted, position_count = (
            await LegacyCsvPortfolioImportService(session).delete_position(
                ticker=ticker,
                principal=principal,
                portfolio_id=portfolio_id,
            )
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    if not deleted:
        return JSONResponse({"error": f"Position {ticker} not found"}, status_code=404)
    assert result is not None
    payload = result.as_dict()
    if result.imported.status == "failed":
        return JSONResponse(payload, status_code=422)
    return {
        **payload,
        "status": "ok",
        "action": "deleted",
        "positions": position_count,
        "portfolio": _legacy_mutation_portfolio_payload(payload),
    }


def _payload_portfolio_id(data: dict[str, object]) -> uuid.UUID | None:
    requested = data.get("portfolio_id")
    if requested is None or requested == "":
        return None
    try:
        return uuid.UUID(str(requested))
    except ValueError as exc:
        raise ValueError("portfolio_id must be a UUID") from exc


def _legacy_mutation_portfolio_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "portfolio_id": payload["portfolio_id"],
        "snapshot_generation": payload["snapshot_generation"],
        "position_rebuild": payload["position_rebuild"],
        "valuation_snapshot": payload["valuation_snapshot"],
    }


@router.post("/api/portfolio/upload-csv")
async def upload_csv_portfolio(
    data: dict[str, object],
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    """Compatibility endpoint backed by the PostgreSQL transaction ledger.

    Expects JSON body: {"positions": [{"ticker": "AAPL", "shares": 10, "buy_price": 150, ...}, ...]}
    """
    require_writable()
    principal.require_role("operator", "admin", "platform_admin")
    positions_raw = data.get("positions", [])
    if not isinstance(positions_raw, list) or not positions_raw:
        return JSONResponse({"error": "No positions provided"}, status_code=400)
    positions: list[dict[str, object]] = []
    for row in positions_raw:
        if not isinstance(row, dict):
            return JSONResponse({"error": "Each position must be an object"}, status_code=400)
        positions.append({str(key): value for key, value in row.items()})

    try:
        portfolio_id = _payload_portfolio_id(data)
        result = await LegacyCsvPortfolioImportService(session).import_positions(
            positions=positions,
            principal=principal,
            portfolio_id=portfolio_id,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    payload = result.as_dict()
    logger.info(
        "Legacy dashboard CSV routed to PostgreSQL ledger",
        extra={
            "portfolio_id": payload["portfolio_id"],
            "import_batch_id": payload["import_batch_id"],
            "persisted_rows": payload["persisted_rows"],
            "duplicate_rows": payload["duplicate_rows"],
            "rejected_rows": payload["rejected_rows"],
        },
    )
    if result.imported.status == "failed":
        return JSONResponse(payload, status_code=422)
    return payload
