"""PostgreSQL-backed transaction ledger service."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transaction
from app.db.repositories import FxRateRepository, PortfolioRepository, TransactionRepository
from app.domain import PositionRebuildResult
from app.services.position_rebuilder import PositionRebuilder


class TransactionLedgerService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = TransactionRepository(session)
        self.portfolios = PortfolioRepository(session)
        self.fx_rates = FxRateRepository(session)
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
        rebuilt = self.rebuilder.rebuild(
            portfolio_id=portfolio_id,
            transactions=transactions,
            as_of=as_of,
        )
        base_costs, missing_fx = await self._historical_base_costs(
            transactions,
            base_currency=portfolio.base_currency,
            knowledge_as_of=as_of,
        )
        positions = tuple(
            replace(
                position,
                cost_basis_base_at_trade=base_costs.get(position.security_id),
                historical_fx_complete=position.security_id not in missing_fx,
            )
            for position in rebuilt.positions
        )
        warnings = list(rebuilt.warnings)
        warnings.extend(
            f"missing_historical_cost_fx:{security_id}"
            for security_id in sorted(missing_fx, key=str)
        )
        return replace(
            rebuilt,
            positions=positions,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    async def _historical_base_costs(
        self,
        transactions: list[Transaction],
        *,
        base_currency: str,
        knowledge_as_of: datetime,
    ) -> tuple[dict[uuid.UUID, Decimal], set[uuid.UUID]]:
        """Replay weighted-average historical base costs using trade-date FX."""
        quantities: dict[uuid.UUID, Decimal] = {}
        base_costs: dict[uuid.UUID, Decimal] = {}
        missing_fx: set[uuid.UUID] = set()
        ordered = sorted(
            transactions,
            key=lambda row: (
                _as_utc(row.occurred_at),
                _as_utc(row.created_at or row.occurred_at),
                str(row.id or ""),
            ),
        )
        for transaction in ordered:
            security_id = transaction.security_id
            if security_id is None:
                continue
            tx_type = transaction.transaction_type
            if tx_type not in {"opening_balance", "buy", "sell", "split"}:
                continue
            quantities.setdefault(security_id, Decimal("0"))
            base_costs.setdefault(security_id, Decimal("0"))
            if tx_type == "split":
                quantities[security_id] *= transaction.quantity
                continue
            if tx_type == "sell":
                current_quantity = quantities[security_id]
                if current_quantity > 0:
                    released = base_costs[security_id] * transaction.quantity / current_quantity
                    base_costs[security_id] -= released
                    quantities[security_id] -= transaction.quantity
                    if quantities[security_id] == 0:
                        base_costs[security_id] = Decimal("0")
                        missing_fx.discard(security_id)
                continue

            native_cost = (
                transaction.quantity * (transaction.price or Decimal("0"))
                if tx_type == "opening_balance"
                else transaction.gross_amount + transaction.fees + transaction.taxes
            )
            rate = await self._trade_fx_rate(
                transaction,
                base_currency=base_currency,
                knowledge_as_of=knowledge_as_of,
            )
            quantities[security_id] += transaction.quantity
            if rate is None:
                missing_fx.add(security_id)
                continue
            base_costs[security_id] += native_cost * rate
        return base_costs, missing_fx

    async def _trade_fx_rate(
        self,
        transaction: Transaction,
        *,
        base_currency: str,
        knowledge_as_of: datetime,
    ) -> Decimal | None:
        if transaction.fx_rate_to_base is not None:
            return transaction.fx_rate_to_base
        native = transaction.currency.upper()
        base = base_currency.upper()
        if native == base:
            return Decimal("1")
        trade_date = _as_utc(transaction.occurred_at).date()
        direct = await self.fx_rates.latest_at_or_before(
            native,
            base,
            trade_date,
            knowledge_as_of=_as_utc(knowledge_as_of),
        )
        if direct is not None:
            return direct.rate
        inverse = await self.fx_rates.latest_at_or_before(
            base,
            native,
            trade_date,
            knowledge_as_of=_as_utc(knowledge_as_of),
        )
        if inverse is not None and inverse.rate > 0:
            return Decimal("1") / inverse.rate
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
