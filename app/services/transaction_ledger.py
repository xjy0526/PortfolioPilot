"""PostgreSQL-backed transaction ledger service."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transaction
from app.db.repositories import PortfolioRepository, TransactionRepository
from app.domain import PositionRebuildResult
from app.services.position_rebuilder import PositionRebuilder


class TransactionLedgerService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = TransactionRepository(session)
        self.portfolios = PortfolioRepository(session)
        self.rebuilder = PositionRebuilder()

    async def list_transactions(
        self, portfolio_id: uuid.UUID, *, as_of: datetime | None = None
    ) -> list[Transaction]:
        return await self.repository.list_for_portfolio(portfolio_id, as_of=as_of)

    async def add_transaction(self, transaction: Transaction) -> tuple[Transaction, bool]:
        self.rebuilder._validate_transaction(transaction)
        savepoint = await self.repository.session.begin_nested()
        try:
            stored, created = await self.repository.add_idempotent(transaction)
            if created:
                transactions = await self.repository.list_for_portfolio(
                    transaction.portfolio_id
                )
                self.rebuilder.rebuild(
                    portfolio_id=transaction.portfolio_id,
                    transactions=transactions,
                    as_of=max(row.occurred_at for row in transactions),
                )
        except Exception:
            await savepoint.rollback()
            raise
        await savepoint.commit()
        return stored, created

    async def rebuild(
        self, portfolio_id: uuid.UUID, *, as_of: datetime
    ) -> PositionRebuildResult:
        portfolio = await self.portfolios.get(portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        if portfolio.cost_basis_method != "weighted_average":
            raise ValueError(
                f"unsupported cost_basis_method: {portfolio.cost_basis_method}"
            )
        transactions = await self.list_transactions(portfolio_id, as_of=as_of)
        return self.rebuilder.rebuild(
            portfolio_id=portfolio_id,
            transactions=transactions,
            as_of=as_of,
        )
