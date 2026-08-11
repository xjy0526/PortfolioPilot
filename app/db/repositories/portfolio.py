"""Repositories for the transaction ledger and derived snapshots."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.db.models import PositionSnapshot, Transaction
from app.db.repositories.base import BaseRepository


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

    async def add_idempotent(self, transaction: Transaction) -> tuple[Transaction, bool]:
        if transaction.external_id:
            existing = await self.find_external(
                transaction.portfolio_id,
                transaction.source,
                transaction.external_id,
            )
            if existing is not None:
                return existing, False
        return await self.add(transaction), True

    async def list_for_portfolio(self, portfolio_id: uuid.UUID) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(Transaction.portfolio_id == portfolio_id)
            .order_by(Transaction.occurred_at, Transaction.created_at)
        )
        return list((await self.session.scalars(statement)).all())


class PositionSnapshotRepository(BaseRepository[PositionSnapshot]):
    model = PositionSnapshot

    async def find_unique(
        self,
        portfolio_id: uuid.UUID,
        security_id: uuid.UUID | None,
        as_of: datetime,
        source: str,
    ) -> PositionSnapshot | None:
        security_clause = (
            PositionSnapshot.security_id.is_(None)
            if security_id is None
            else PositionSnapshot.security_id == security_id
        )
        statement = select(PositionSnapshot).where(
            PositionSnapshot.portfolio_id == portfolio_id,
            security_clause,
            PositionSnapshot.as_of == as_of,
            PositionSnapshot.source == source,
        )
        return await self.session.scalar(statement)

    async def add_idempotent(
        self,
        snapshot: PositionSnapshot,
    ) -> tuple[PositionSnapshot, bool]:
        existing = await self.find_unique(
            snapshot.portfolio_id,
            snapshot.security_id,
            snapshot.as_of,
            snapshot.source,
        )
        if existing is not None:
            return existing, False
        return await self.add(snapshot), True

    async def list_at(
        self,
        portfolio_id: uuid.UUID,
        as_of: datetime,
    ) -> list[PositionSnapshot]:
        statement = select(PositionSnapshot).where(
            PositionSnapshot.portfolio_id == portfolio_id,
            PositionSnapshot.as_of == as_of,
        )
        return list((await self.session.scalars(statement)).all())
