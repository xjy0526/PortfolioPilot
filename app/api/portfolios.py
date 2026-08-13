"""PostgreSQL-backed portfolio ledger and valuation APIs."""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import (
    Principal,
    get_principal,
    require_portfolio_access,
    require_writable,
)
from app.db.models import Security
from app.db.repositories import (
    ImportBatchRepository,
    PortfolioRepository,
    PortfolioValuationRepository,
    PositionSnapshotRepository,
    TransactionRepository,
)
from app.services.portfolio_valuation import PortfolioValuationService
from app.services.transaction_import import TransactionCsvImporter
from app.services.transaction_ledger import TransactionLedgerService
from config import settings
from time_utils import utc_now

router = APIRouter(prefix="/api", tags=["portfolio-ledger"])
logger = logging.getLogger(__name__)


@router.get("/portfolios")
async def list_portfolios(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    portfolios = await PortfolioRepository(session).list_accessible(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        platform_admin=principal.is_platform_admin,
    )
    return [_portfolio_payload(item) for item in portfolios]


@router.get("/portfolios/{portfolio_id}")
async def get_portfolio(
    portfolio_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    portfolio = await require_portfolio_access(session, principal, portfolio_id, "read")
    return _portfolio_payload(portfolio)


@router.get("/portfolios/{portfolio_id}/transactions")
async def list_transactions(
    portfolio_id: uuid.UUID,
    as_of: datetime | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    await require_portfolio_access(session, principal, portfolio_id, "read")
    rows = await TransactionRepository(session).list_for_portfolio(
        portfolio_id,
        as_of=_as_utc(as_of) if as_of else None,
    )
    output: list[dict[str, object]] = []
    for row in rows:
        security = await session.get(Security, row.security_id) if row.security_id else None
        output.append(
            {
                "id": str(row.id),
                "external_id": row.external_id,
                "transaction_type": row.transaction_type,
                "ticker": security.canonical_symbol if security else None,
                "security_id": str(row.security_id) if row.security_id else None,
                "occurred_at": _as_utc(row.occurred_at).isoformat(),
                "settled_at": _as_utc(row.settled_at).isoformat() if row.settled_at else None,
                "quantity": row.quantity,
                "price": row.price,
                "gross_amount": row.gross_amount,
                "fees": row.fees,
                "taxes": row.taxes,
                "currency": row.currency,
                "source": row.source,
                "note": row.note,
                "import_batch_id": str(row.import_batch_id) if row.import_batch_id else None,
            }
        )
    return output


@router.get("/portfolios/{portfolio_id}/positions")
async def get_positions(
    portfolio_id: uuid.UUID,
    as_of: datetime | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    await require_portfolio_access(session, principal, portfolio_id, "read")
    rebuilt = await TransactionLedgerService(session).rebuild(
        portfolio_id,
        as_of=_as_utc(as_of or utc_now()),
    )
    positions: list[dict[str, object]] = []
    for row in rebuilt.positions:
        security = await session.get(Security, row.security_id)
        positions.append(
            {
                "security_id": str(row.security_id),
                "ticker": security.canonical_symbol if security else None,
                "quantity": row.quantity,
                "average_cost_native": row.average_cost_native,
                "cost_basis_native": row.cost_basis_native,
                "realized_pnl_native": row.realized_pnl_native,
                "native_currency": row.native_currency,
            }
        )
    return {
        "portfolio_id": str(portfolio_id),
        "as_of": rebuilt.as_of.isoformat(),
        "positions": positions,
        "cash_balances": rebuilt.cash_balances,
        "warnings": list(rebuilt.warnings),
        "last_transaction_at": (
            rebuilt.last_transaction_at.isoformat() if rebuilt.last_transaction_at else None
        ),
        "history_completeness": rebuilt.history_completeness,
    }


@router.get("/portfolios/{portfolio_id}/valuation")
async def get_valuation(
    portfolio_id: uuid.UUID,
    as_of: datetime | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    await require_portfolio_access(session, principal, portfolio_id, "read")
    cutoff = _as_utc(as_of or utc_now())
    valuation = await PortfolioValuationRepository(session).latest_at_or_before(
        portfolio_id,
        cutoff,
        preferred_source="ledger_rebuild",
    )
    if valuation is None:
        raise HTTPException(
            status_code=404,
            detail="No valuation snapshot exists at or before as_of; run rebuild first",
        )
    positions = await PositionSnapshotRepository(session).list_at(valuation.id)
    return await _valuation_payload(session, valuation, positions)


@router.post("/portfolios/{portfolio_id}/rebuild")
async def rebuild_portfolio(
    portfolio_id: uuid.UUID,
    as_of: datetime | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    require_writable()
    principal.require_role("operator", "admin", "platform_admin")
    await require_portfolio_access(session, principal, portfolio_id, "write")
    try:
        result = await PortfolioValuationService(session).value(
            portfolio_id=portfolio_id,
            as_of=_as_utc(as_of or utc_now()),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "Portfolio valuation rebuilt",
        extra={"portfolio_id": str(portfolio_id), "snapshot_id": str(result.valuation.id)},
    )
    return await _valuation_payload(session, result.valuation, list(result.positions))


@router.post("/portfolios/{portfolio_id}/imports/transactions")
async def import_transactions(
    portfolio_id: uuid.UUID,
    file: UploadFile = File(...),
    source: str = Query(default="csv_upload", min_length=1, max_length=80),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    require_writable()
    await require_portfolio_access(session, principal, portfolio_id, "write")
    content = await file.read(settings.MAX_TRANSACTION_IMPORT_BYTES + 1)
    if len(content) > settings.MAX_TRANSACTION_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds configured upload limit")
    try:
        result = await TransactionCsvImporter(session).import_bytes(
            portfolio_id=portfolio_id,
            filename=file.filename or "transactions.csv",
            content=content,
            source=source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "Transaction CSV processed",
        extra={
            "portfolio_id": str(portfolio_id),
            "import_batch_id": str(result.import_batch_id),
            "accepted": result.accepted_rows,
            "rejected": result.rejected_rows,
            "idempotent_replay": result.idempotent_replay,
        },
    )
    return result.as_dict()


@router.get("/import-batches/{batch_id}/errors")
async def download_import_errors(
    batch_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    batch = await ImportBatchRepository(session).get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Import batch not found")
    await require_portfolio_access(session, principal, batch.portfolio_id, "read")
    errors = list((batch.error_summary or {}).get("errors", []))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["line", "error", "row_json"])
    writer.writeheader()
    for error in errors:
        writer.writerow(
            {
                "line": error.get("line", ""),
                "error": error.get("error", ""),
                "row_json": json.dumps(error.get("row", {}), ensure_ascii=False),
            }
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="import-{batch_id}-errors.csv"'
        },
    )


def _portfolio_payload(portfolio) -> dict[str, object]:
    return {
        "id": str(portfolio.id),
        "user_id": str(portfolio.user_id),
        "tenant_id": portfolio.tenant_id,
        "name": portfolio.name,
        "description": portfolio.description,
        "base_currency": portfolio.base_currency,
        "cost_basis_method": portfolio.cost_basis_method,
        "display_timezone": portfolio.display_timezone,
        "benchmark_security_id": (
            str(portfolio.benchmark_security_id) if portfolio.benchmark_security_id else None
        ),
        "is_active": portfolio.is_active,
        "created_at": _as_utc(portfolio.created_at).isoformat(),
        "updated_at": _as_utc(portfolio.updated_at).isoformat(),
    }


async def _valuation_payload(session, valuation, positions) -> dict[str, object]:
    output_positions: list[dict[str, object]] = []
    for row in positions:
        security = await session.get(Security, row.security_id)
        output_positions.append(
            {
                "security_id": str(row.security_id),
                "ticker": security.canonical_symbol if security else None,
                "quantity": row.quantity,
                "average_cost": row.average_cost,
                "price_bar_id": str(row.price_bar_id) if row.price_bar_id else None,
                "fx_rate_id": str(row.fx_rate_id) if row.fx_rate_id else None,
                "native_price": row.native_price,
                "native_currency": row.native_currency,
                "valuation_fx_rate": row.valuation_fx_rate,
                "market_value_base": row.market_value_base,
                "cost_basis_base": row.cost_basis_base,
                "unrealized_pnl_base": row.unrealized_pnl_base,
                "cost_basis_native": row.cost_basis_native,
                "cost_basis_base_at_trade": row.cost_basis_base_at_trade,
                "local_price_pnl": row.local_price_pnl,
                "fx_pnl": row.fx_pnl,
                "total_pnl_base": row.total_pnl_base,
                "weight": row.weight,
                "base_currency": row.base_currency,
                "lineage": row.snapshot_data,
            }
        )
    return {
        "id": str(valuation.id),
        "portfolio_id": str(valuation.portfolio_id),
        "as_of": _as_utc(valuation.as_of).isoformat(),
        "data_as_of": (
            _as_utc(valuation.data_as_of).isoformat() if valuation.data_as_of else None
        ),
        "base_currency": valuation.base_currency,
        "total_market_value": valuation.total_market_value,
        "priced_market_value": valuation.priced_market_value,
        "total_cost_basis": valuation.total_cost_basis,
        "cash_value": valuation.cash_value,
        "unrealized_pnl": valuation.unrealized_pnl,
        "valuation_status": valuation.valuation_status,
        "priced_asset_count": valuation.priced_asset_count,
        "unpriced_asset_count": valuation.unpriced_asset_count,
        "unpriced_assets": valuation.unpriced_assets,
        "data_as_of_earliest": (
            _as_utc(valuation.data_as_of_earliest).isoformat()
            if valuation.data_as_of_earliest
            else None
        ),
        "data_as_of_latest": (
            _as_utc(valuation.data_as_of_latest).isoformat()
            if valuation.data_as_of_latest
            else None
        ),
        "max_staleness_days": valuation.max_staleness_days,
        "coverage_ratio": valuation.coverage_ratio,
        "history_completeness": valuation.history_completeness,
        "input_hash": valuation.input_hash,
        "source": valuation.source,
        "sync_run_id": str(valuation.sync_run_id) if valuation.sync_run_id else None,
        "cash_balances": valuation.cash_balances,
        "warnings": valuation.warnings,
        "positions": output_positions,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
