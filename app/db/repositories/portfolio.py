"""Repositories for the transaction ledger and derived snapshots."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import (
    ImportBatch,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    Transaction,
)
from app.db.repositories.base import BaseRepository
from time_utils import utc_now


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
