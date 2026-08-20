"""Deterministically rebuild positions and cash from the transaction ledger."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.db.models import Transaction
from app.domain import LedgerPosition, PositionRebuildResult

ZERO = Decimal("0")


class LedgerValidationError(ValueError):
    """Raised when a transaction sequence violates long-only ledger rules."""


@dataclass(slots=True)
class _MutablePosition:
    quantity: Decimal
    average_cost: Decimal
    currency: str
    realized_pnl: Decimal = ZERO


class PositionRebuilder:
    """Apply absolute-value transactions using weighted-average cost."""

    def rebuild(
        self,
        *,
        portfolio_id: uuid.UUID,
        transactions: list[Transaction],
        as_of: datetime,
    ) -> PositionRebuildResult:
        cutoff = _as_utc(as_of)
        ordered = sorted(
            (row for row in transactions if _as_utc(row.occurred_at) <= cutoff),
            key=lambda row: (
                _as_utc(row.occurred_at),
                _as_utc(row.created_at or row.occurred_at),
                str(row.id or ""),
            ),
        )
        positions: dict[uuid.UUID, _MutablePosition] = {}
        cash: dict[str, Decimal] = {}
        warnings: list[str] = []
        has_opening = False
        has_trading_history = False

        for transaction in ordered:
            self._validate_transaction(transaction)
            currency = transaction.currency.upper()
            cash.setdefault(currency, ZERO)
            tx_type = transaction.transaction_type
            gross = abs(transaction.gross_amount)
            fees = abs(transaction.fees)
            taxes = abs(transaction.taxes)

            if tx_type == "opening_balance":
                has_opening = True
                self._apply_opening_balance(positions, transaction)
            elif tx_type == "buy":
                has_trading_history = True
                self._apply_buy(positions, transaction)
                cash[currency] -= gross + fees + taxes
            elif tx_type == "sell":
                has_trading_history = True
                self._apply_sell(positions, transaction)
                cash[currency] += gross - fees - taxes
            elif tx_type == "split":
                has_trading_history = True
                self._apply_split(positions, transaction)
            elif tx_type in {"deposit", "transfer_in"}:
                cash[currency] += gross - fees - taxes
            elif tx_type in {"withdrawal", "transfer_out"}:
                cash[currency] -= gross + fees + taxes
            elif tx_type == "dividend":
                cash[currency] += gross - fees - taxes
            elif tx_type in {"fee", "tax"}:
                cash[currency] -= gross + fees + taxes

        for currency, balance in sorted(cash.items()):
            if balance < ZERO:
                warnings.append(f"negative_cash_balance:{currency}")
        if has_opening:
            warnings.append("opening_balance_indicates_incomplete_history")

        history_completeness = "complete"
        if has_opening and not has_trading_history:
            history_completeness = "opening_balance_only"
        elif has_opening:
            history_completeness = "partial_history"

        output_positions = tuple(
            LedgerPosition(
                security_id=security_id,
                quantity=position.quantity,
                average_cost_native=position.average_cost,
                native_currency=position.currency,
                realized_pnl_native=position.realized_pnl,
            )
            for security_id, position in sorted(positions.items(), key=lambda item: str(item[0]))
            if position.quantity > ZERO
        )
        return PositionRebuildResult(
            portfolio_id=portfolio_id,
            as_of=cutoff,
            positions=output_positions,
            cash_balances=dict(sorted(cash.items())),
            warnings=tuple(dict.fromkeys(warnings)),
            last_transaction_at=_as_utc(ordered[-1].occurred_at) if ordered else None,
            history_completeness=history_completeness,
        )

    @staticmethod
    def _validate_transaction(transaction: Transaction) -> None:
        for name in ("quantity", "gross_amount", "fees", "taxes"):
            if getattr(transaction, name) < ZERO:
                raise LedgerValidationError(f"{name} must be non-negative")
        if transaction.price is not None and transaction.price < ZERO:
            raise LedgerValidationError("price must be non-negative")
        if transaction.transaction_type in {"opening_balance", "buy", "sell", "split"}:
            if transaction.security_id is None:
                raise LedgerValidationError(
                    f"security_id is required for {transaction.transaction_type}"
                )

    @staticmethod
    def _apply_opening_balance(
        positions: dict[uuid.UUID, _MutablePosition], transaction: Transaction
    ) -> None:
        assert transaction.security_id is not None
        if transaction.price is None:
            raise LedgerValidationError("opening_balance requires price")
        existing = positions.get(transaction.security_id)
        if existing and existing.quantity > ZERO:
            if existing.currency != transaction.currency.upper():
                raise LedgerValidationError(
                    "security transaction currency changed within the ledger"
                )
            total_quantity = existing.quantity + transaction.quantity
            total_cost = (
                existing.quantity * existing.average_cost
                + transaction.quantity * transaction.price
            )
            existing.quantity = total_quantity
            existing.average_cost = total_cost / total_quantity if total_quantity else ZERO
            return
        positions[transaction.security_id] = _MutablePosition(
            quantity=transaction.quantity,
            average_cost=transaction.price,
            currency=transaction.currency.upper(),
        )

    @staticmethod
    def _apply_buy(
        positions: dict[uuid.UUID, _MutablePosition], transaction: Transaction
    ) -> None:
        assert transaction.security_id is not None
        if transaction.price is None or transaction.quantity <= ZERO:
            raise LedgerValidationError("buy requires positive quantity and price")
        position = positions.setdefault(
            transaction.security_id,
            _MutablePosition(ZERO, ZERO, transaction.currency.upper()),
        )
        if position.currency != transaction.currency.upper():
            raise LedgerValidationError("security transaction currency changed within the ledger")
        total_quantity = position.quantity + transaction.quantity
        total_cost = (
            position.quantity * position.average_cost
            + transaction.gross_amount
            + transaction.fees
            + transaction.taxes
        )
        position.quantity = total_quantity
        position.average_cost = total_cost / total_quantity

    @staticmethod
    def _apply_sell(
        positions: dict[uuid.UUID, _MutablePosition], transaction: Transaction
    ) -> None:
        assert transaction.security_id is not None
        if transaction.price is None or transaction.quantity <= ZERO:
            raise LedgerValidationError("sell requires positive quantity and price")
        position = positions.get(transaction.security_id)
        if position is None or position.quantity < transaction.quantity:
            raise LedgerValidationError(
                f"sell would create a negative position for {transaction.security_id}"
            )
        if position.currency != transaction.currency.upper():
            raise LedgerValidationError("security transaction currency changed within the ledger")
        proceeds = transaction.gross_amount - transaction.fees - transaction.taxes
        released_cost = transaction.quantity * position.average_cost
        position.realized_pnl += proceeds - released_cost
        position.quantity -= transaction.quantity
        if position.quantity == ZERO:
            position.average_cost = ZERO

    @staticmethod
    def _apply_split(
        positions: dict[uuid.UUID, _MutablePosition], transaction: Transaction
    ) -> None:
        assert transaction.security_id is not None
        ratio = transaction.quantity
        position = positions.get(transaction.security_id)
        if ratio <= ZERO:
            raise LedgerValidationError("split quantity is the ratio and must be positive")
        if position is None or position.quantity <= ZERO:
            raise LedgerValidationError("split requires an existing position")
        position.quantity *= ratio
        position.average_cost /= ratio


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
