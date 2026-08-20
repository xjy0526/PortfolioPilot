"""Compatibility adapter from the dashboard CSV payload to the PostgreSQL ledger."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import Portfolio, PriceBar
from app.db.repositories import PortfolioRepository, PriceBarRepository, TransactionRepository
from app.services.portfolio_valuation import PortfolioValuationService, ValuationResult
from app.services.transaction_import import TransactionCsvImporter, TransactionImportResult
from app.services.transaction_ledger import TransactionLedgerService
from config import settings
from time_utils import utc_now

LEGACY_IMPORT_SOURCE = "legacy_dashboard_csv"
LEGACY_DECLARED_PRICE_SOURCE = "legacy_csv_user_supplied"
LEGACY_COST_FALLBACK_SOURCE = "legacy_csv_cost_basis_fallback"
LEGACY_CSV_FIELDS = (
    "ticker",
    "shares",
    "buy_price",
    "current_price",
    "buy_date",
    "currency",
    "sector",
    "name",
    "asset_type",
    "market",
    "exchange",
    "country",
    "note",
)


@dataclass(frozen=True, slots=True)
class LegacyCsvPortfolioImportResult:
    portfolio_id: uuid.UUID
    imported: TransactionImportResult
    position_rebuild: dict[str, object]
    valuation_snapshot: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        import_payload = self.imported.as_dict()
        return {
            **import_payload,
            "status": "failed" if self.imported.status == "failed" else "ok",
            "import_status": self.imported.status,
            "portfolio_id": str(self.portfolio_id),
            "positions_imported": self.imported.persisted_rows,
            "position_rebuild": self.position_rebuild,
            "valuation_snapshot": self.valuation_snapshot,
        }


class LegacyCsvPortfolioImportService:
    """Route legacy dashboard imports through the governed PostgreSQL services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolios = PortfolioRepository(session)
        self.transactions = TransactionRepository(session)
        self.prices = PriceBarRepository(session)

    async def import_positions(
        self,
        *,
        positions: Sequence[Mapping[str, object]],
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
    ) -> LegacyCsvPortfolioImportResult:
        cutoff = _as_utc(as_of or utc_now())
        portfolio = await self._writable_portfolio(principal, portfolio_id)
        content = _legacy_csv_bytes(
            positions,
            default_trade_date=_as_utc(portfolio.created_at).date().isoformat(),
        )
        imported = await TransactionCsvImporter(self.session).import_bytes(
            portfolio_id=portfolio.id,
            filename="dashboard-positions.csv",
            content=content,
            source=LEGACY_IMPORT_SOURCE,
        )
        if imported.status == "failed":
            return LegacyCsvPortfolioImportResult(
                portfolio_id=portfolio.id,
                imported=imported,
                position_rebuild={"status": "not_run", "reason": "transaction_import_failed"},
                valuation_snapshot={"status": "not_created"},
            )

        price_sources = await self._persist_declared_prices(
            import_batch_id=imported.import_batch_id,
            cutoff=cutoff,
        )
        rebuilt = await TransactionLedgerService(self.session).rebuild(
            portfolio.id,
            as_of=cutoff,
            knowledge_as_of=cutoff,
        )
        source_context: dict[str, object] = {
            "import_batch_id": str(imported.import_batch_id),
            "transaction_source": LEGACY_IMPORT_SOURCE,
            "price_sources": price_sources,
            "price_disclosure": (
                "Dashboard CSV prices are user supplied; buy_price is used only as an "
                "explicitly labelled fallback when current_price is absent."
            ),
        }
        valuation = await PortfolioValuationService(self.session).value(
            portfolio_id=portfolio.id,
            as_of=cutoff,
            knowledge_as_of=cutoff,
            source="ledger_rebuild",
            data_source_context=source_context,
        )
        return LegacyCsvPortfolioImportResult(
            portfolio_id=portfolio.id,
            imported=imported,
            position_rebuild={
                "status": "completed",
                "as_of": rebuilt.as_of.isoformat(),
                "position_count": len(rebuilt.positions),
                "cash_balances": {
                    currency: str(amount) for currency, amount in rebuilt.cash_balances.items()
                },
                "warnings": list(rebuilt.warnings),
                "history_completeness": rebuilt.history_completeness,
            },
            valuation_snapshot=_valuation_status(valuation, source_context),
        )

    async def _writable_portfolio(
        self,
        principal: Principal,
        portfolio_id: uuid.UUID | None,
    ) -> Portfolio:
        if portfolio_id is not None:
            portfolio = await self.portfolios.get_accessible(
                portfolio_id=portfolio_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                access="write",
                platform_admin=principal.is_platform_admin,
            )
            if portfolio is None:
                raise ValueError("portfolio not found")
            return portfolio

        configured_id: uuid.UUID | None = None
        if settings.DEFAULT_PORTFOLIO_ID:
            try:
                configured_id = uuid.UUID(settings.DEFAULT_PORTFOLIO_ID)
            except ValueError:
                configured_id = None
        if configured_id is not None:
            configured = await self.portfolios.get_accessible(
                portfolio_id=configured_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                access="write",
                platform_admin=principal.is_platform_admin,
            )
            if configured is not None:
                return configured

        for candidate in await self.portfolios.list_accessible(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            platform_admin=principal.is_platform_admin,
        ):
            writable = await self.portfolios.get_accessible(
                portfolio_id=candidate.id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                access="write",
                platform_admin=principal.is_platform_admin,
            )
            if writable is not None:
                return writable
        raise ValueError("no writable PostgreSQL portfolio is available")

    async def _persist_declared_prices(
        self,
        *,
        import_batch_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[dict[str, object]]:
        rows = await self.transactions.list_for_import_batch(import_batch_id)
        sources: dict[uuid.UUID, dict[str, object]] = {}
        for transaction in rows:
            if transaction.security_id is None or transaction.transaction_type != "opening_balance":
                continue
            raw = transaction.raw_payload or {}
            current_price = _optional_decimal(raw.get("current_price"))
            if current_price is not None:
                price = current_price
                source = LEGACY_DECLARED_PRICE_SOURCE
                supplied_field = "current_price"
            else:
                price = transaction.price or Decimal("0")
                source = LEGACY_COST_FALLBACK_SOURCE
                supplied_field = "buy_price"
            stored = await self.prices.upsert(
                PriceBar(
                    security_id=transaction.security_id,
                    trade_date=cutoff.date(),
                    source=source,
                    currency=transaction.currency,
                    close=price,
                    adjusted_close=price,
                    adjustment_factor=Decimal("1"),
                    data_as_of=cutoff,
                    is_final=True,
                    quality_status="valid",
                    raw_payload={
                        "user_supplied": True,
                        "research_only": True,
                        "fallback": source == LEGACY_COST_FALLBACK_SOURCE,
                        "supplied_field": supplied_field,
                        "import_batch_id": str(import_batch_id),
                    },
                )
            )
            sources[transaction.security_id] = {
                "security_id": str(transaction.security_id),
                "price_bar_id": str(stored.id),
                "source": source,
                "data_as_of": cutoff.isoformat(),
                "user_supplied": True,
                "fallback": source == LEGACY_COST_FALLBACK_SOURCE,
            }
        return list(sources.values())


def _legacy_csv_bytes(
    positions: Sequence[Mapping[str, object]],
    *,
    default_trade_date: str,
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=LEGACY_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for position in positions:
        normalized = {str(key).strip().lower(): value for key, value in position.items()}
        writer.writerow(
            {
                field: (
                    default_trade_date
                    if field == "buy_date" and not normalized.get(field)
                    else normalized.get(field, "")
                )
                for field in LEGACY_CSV_FIELDS
            }
        )
    return buffer.getvalue().encode("utf-8")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def _valuation_status(
    result: ValuationResult,
    source_context: dict[str, object],
) -> dict[str, object]:
    valuation = result.valuation
    return {
        "status": "created",
        "snapshot_id": str(valuation.id),
        "valuation_status": valuation.valuation_status,
        "as_of": _as_utc(valuation.as_of).isoformat(),
        "data_as_of": (_as_utc(valuation.data_as_of).isoformat() if valuation.data_as_of else None),
        "position_count": len(result.positions),
        "warnings": list(valuation.warnings),
        "data_sources": source_context,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
