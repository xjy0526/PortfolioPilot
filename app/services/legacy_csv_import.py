"""PostgreSQL compatibility adapter for replaceable dashboard holdings snapshots."""

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
from app.db.models import (
    ImportBatch,
    LegacySnapshotGeneration,
    Portfolio,
    PortfolioValuationSnapshot,
    PriceBar,
    Security,
    Transaction,
)
from app.db.repositories import (
    LegacySnapshotGenerationRepository,
    PortfolioRepository,
    PortfolioValuationRepository,
    PositionSnapshotRepository,
    PriceBarRepository,
    TransactionRepository,
)
from app.domain import PositionRebuildResult
from app.services.portfolio_valuation import PortfolioValuationService, ValuationResult
from app.services.transaction_import import TransactionCsvImporter, TransactionImportResult
from app.services.transaction_ledger import TransactionLedgerService
from config import settings
from time_utils import utc_now

LEGACY_IMPORT_SOURCE = "legacy_dashboard_csv"
LEGACY_DECLARED_PRICE_SOURCE = "legacy_csv_user_supplied"
LEGACY_COST_FALLBACK_SOURCE = "legacy_csv_cost_basis_fallback"
LEGACY_VALUATION_SOURCE = "ledger_rebuild"
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
    snapshot_generation: dict[str, object]
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
            "snapshot_generation": self.snapshot_generation,
            "position_rebuild": self.position_rebuild,
            "valuation_snapshot": self.valuation_snapshot,
        }


class LegacyCsvPortfolioImportService:
    """Treat dashboard holdings as one replaceable, auditable PostgreSQL snapshot."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolios = PortfolioRepository(session)
        self.transactions = TransactionRepository(session)
        self.generations = LegacySnapshotGenerationRepository(session)
        self.valuations = PortfolioValuationRepository(session)
        self.position_snapshots = PositionSnapshotRepository(session)
        self.prices = PriceBarRepository(session)

    async def import_positions(
        self,
        *,
        positions: Sequence[Mapping[str, object]],
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
    ) -> LegacyCsvPortfolioImportResult:
        """Compatibility alias for replacing the active dashboard snapshot."""
        return await self.replace_positions(
            positions=positions,
            principal=principal,
            portfolio_id=portfolio_id,
            as_of=as_of,
        )

    async def replace_positions(
        self,
        *,
        positions: Sequence[Mapping[str, object]],
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
    ) -> LegacyCsvPortfolioImportResult:
        cutoff = _as_utc(as_of or utc_now())
        portfolio = await self._writable_portfolio(principal, portfolio_id)
        async with self.session.begin_nested():
            return await self._replace_snapshot_locked(
                portfolio=portfolio,
                positions=positions,
                cutoff=cutoff,
            )

    async def list_managed_positions(
        self,
        *,
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        portfolio = await self._resolve_portfolio(
            principal,
            portfolio_id,
            access="read",
            for_update=False,
        )
        generation = await self.generations.get_active(portfolio.id)
        positions = await self._active_position_payloads(
            portfolio.id,
            include_cash=False,
            generation=generation,
        )
        return {
            "exists": generation is not None,
            "sample_fallback": False,
            "portfolio_id": str(portfolio.id),
            "import_batch_id": (
                str(generation.import_batch_id) if generation is not None else None
            ),
            "snapshot_generation": (
                _generation_payload(generation, created=False, superseded=None)
                if generation is not None
                else None
            ),
            "positions": positions,
        }

    async def upsert_position(
        self,
        *,
        position: Mapping[str, object],
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
        original_ticker: str | None = None,
        as_of: datetime | None = None,
    ) -> tuple[LegacyCsvPortfolioImportResult, dict[str, object], bool, int]:
        cutoff = _as_utc(as_of or utc_now())
        portfolio = await self._resolve_portfolio(
            principal,
            portfolio_id,
            access="write",
            for_update=True,
        )
        async with self.session.begin_nested():
            current = await self._active_position_payloads(
                portfolio.id,
                include_cash=True,
            )
            normalized = _normalized_position_mapping(position)
            new_key = str(normalized.get("ticker", "")).strip().upper()
            original_key = str(original_ticker or new_key).strip().upper()
            replaced = False
            updated: list[dict[str, object]] = []
            for existing in current:
                existing_key = str(existing.get("ticker", "")).strip().upper()
                if existing_key in {original_key, new_key}:
                    replaced = True
                    continue
                updated.append(existing)
            updated.append(normalized)
            result = await self._replace_snapshot_locked(
                portfolio=portfolio,
                positions=updated,
                cutoff=cutoff,
            )
            visible = await self._active_position_payloads(
                portfolio.id,
                include_cash=False,
            )
            saved = next(
                (
                    item
                    for item in visible
                    if str(item.get("ticker", "")).upper() == new_key
                ),
                normalized,
            )
            return result, saved, replaced, len(visible)

    async def delete_position(
        self,
        *,
        ticker: str,
        principal: Principal,
        portfolio_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
    ) -> tuple[LegacyCsvPortfolioImportResult | None, bool, int]:
        cutoff = _as_utc(as_of or utc_now())
        portfolio = await self._resolve_portfolio(
            principal,
            portfolio_id,
            access="write",
            for_update=True,
        )
        async with self.session.begin_nested():
            current = await self._active_position_payloads(
                portfolio.id,
                include_cash=True,
            )
            ticker_key = ticker.strip().upper()
            updated = [
                item
                for item in current
                if str(item.get("ticker", "")).strip().upper() != ticker_key
            ]
            deleted = len(updated) != len(current)
            if not deleted:
                return None, False, len(
                    [item for item in current if item.get("ticker") != "CASH"]
                )
            result = await self._replace_snapshot_locked(
                portfolio=portfolio,
                positions=updated,
                cutoff=cutoff,
            )
            visible = await self._active_position_payloads(
                portfolio.id,
                include_cash=False,
            )
            return result, True, len(visible)

    async def _replace_snapshot_locked(
        self,
        *,
        portfolio: Portfolio,
        positions: Sequence[Mapping[str, object]],
        cutoff: datetime,
    ) -> LegacyCsvPortfolioImportResult:
        content = _legacy_csv_bytes(
            positions,
            default_trade_date=_as_utc(portfolio.created_at).date().isoformat(),
        )
        imported = await TransactionCsvImporter(self.session).import_bytes(
            portfolio_id=portfolio.id,
            filename="dashboard-positions.csv",
            content=content,
            source=LEGACY_IMPORT_SOURCE,
            allow_empty=True,
            legacy_positions=True,
            require_all_rows_valid=True,
            validate_portfolio_state=False,
        )
        if imported.status == "failed":
            return LegacyCsvPortfolioImportResult(
                portfolio_id=portfolio.id,
                imported=imported,
                snapshot_generation={"status": "not_activated"},
                position_rebuild={
                    "status": "not_run",
                    "reason": "transaction_import_failed",
                },
                valuation_snapshot={"status": "not_created"},
            )

        batch = await self.session.get(ImportBatch, imported.import_batch_id)
        if batch is None:
            raise RuntimeError("import batch disappeared before snapshot activation")
        imported_rows = await self.transactions.list_for_import_batch(batch.id)
        generation, created, superseded = await self.generations.activate(
            portfolio_id=portfolio.id,
            import_batch_id=batch.id,
            source=LEGACY_IMPORT_SOURCE,
            activated_at=cutoff,
            position_count=len(imported_rows),
            generation_metadata={
                "file_sha256": batch.file_sha256,
                "source_filename": batch.source_filename,
                "history_completeness": imported.history_completeness,
                "idempotent_replay": imported.idempotent_replay,
            },
        )
        generation_payload = _generation_payload(
            generation,
            created=created,
            superseded=superseded,
        )
        source_context = {
            "import_batch_id": str(imported.import_batch_id),
            "transaction_source": LEGACY_IMPORT_SOURCE,
            "snapshot_generation_id": str(generation.id),
            "snapshot_generation_number": generation.generation_number,
            "snapshot_generation_created": created,
        }

        rebuilt = await TransactionLedgerService(self.session).rebuild(
            portfolio.id,
            as_of=cutoff,
            knowledge_as_of=cutoff,
        )
        if not created:
            existing = await self._existing_valuation(
                portfolio.id,
                generation,
                cutoff,
            )
            if existing is not None:
                valuation, position_count = existing
                return LegacyCsvPortfolioImportResult(
                    portfolio_id=portfolio.id,
                    imported=imported,
                    snapshot_generation=generation_payload,
                    position_rebuild=_rebuild_status(rebuilt),
                    valuation_snapshot=_stored_valuation_status(
                        valuation,
                        position_count=position_count,
                        status="unchanged",
                    ),
                )

        price_sources = await self._persist_declared_prices(
            import_batch_id=imported.import_batch_id,
            generation_id=generation.id,
            cutoff=cutoff,
        )
        source_context.update(
            {
                "price_sources": price_sources,
                "price_disclosure": (
                    "Dashboard CSV prices are user supplied; buy_price is used only "
                    "as an explicitly labelled fallback when current_price is absent."
                ),
            }
        )
        valuation_result = await PortfolioValuationService(self.session).value(
            portfolio_id=portfolio.id,
            as_of=cutoff,
            knowledge_as_of=cutoff,
            source=LEGACY_VALUATION_SOURCE,
            data_source_context=source_context,
        )
        return LegacyCsvPortfolioImportResult(
            portfolio_id=portfolio.id,
            imported=imported,
            snapshot_generation=generation_payload,
            position_rebuild=_rebuild_status(rebuilt),
            valuation_snapshot=_valuation_status(valuation_result, source_context),
        )

    async def _existing_valuation(
        self,
        portfolio_id: uuid.UUID,
        generation: LegacySnapshotGeneration,
        cutoff: datetime,
    ) -> tuple[PortfolioValuationSnapshot, int] | None:
        valuation = await self.valuations.latest_for_source(
            portfolio_id,
            source=LEGACY_VALUATION_SOURCE,
            as_of=cutoff,
        )
        if valuation is None:
            return None
        context = valuation.config_snapshot.get("data_source_context", {})
        if not isinstance(context, dict):
            return None
        generation_id = context.get("legacy_snapshot_generation_id") or context.get(
            "snapshot_generation_id"
        )
        if str(generation_id or "") != str(generation.id):
            return None
        rows = await self.position_snapshots.list_at(valuation.id)
        return valuation, len(rows)

    async def _resolve_portfolio(
        self,
        principal: Principal,
        portfolio_id: uuid.UUID | None,
        *,
        access: str,
        for_update: bool,
    ) -> Portfolio:
        if portfolio_id is not None:
            portfolio = await self.portfolios.get_accessible(
                portfolio_id=portfolio_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                access=access,
                platform_admin=principal.is_platform_admin,
                for_update=for_update,
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
                access=access,
                platform_admin=principal.is_platform_admin,
                for_update=for_update,
            )
            if configured is not None:
                return configured

        for candidate in await self.portfolios.list_accessible(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            platform_admin=principal.is_platform_admin,
        ):
            accessible = await self.portfolios.get_accessible(
                portfolio_id=candidate.id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                access=access,
                platform_admin=principal.is_platform_admin,
                for_update=for_update,
            )
            if accessible is not None:
                return accessible
        access_label = "writable" if access == "write" else "readable"
        raise ValueError(f"no {access_label} PostgreSQL portfolio is available")

    async def _writable_portfolio(
        self,
        principal: Principal,
        portfolio_id: uuid.UUID | None,
    ) -> Portfolio:
        """Retained for compatibility with focused unit tests and internal callers."""
        return await self._resolve_portfolio(
            principal,
            portfolio_id,
            access="write",
            for_update=True,
        )

    async def _active_position_payloads(
        self,
        portfolio_id: uuid.UUID,
        *,
        include_cash: bool,
        generation: LegacySnapshotGeneration | None = None,
    ) -> list[dict[str, object]]:
        active = generation or await self.generations.get_active(portfolio_id)
        if active is None:
            return []
        rows = await self.transactions.list_for_import_batch(active.import_batch_id)
        output: list[dict[str, object]] = []
        for transaction in rows:
            security = (
                await self.session.get(Security, transaction.security_id)
                if transaction.security_id is not None
                else None
            )
            position = _transaction_position_payload(transaction, security)
            if not include_cash and position["ticker"] == "CASH":
                continue
            output.append(position)
        return output

    async def _persist_declared_prices(
        self,
        *,
        import_batch_id: uuid.UUID,
        generation_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[dict[str, object]]:
        rows = await self.transactions.list_for_import_batch(import_batch_id)
        sources: dict[uuid.UUID, dict[str, object]] = {}
        for transaction in rows:
            if (
                transaction.security_id is None
                or transaction.transaction_type != "opening_balance"
            ):
                continue
            raw = transaction.raw_payload or {}
            current_price = _optional_decimal(raw.get("current_price"))
            if current_price is not None:
                price = current_price
                source_base = LEGACY_DECLARED_PRICE_SOURCE
                supplied_field = "current_price"
            else:
                price = transaction.price or Decimal("0")
                source_base = LEGACY_COST_FALLBACK_SOURCE
                supplied_field = "buy_price"
            source = f"{source_base}:{import_batch_id}"
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
                        "fallback": source_base == LEGACY_COST_FALLBACK_SOURCE,
                        "supplied_field": supplied_field,
                        "import_batch_id": str(import_batch_id),
                        "snapshot_generation_id": str(generation_id),
                    },
                )
            )
            sources[transaction.security_id] = {
                "security_id": str(transaction.security_id),
                "price_bar_id": str(stored.id),
                "source": source,
                "data_as_of": cutoff.isoformat(),
                "user_supplied": True,
                "fallback": source_base == LEGACY_COST_FALLBACK_SOURCE,
                "snapshot_generation_id": str(generation_id),
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


def _normalized_position_mapping(position: Mapping[str, object]) -> dict[str, object]:
    return {str(key).strip().lower(): value for key, value in position.items()}


def _transaction_position_payload(
    transaction: Transaction,
    security: Security | None,
) -> dict[str, object]:
    raw = transaction.raw_payload or {}
    is_cash = transaction.transaction_type == "deposit" and transaction.security_id is None
    ticker = str(raw.get("ticker") or (security.canonical_symbol if security else "CASH"))
    shares = raw.get("shares")
    buy_price = raw.get("buy_price")
    return {
        "ticker": ticker.strip().upper(),
        "name": str(raw.get("name") or (security.name if security else "Cash")),
        "shares": _number(shares, fallback=transaction.quantity),
        "buy_price": _number(buy_price, fallback=transaction.price or Decimal("0")),
        "current_price": _optional_number(raw.get("current_price")),
        "buy_date": str(raw.get("buy_date") or transaction.occurred_at.date().isoformat()),
        "currency": transaction.currency,
        "sector": str(raw.get("sector") or (security.sector if security else "")),
        "asset_type": str(
            raw.get("asset_type")
            or (security.asset_type if security else ("cash" if is_cash else "equity"))
        ),
        "market": str(raw.get("market") or (security.market if security else "")),
        "exchange": str(raw.get("exchange") or (security.exchange if security else "")),
        "country": str(raw.get("country") or (security.country if security else "")),
        "note": str(raw.get("note") or transaction.note),
        "source": LEGACY_IMPORT_SOURCE,
    }


def _generation_payload(
    generation: LegacySnapshotGeneration,
    *,
    created: bool,
    superseded: LegacySnapshotGeneration | None,
) -> dict[str, object]:
    return {
        "id": str(generation.id),
        "status": generation.status,
        "generation_number": generation.generation_number,
        "import_batch_id": str(generation.import_batch_id),
        "activated_at": _as_utc(generation.activated_at).isoformat(),
        "created": created,
        "superseded_generation_id": str(superseded.id) if superseded else None,
    }


def _rebuild_status(rebuilt: PositionRebuildResult) -> dict[str, object]:
    return {
        "status": "completed",
        "as_of": rebuilt.as_of.isoformat(),
        "position_count": len(rebuilt.positions),
        "cash_balances": {
            currency: str(amount) for currency, amount in rebuilt.cash_balances.items()
        },
        "warnings": list(rebuilt.warnings),
        "history_completeness": rebuilt.history_completeness,
    }


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def _number(value: object, *, fallback: Decimal) -> float:
    parsed = _optional_decimal(value)
    return float(parsed if parsed is not None else fallback)


def _optional_number(value: object) -> float | None:
    parsed = _optional_decimal(value)
    return float(parsed) if parsed is not None else None


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
        "data_as_of": (
            _as_utc(valuation.data_as_of).isoformat() if valuation.data_as_of else None
        ),
        "position_count": len(result.positions),
        "warnings": list(valuation.warnings),
        "data_sources": source_context,
    }


def _stored_valuation_status(
    valuation: PortfolioValuationSnapshot,
    *,
    position_count: int,
    status: str,
) -> dict[str, object]:
    context = valuation.config_snapshot.get("data_source_context", {})
    return {
        "status": status,
        "snapshot_id": str(valuation.id),
        "valuation_status": valuation.valuation_status,
        "as_of": _as_utc(valuation.as_of).isoformat(),
        "data_as_of": (
            _as_utc(valuation.data_as_of).isoformat() if valuation.data_as_of else None
        ),
        "position_count": position_count,
        "warnings": list(valuation.warnings),
        "data_sources": context if isinstance(context, dict) else {},
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
