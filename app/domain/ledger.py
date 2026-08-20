"""Immutable outputs produced by the transaction ledger."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LedgerPosition:
    security_id: uuid.UUID
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    realized_pnl_native: Decimal = Decimal("0")
    cost_basis_base_at_trade: Decimal | None = None
    historical_fx_complete: bool = True

    @property
    def cost_basis_native(self) -> Decimal:
        return self.quantity * self.average_cost_native


@dataclass(frozen=True, slots=True)
class PositionRebuildResult:
    portfolio_id: uuid.UUID
    as_of: datetime
    positions: tuple[LedgerPosition, ...]
    cash_balances: dict[str, Decimal]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    last_transaction_at: datetime | None = None
    history_completeness: str = "complete"
