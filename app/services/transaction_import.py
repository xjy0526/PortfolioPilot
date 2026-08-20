"""Atomic CSV import into the PostgreSQL transaction ledger."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ImportBatch, Portfolio, Transaction
from app.db.repositories import ImportBatchRepository, PortfolioRepository, TransactionRepository
from app.services.position_rebuilder import LedgerValidationError, PositionRebuilder
from app.services.security_master import SecurityMasterService
from time_utils import utc_now

TRANSACTION_TYPES = {
    "opening_balance",
    "buy",
    "sell",
    "deposit",
    "withdrawal",
    "dividend",
    "fee",
    "tax",
    "split",
    "transfer_in",
    "transfer_out",
}
SECURITY_TRANSACTION_TYPES = {"opening_balance", "buy", "sell", "split"}


@dataclass(frozen=True, slots=True)
class TransactionImportResult:
    import_batch_id: uuid.UUID
    status: str
    total_rows: int
    accepted_rows: int
    duplicate_rows: int
    rejected_rows: int
    persisted_rows: int
    inserted_rows: int
    idempotent_replay: bool
    history_completeness: str
    errors: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "import_batch_id": str(self.import_batch_id),
            "status": self.status,
            "total_rows": self.total_rows,
            "accepted_rows": self.accepted_rows,
            "duplicate_rows": self.duplicate_rows,
            "rejected_rows": self.rejected_rows,
            "persisted_rows": self.persisted_rows,
            "inserted_rows": self.inserted_rows,
            "idempotent_replay": self.idempotent_replay,
            "history_completeness": self.history_completeness,
            "errors": list(self.errors),
            "error_report_url": f"/api/import-batches/{self.import_batch_id}/errors",
        }


@dataclass(frozen=True, slots=True)
class _NormalizedRow:
    line_number: int
    values: dict[str, object]
    source_record_hash: str
    security: dict[str, str] | None


class TransactionCsvImporter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.batches = ImportBatchRepository(session)
        self.transactions = TransactionRepository(session)
        self.portfolios = PortfolioRepository(session)
        self.security_master = SecurityMasterService(session)

    async def import_bytes(
        self,
        *,
        portfolio_id: uuid.UUID,
        filename: str,
        content: bytes,
        source: str = "csv_upload",
        allow_empty: bool = False,
        legacy_positions: bool | None = None,
        require_all_rows_valid: bool = False,
        validate_portfolio_state: bool = True,
    ) -> TransactionImportResult:
        portfolio = await self.portfolios.get(portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        file_sha256 = hashlib.sha256(content).hexdigest()
        safe_filename = PurePath(filename or "transactions.csv").name
        rows = _read_rows(content)
        legacy = (
            _is_legacy_positions_csv(rows)
            if legacy_positions is None
            else legacy_positions
        )
        batch, created = await self.batches.get_or_create(
            ImportBatch(
                portfolio_id=portfolio_id,
                source=source,
                source_filename=safe_filename,
                file_sha256=file_sha256,
                status="pending",
                total_rows=len(rows),
                accepted_rows=0,
                rejected_rows=0,
                error_summary={},
            )
        )
        if not created and batch.status in {
            "pending",
            "processing",
            "completed",
            "completed_with_errors",
            "failed",
            "duplicate",
        }:
            summary = batch.error_summary or {}
            duplicate_rows = batch.total_rows - batch.rejected_rows
            return TransactionImportResult(
                import_batch_id=batch.id,
                status="failed" if batch.status == "failed" else "duplicate",
                total_rows=batch.total_rows,
                accepted_rows=0,
                duplicate_rows=duplicate_rows,
                rejected_rows=batch.rejected_rows,
                persisted_rows=0,
                inserted_rows=0,
                idempotent_replay=True,
                history_completeness=str(summary.get("history_completeness", "complete")),
                errors=tuple(summary.get("errors", [])),
            )

        batch.status = "processing"
        await self.session.flush()

        normalized: list[_NormalizedRow] = []
        errors: list[dict[str, object]] = []
        if not rows and not allow_empty:
            errors.append({"line": 0, "error": "CSV contains no data rows", "row": {}})
        for line_number, csv_row in enumerate(rows, start=2):
            try:
                normalized.append(
                    _normalize_legacy_row(csv_row, line_number, portfolio)
                    if legacy
                    else _normalize_transaction_row(csv_row, line_number, portfolio)
                )
            except ValueError as exc:
                errors.append(
                    {
                        "line": line_number,
                        "error": str(exc),
                        "row": _safe_error_row(csv_row),
                    }
                )

        normalized = [_with_source_identity(row, file_sha256=file_sha256) for row in normalized]
        persisted = 0
        duplicates = 0
        atomic_failure = (not rows and not allow_empty) or (
            require_all_rows_valid and bool(errors)
        )
        if require_all_rows_valid and errors and rows:
            errors.append(
                {
                    "line": 0,
                    "error": "Snapshot replacement requires every row to be valid",
                    "row": {},
                }
            )
        if not atomic_failure:
            savepoint = await self.session.begin_nested()
            try:
                for normalized_row in normalized:
                    security_id = None
                    if normalized_row.security is not None:
                        security_input = normalized_row.security
                        security = await self.security_master.resolve_or_create(
                            ticker=security_input["ticker"],
                            exchange=security_input["exchange"],
                            currency=security_input["currency"],
                            market=security_input.get("market", ""),
                            country=security_input.get("country", ""),
                            name=security_input.get("name", ""),
                            asset_type=security_input.get("asset_type", "equity"),
                            sector=security_input.get("sector", "Unknown"),
                        )
                        security_id = security.id
                    transaction_values = {**normalized_row.values, "source": source}
                    raw_value = transaction_values.get("raw_payload")
                    raw_payload = dict(raw_value) if isinstance(raw_value, dict) else {}
                    raw_payload.update(
                        {
                            "source_file_sha256": file_sha256,
                            "source_row_number": normalized_row.line_number,
                        }
                    )
                    transaction_values["raw_payload"] = raw_payload
                    transaction = Transaction(
                        portfolio_id=portfolio_id,
                        security_id=security_id,
                        import_batch_id=batch.id,
                        source_record_hash=normalized_row.source_record_hash,
                        **transaction_values,
                    )
                    _, was_created = await self.transactions.add_idempotent(transaction)
                    persisted += int(was_created)
                    duplicates += int(not was_created)

                if validate_portfolio_state:
                    all_transactions = (
                        await self.transactions.list_effective_for_portfolio(portfolio_id)
                    )
                    PositionRebuilder().rebuild(
                        portfolio_id=portfolio_id,
                        transactions=all_transactions,
                        as_of=datetime.max.replace(tzinfo=UTC),
                    )
            except Exception as exc:
                await savepoint.rollback()
                message = (
                    str(exc)
                    if isinstance(exc, (LedgerValidationError, ValueError))
                    else f"Atomic import failed: {type(exc).__name__}"
                )
                errors.append({"line": 0, "error": message, "row": {}})
                atomic_failure = True
                persisted = 0
                duplicates = 0
            else:
                await savepoint.commit()

        history = _history_completeness(normalized, legacy=legacy)
        batch.total_rows = len(rows)
        batch.accepted_rows = persisted
        batch.rejected_rows = len(rows) if atomic_failure else len(rows) - len(normalized)
        if (
            not atomic_failure
            and batch.rejected_rows == 0
            and (batch.total_rows > 0 or allow_empty)
        ):
            batch.status = "completed"
        elif not atomic_failure and (persisted > 0 or duplicates > 0):
            batch.status = "completed_with_errors"
        else:
            batch.status = "failed"
        batch.completed_at = utc_now()
        batch.error_summary = {
            "errors": errors,
            "accepted_rows": persisted,
            "duplicate_rows": duplicates,
            "persisted_rows": persisted,
            "inserted_rows": persisted,
            "history_completeness": history,
            "legacy_positions_csv": legacy,
        }
        await self.session.flush()
        return TransactionImportResult(
            import_batch_id=batch.id,
            status=batch.status,
            total_rows=batch.total_rows,
            accepted_rows=batch.accepted_rows,
            duplicate_rows=duplicates,
            rejected_rows=batch.rejected_rows,
            persisted_rows=persisted,
            inserted_rows=persisted,
            idempotent_replay=False,
            history_completeness=history,
            errors=tuple(errors),
        )


def _history_completeness(rows: list[_NormalizedRow], *, legacy: bool) -> str:
    if legacy:
        return "opening_balance_only"
    transaction_types = {str(row.values["transaction_type"]) for row in rows}
    if "opening_balance" not in transaction_types:
        return "complete"
    if transaction_types <= {"opening_balance", "deposit"}:
        return "opening_balance_only"
    return "partial_history"


def _read_rows(content: bytes) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV header is required")
    return [
        {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}
        for row in reader
        if any(str(value or "").strip() for value in row.values())
    ]


def _is_legacy_positions_csv(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    fields = set(rows[0])
    return "transaction_type" not in fields and {"ticker", "shares", "buy_price"} <= fields


def _normalize_transaction_row(
    row: dict[str, str], line_number: int, portfolio: Portfolio
) -> _NormalizedRow:
    tx_type = row.get("transaction_type", "").strip().lower()
    if tx_type not in TRANSACTION_TYPES:
        raise ValueError(f"unsupported transaction_type: {tx_type or '<empty>'}")
    quantity = _decimal(row.get("quantity"), "quantity", default=ZERO)
    price = _optional_decimal(row.get("price"), "price")
    fees = _decimal(row.get("fees"), "fees", default=ZERO)
    taxes = _decimal(row.get("taxes"), "taxes", default=ZERO)
    currency = _currency(row.get("currency"))
    ticker = row.get("ticker", "").strip().upper()
    exchange = row.get("exchange", "").strip().upper()
    if tx_type in SECURITY_TRANSACTION_TYPES and not ticker:
        raise ValueError("ticker is required for security transactions")
    if tx_type in {"buy", "sell", "opening_balance"} and price is None:
        raise ValueError(f"price is required for {tx_type}")
    if tx_type == "split" and quantity <= ZERO:
        raise ValueError("split quantity is the split ratio and must be positive")
    gross = _optional_decimal(row.get("gross_amount"), "gross_amount")
    if gross is None:
        gross = quantity * price if price is not None else quantity
    occurred_at = _parse_date(row.get("trade_date"), portfolio.display_timezone)
    settled_at = _parse_optional_date(row.get("settlement_date"), portfolio.display_timezone)
    values: dict[str, object] = {
        "transaction_type": tx_type,
        "occurred_at": occurred_at,
        "settled_at": settled_at,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross,
        "fees": fees,
        "taxes": taxes,
        "currency": currency,
        "source": "csv_upload",
        "external_id": row.get("external_id") or None,
        "note": row.get("note", ""),
        "raw_payload": row,
    }
    security = None
    if ticker:
        security = {
            "ticker": ticker,
            "exchange": exchange,
            "currency": currency,
            "market": row.get("market", ""),
            "country": row.get("country", ""),
            "name": row.get("name", ticker),
            "asset_type": row.get("asset_type", "equity"),
            "sector": row.get("sector", "Unknown"),
        }
    return _normalized(line_number, values, security)


def _normalize_legacy_row(
    row: dict[str, str], line_number: int, portfolio: Portfolio
) -> _NormalizedRow:
    ticker = row.get("ticker", "").strip().upper()
    if not ticker:
        raise ValueError("legacy position row requires a ticker")
    quantity = _decimal(row.get("shares"), "shares")
    price = _decimal(row.get("buy_price"), "buy_price")
    if quantity <= ZERO or price < ZERO:
        raise ValueError("legacy shares must be positive and buy_price non-negative")
    currency = _currency(row.get("currency"))
    trade_date = row.get("buy_date") or utc_now().date().isoformat()
    is_cash = ticker == "CASH" or row.get("asset_type", "").lower() == "cash"
    values: dict[str, object] = {
        "transaction_type": "deposit" if is_cash else "opening_balance",
        "occurred_at": _parse_date(trade_date, portfolio.display_timezone),
        "settled_at": None,
        "quantity": ZERO if is_cash else quantity,
        "price": None if is_cash else price,
        "gross_amount": quantity * price,
        "fees": ZERO,
        "taxes": ZERO,
        "currency": currency,
        "source": "csv_upload",
        "external_id": row.get("external_id") or None,
        "note": row.get("note")
        or (
            "Imported from legacy position CSV as opening cash balance"
            if is_cash
            else "Imported from legacy position CSV as opening balance"
        ),
        "raw_payload": {**row, "history_completeness": "opening_balance_only"},
    }
    security = None if is_cash else {
        "ticker": ticker,
        "exchange": row.get("exchange", ""),
        "currency": currency,
        "market": row.get("market", ""),
        "country": row.get("country", ""),
        "name": row.get("name", ticker),
        "asset_type": row.get("asset_type", "equity"),
        "sector": row.get("sector", "Unknown"),
    }
    return _normalized(line_number, values, security)


def _normalized(
    line_number: int,
    values: dict[str, object],
    security: dict[str, str] | None,
) -> _NormalizedRow:
    canonical = {
        key: value.isoformat() if isinstance(value, (datetime, date)) else str(value)
        for key, value in sorted(values.items())
        if key != "raw_payload"
    }
    if security:
        canonical["security"] = json.dumps(security, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return _NormalizedRow(line_number, values, digest, security)


def _with_source_identity(
    row: _NormalizedRow,
    *,
    file_sha256: str,
) -> _NormalizedRow:
    """Scope row idempotency to a stable file occurrence, not only its values."""
    digest = hashlib.sha256(
        json.dumps(
            {
                "canonical_row_hash": row.source_record_hash,
                "source_file_sha256": file_sha256,
                "source_row_number": row.line_number,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return _NormalizedRow(row.line_number, row.values, digest, row.security)


ZERO = Decimal("0")


def _decimal(value: str | None, field: str, *, default: Decimal | None = None) -> Decimal:
    if value is None or not str(value).strip():
        if default is not None:
            return default
        raise ValueError(f"{field} is required")
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if result < ZERO:
        raise ValueError(f"{field} must be non-negative")
    return result


def _optional_decimal(value: str | None, field: str) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    return _decimal(value, field)


def _currency(value: str | None) -> str:
    currency = str(value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be an explicit three-letter ISO code")
    return currency


def _parse_date(value: str | None, timezone_name: str) -> datetime:
    if not value:
        raise ValueError("trade_date is required")
    try:
        parsed = date.fromisoformat(value.strip())
        local = datetime.combine(parsed, datetime.min.time(), tzinfo=ZoneInfo(timezone_name))
    except (ValueError, KeyError) as exc:
        raise ValueError("dates must use YYYY-MM-DD") from exc
    return local.astimezone(UTC)


def _parse_optional_date(value: str | None, timezone_name: str) -> datetime | None:
    return _parse_date(value, timezone_name) if value and value.strip() else None


def _safe_error_row(row: dict[str, str]) -> dict[str, str]:
    return {key: value[:500] for key, value in row.items()}
