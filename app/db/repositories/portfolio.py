"""Repositories for the transaction ledger and derived snapshots."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import (
    ImportBatch,
    LegacySnapshotGeneration,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    Transaction,
)
from app.db.repositories.base import BaseRepository
from time_utils import utc_now


@dataclass(frozen=True, slots=True)
class TransactionAuditRecord:
    """A ledger row with its optional legacy generation lineage."""

    transaction: Transaction
    generation: LegacySnapshotGeneration | None
    effective: bool


class TransactionRepository(BaseRepository[Transaction]):
    model = Transaction

    async def find_external(
        self,
        portfolio_id: uuid.UUID,
        source: str,
        external_id: str,
    ) -> Transaction | None:
        statement = select(Transaction).where(
            Transaction.portfolio_id == portfolio_id,
            Transaction.source == source,
            Transaction.external_id == external_id,
        )
        return await self.session.scalar(statement)

    async def find_source_record(
        self,
        portfolio_id: uuid.UUID,
        source: str,
        source_record_hash: str,
    ) -> Transaction | None:
        statement = select(Transaction).where(
            Transaction.portfolio_id == portfolio_id,
            Transaction.source == source,
            Transaction.source_record_hash == source_record_hash,
        )
        return await self.session.scalar(statement)

    async def add_idempotent(self, transaction: Transaction) -> tuple[Transaction, bool]:
        statement = (
            insert(Transaction)
            .values(**self.insert_values(transaction))
            .on_conflict_do_nothing()
            .returning(Transaction)
        )
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created, True
        existing = None
        if transaction.external_id:
            existing = await self.find_external(
                transaction.portfolio_id, transaction.source, transaction.external_id
            )
        if existing is None and transaction.source_record_hash:
            existing = await self.find_source_record(
                transaction.portfolio_id,
                transaction.source,
                transaction.source_record_hash,
            )
        if existing is None:
            raise RuntimeError("Atomic transaction upsert did not return a row")
        return existing, False

    async def list_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[Transaction]:
        statement = select(Transaction).where(Transaction.portfolio_id == portfolio_id)
        if as_of is not None:
            statement = statement.where(Transaction.occurred_at <= as_of)
        statement = statement.order_by(
            Transaction.occurred_at,
            Transaction.created_at,
            Transaction.id,
        )
        return list((await self.session.scalars(statement)).all())

    async def list_effective_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        as_of: datetime | None = None,
        legacy_snapshot_source: str = "legacy_dashboard_csv",
    ) -> list[Transaction]:
        """Return formal ledger rows plus only the active dashboard generation."""
        statement = select(Transaction).where(
            Transaction.portfolio_id == portfolio_id,
            self._effective_condition(
                portfolio_id,
                legacy_snapshot_source=legacy_snapshot_source,
            ),
        )
        if as_of is not None:
            statement = statement.where(Transaction.occurred_at <= as_of)
        statement = statement.order_by(
            Transaction.occurred_at,
            Transaction.created_at,
            Transaction.id,
        )
        return list((await self.session.scalars(statement)).all())

    async def list_audit_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        as_of: datetime | None = None,
        legacy_snapshot_source: str = "legacy_dashboard_csv",
    ) -> list[TransactionAuditRecord]:
        """Return every ledger row with legacy generation and effective status."""
        generation_join = and_(
            LegacySnapshotGeneration.portfolio_id == Transaction.portfolio_id,
            LegacySnapshotGeneration.source == Transaction.source,
            LegacySnapshotGeneration.import_batch_id == Transaction.import_batch_id,
        )
        effective = self._effective_condition(
            portfolio_id,
            legacy_snapshot_source=legacy_snapshot_source,
        )
        statement = (
            select(
                Transaction,
                LegacySnapshotGeneration,
                effective.label("effective"),
            )
            .outerjoin(LegacySnapshotGeneration, generation_join)
            .where(Transaction.portfolio_id == portfolio_id)
        )
        if as_of is not None:
            statement = statement.where(Transaction.occurred_at <= as_of)
        statement = statement.order_by(
            Transaction.occurred_at,
            Transaction.created_at,
            Transaction.id,
        )
        rows = (await self.session.execute(statement)).all()
        return [
            TransactionAuditRecord(
                transaction=row[0],
                generation=row[1],
                effective=bool(row[2]),
            )
            for row in rows
        ]

    @staticmethod
    def _effective_condition(
        portfolio_id: uuid.UUID,
        *,
        legacy_snapshot_source: str,
    ) -> ColumnElement[bool]:
        active_batch_ids = select(LegacySnapshotGeneration.import_batch_id).where(
            LegacySnapshotGeneration.portfolio_id == portfolio_id,
            LegacySnapshotGeneration.source == legacy_snapshot_source,
            LegacySnapshotGeneration.status == "active",
        )
        return or_(
            Transaction.source != legacy_snapshot_source,
            Transaction.import_batch_id.in_(active_batch_ids),
        )

    async def list_for_import_batch(self, import_batch_id: uuid.UUID) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(Transaction.import_batch_id == import_batch_id)
            .order_by(Transaction.occurred_at, Transaction.created_at, Transaction.id)
        )
        return list((await self.session.scalars(statement)).all())


class PositionSnapshotRepository(BaseRepository[PositionSnapshot]):
    model = PositionSnapshot

    async def find_unique(
        self,
        valuation_snapshot_id: uuid.UUID,
        security_id: uuid.UUID,
    ) -> PositionSnapshot | None:
        statement = select(PositionSnapshot).where(
            PositionSnapshot.valuation_snapshot_id == valuation_snapshot_id,
            PositionSnapshot.security_id == security_id,
        )
        return await self.session.scalar(statement)

    async def add_idempotent(
        self,
        snapshot: PositionSnapshot,
    ) -> tuple[PositionSnapshot, bool]:
        if snapshot.id is None:
            snapshot.id = uuid.uuid4()
        insert_statement = insert(PositionSnapshot).values(**self.insert_values(snapshot))
        statement = insert_statement.on_conflict_do_update(
            index_elements=[
                PositionSnapshot.valuation_snapshot_id,
                PositionSnapshot.security_id,
            ],
            set_={
                "quantity": insert_statement.excluded.quantity,
                "average_cost": insert_statement.excluded.average_cost,
                "price_bar_id": insert_statement.excluded.price_bar_id,
                "fx_rate_id": insert_statement.excluded.fx_rate_id,
                "native_price": insert_statement.excluded.native_price,
                "native_currency": insert_statement.excluded.native_currency,
                "valuation_fx_rate": insert_statement.excluded.valuation_fx_rate,
                "market_value_base": insert_statement.excluded.market_value_base,
                "cost_basis_base": insert_statement.excluded.cost_basis_base,
                "unrealized_pnl_base": insert_statement.excluded.unrealized_pnl_base,
                "cost_basis_native": insert_statement.excluded.cost_basis_native,
                "cost_basis_base_at_trade": (
                    insert_statement.excluded.cost_basis_base_at_trade
                ),
                "local_price_pnl": insert_statement.excluded.local_price_pnl,
                "fx_pnl": insert_statement.excluded.fx_pnl,
                "total_pnl_base": insert_statement.excluded.total_pnl_base,
                "weight": insert_statement.excluded.weight,
                "base_currency": insert_statement.excluded.base_currency,
                "snapshot_data": insert_statement.excluded.snapshot_data,
                "updated_at": utc_now(),
            },
        ).returning(PositionSnapshot)
        stored = (await self.session.execute(statement)).scalar_one()
        return stored, stored.id == snapshot.id

    async def delete_not_in(
        self,
        valuation_snapshot_id: uuid.UUID,
        security_ids: set[uuid.UUID],
    ) -> None:
        statement = delete(PositionSnapshot).where(
            PositionSnapshot.valuation_snapshot_id == valuation_snapshot_id
        )
        if security_ids:
            statement = statement.where(PositionSnapshot.security_id.not_in(security_ids))
        await self.session.execute(statement)

    async def list_at(
        self,
        valuation_snapshot_id: uuid.UUID,
    ) -> list[PositionSnapshot]:
        statement = select(PositionSnapshot).where(
            PositionSnapshot.valuation_snapshot_id == valuation_snapshot_id,
        )
        return list((await self.session.scalars(statement)).all())


class ImportBatchRepository(BaseRepository[ImportBatch]):
    model = ImportBatch

    async def get_or_create(self, batch: ImportBatch) -> tuple[ImportBatch, bool]:
        statement = (
            insert(ImportBatch)
            .values(**self.insert_values(batch))
            .on_conflict_do_nothing(
                index_elements=[
                    ImportBatch.portfolio_id,
                    ImportBatch.source,
                    ImportBatch.file_sha256,
                ]
            )
            .returning(ImportBatch)
        )
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created, True
        query = select(ImportBatch).where(
            ImportBatch.portfolio_id == batch.portfolio_id,
            ImportBatch.source == batch.source,
            ImportBatch.file_sha256 == batch.file_sha256,
        )
        existing = await self.session.scalar(query)
        if existing is None:
            raise RuntimeError("Atomic import-batch upsert did not return a row")
        return existing, False


class LegacySnapshotGenerationRepository(BaseRepository[LegacySnapshotGeneration]):
    model = LegacySnapshotGeneration

    async def get_active(
        self,
        portfolio_id: uuid.UUID,
        *,
        source: str = "legacy_dashboard_csv",
        for_update: bool = False,
    ) -> LegacySnapshotGeneration | None:
        statement = select(LegacySnapshotGeneration).where(
            LegacySnapshotGeneration.portfolio_id == portfolio_id,
            LegacySnapshotGeneration.source == source,
            LegacySnapshotGeneration.status == "active",
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        source: str = "legacy_dashboard_csv",
    ) -> list[LegacySnapshotGeneration]:
        statement = (
            select(LegacySnapshotGeneration)
            .where(
                LegacySnapshotGeneration.portfolio_id == portfolio_id,
                LegacySnapshotGeneration.source == source,
            )
            .order_by(LegacySnapshotGeneration.generation_number)
        )
        return list((await self.session.scalars(statement)).all())

    async def activate(
        self,
        *,
        portfolio_id: uuid.UUID,
        import_batch_id: uuid.UUID,
        source: str,
        activated_at: datetime,
        position_count: int,
        generation_metadata: dict[str, object],
    ) -> tuple[LegacySnapshotGeneration, bool, LegacySnapshotGeneration | None]:
        active = await self.get_active(
            portfolio_id,
            source=source,
            for_update=True,
        )
        if active is not None and active.import_batch_id == import_batch_id:
            return active, False, None

        next_number = int(
            await self.session.scalar(
                select(
                    func.coalesce(
                        func.max(LegacySnapshotGeneration.generation_number),
                        0,
                    )
                ).where(
                    LegacySnapshotGeneration.portfolio_id == portfolio_id,
                    LegacySnapshotGeneration.source == source,
                )
            )
            or 0
        ) + 1
        if active is not None:
            active.status = "superseded"
            active.superseded_at = activated_at
            await self.session.flush()

        generation = LegacySnapshotGeneration(
            id=uuid.uuid4(),
            portfolio_id=portfolio_id,
            import_batch_id=import_batch_id,
            source=source,
            generation_number=next_number,
            status="active",
            activated_at=activated_at,
            position_count=position_count,
            generation_metadata=generation_metadata,
        )
        await self.add(generation)
        if active is not None:
            active.superseded_by_id = generation.id
            await self.session.flush()
        return generation, True, active


class PortfolioValuationRepository(BaseRepository[PortfolioValuationSnapshot]):
    model = PortfolioValuationSnapshot

    async def find_unique(
        self,
        portfolio_id: uuid.UUID,
        as_of: datetime,
        source: str,
    ) -> PortfolioValuationSnapshot | None:
        statement = select(PortfolioValuationSnapshot).where(
            PortfolioValuationSnapshot.portfolio_id == portfolio_id,
            PortfolioValuationSnapshot.as_of == as_of,
            PortfolioValuationSnapshot.source == source,
        )
        return await self.session.scalar(statement)

    async def upsert(
        self, snapshot: PortfolioValuationSnapshot
    ) -> PortfolioValuationSnapshot:
        insert_statement = insert(PortfolioValuationSnapshot).values(
            **self.insert_values(snapshot)
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[
                PortfolioValuationSnapshot.portfolio_id,
                PortfolioValuationSnapshot.as_of,
                PortfolioValuationSnapshot.source,
            ],
            set_={
                "valuation_date": insert_statement.excluded.valuation_date,
                "base_currency": insert_statement.excluded.base_currency,
                "total_market_value": insert_statement.excluded.total_market_value,
                "priced_market_value": insert_statement.excluded.priced_market_value,
                "total_cost_basis": insert_statement.excluded.total_cost_basis,
                "cash_value": insert_statement.excluded.cash_value,
                "unrealized_pnl": insert_statement.excluded.unrealized_pnl,
                "valuation_status": insert_statement.excluded.valuation_status,
                "priced_asset_count": insert_statement.excluded.priced_asset_count,
                "unpriced_asset_count": insert_statement.excluded.unpriced_asset_count,
                "unpriced_assets": insert_statement.excluded.unpriced_assets,
                "data_as_of": insert_statement.excluded.data_as_of,
                "data_as_of_earliest": insert_statement.excluded.data_as_of_earliest,
                "data_as_of_latest": insert_statement.excluded.data_as_of_latest,
                "max_staleness_days": insert_statement.excluded.max_staleness_days,
                "coverage_ratio": insert_statement.excluded.coverage_ratio,
                "sync_run_id": insert_statement.excluded.sync_run_id,
                "input_hash": insert_statement.excluded.input_hash,
                "history_completeness": insert_statement.excluded.history_completeness,
                "cash_balances": insert_statement.excluded.cash_balances,
                "warnings": insert_statement.excluded.warnings,
                "config_snapshot": insert_statement.excluded.config_snapshot,
                "updated_at": utc_now(),
            },
        ).returning(PortfolioValuationSnapshot)
        return (await self.session.execute(statement)).scalar_one()

    async def latest_at_or_before(
        self,
        portfolio_id: uuid.UUID,
        as_of: datetime,
        *,
        preferred_source: str | None = None,
    ) -> PortfolioValuationSnapshot | None:
        statement = (
            select(PortfolioValuationSnapshot)
            .where(
                PortfolioValuationSnapshot.portfolio_id == portfolio_id,
                PortfolioValuationSnapshot.as_of <= as_of,
            )
        )
        if preferred_source:
            statement = statement.order_by(
                PortfolioValuationSnapshot.as_of.desc(),
                case(
                    (PortfolioValuationSnapshot.source == preferred_source, 0),
                    else_=1,
                ),
                PortfolioValuationSnapshot.updated_at.desc(),
            )
        else:
            statement = statement.order_by(
                PortfolioValuationSnapshot.as_of.desc(),
                PortfolioValuationSnapshot.updated_at.desc(),
            )
        statement = statement.limit(1)
        return await self.session.scalar(statement)

    async def latest_for_source(
        self,
        portfolio_id: uuid.UUID,
        *,
        source: str,
        as_of: datetime | None = None,
    ) -> PortfolioValuationSnapshot | None:
        statement = select(PortfolioValuationSnapshot).where(
            PortfolioValuationSnapshot.portfolio_id == portfolio_id,
            PortfolioValuationSnapshot.source == source,
        )
        if as_of is not None:
            statement = statement.where(PortfolioValuationSnapshot.as_of <= as_of)
        statement = statement.order_by(
            PortfolioValuationSnapshot.as_of.desc(),
            PortfolioValuationSnapshot.updated_at.desc(),
        ).limit(1)
        return await self.session.scalar(statement)

    async def list_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        since: datetime | None = None,
    ) -> list[PortfolioValuationSnapshot]:
        statement = select(PortfolioValuationSnapshot).where(
            PortfolioValuationSnapshot.portfolio_id == portfolio_id
        )
        if since is not None:
            statement = statement.where(PortfolioValuationSnapshot.as_of >= since)
        statement = statement.order_by(PortfolioValuationSnapshot.as_of)
        return list((await self.session.scalars(statement)).all())
