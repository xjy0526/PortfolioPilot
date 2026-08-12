"""Deterministic transaction-ledger accounting tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import Transaction
from app.services.position_rebuilder import LedgerValidationError, PositionRebuilder

PORTFOLIO_ID = uuid.uuid4()
SECURITY_ID = uuid.uuid4()
START = datetime(2026, 1, 2, 8, tzinfo=UTC)


def _transaction(
    transaction_type: str,
    *,
    offset: int,
    quantity: str = "0",
    price: str | None = None,
    gross: str = "0",
    fees: str = "0",
    taxes: str = "0",
    currency: str = "CNY",
    security_id: uuid.UUID | None = None,
) -> Transaction:
    occurred_at = START + timedelta(hours=offset)
    return Transaction(
        id=uuid.uuid4(),
        created_at=occurred_at + timedelta(minutes=1),
        updated_at=occurred_at + timedelta(minutes=1),
        portfolio_id=PORTFOLIO_ID,
        security_id=(
            SECURITY_ID
            if security_id is None
            and transaction_type in {"opening_balance", "buy", "sell", "split"}
            else security_id
        ),
        transaction_type=transaction_type,
        occurred_at=occurred_at,
        quantity=Decimal(quantity),
        price=Decimal(price) if price is not None else None,
        gross_amount=Decimal(gross),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=currency,
        source="unit_test",
        raw_payload={},
    )


def test_buy_partial_sell_dividend_fee_tax_and_weighted_average() -> None:
    transactions = [
        _transaction("deposit", offset=0, gross="10000"),
        _transaction("buy", offset=1, quantity="10", price="100", gross="1000", fees="10"),
        _transaction("buy", offset=2, quantity="10", price="120", gross="1200"),
        _transaction(
            "sell", offset=3, quantity="5", price="150", gross="750", fees="5", taxes="2"
        ),
        _transaction("dividend", offset=4, gross="50"),
        _transaction("fee", offset=5, gross="10"),
        _transaction("tax", offset=6, gross="5"),
    ]

    result = PositionRebuilder().rebuild(
        portfolio_id=PORTFOLIO_ID,
        transactions=list(reversed(transactions)),
        as_of=START + timedelta(days=1),
    )

    assert result.cash_balances == {"CNY": Decimal("8568")}
    assert len(result.positions) == 1
    position = result.positions[0]
    assert position.quantity == Decimal("15")
    assert position.average_cost_native == Decimal("110.5")
    assert position.cost_basis_native == Decimal("1657.5")
    assert position.realized_pnl_native == Decimal("190.5")
    assert result.history_completeness == "complete"


def test_full_sell_removes_position_and_split_preserves_cost_basis() -> None:
    split_result = PositionRebuilder().rebuild(
        portfolio_id=PORTFOLIO_ID,
        transactions=[
            _transaction("buy", offset=0, quantity="10", price="100", gross="1000"),
            _transaction("split", offset=1, quantity="2", gross="0"),
        ],
        as_of=START + timedelta(hours=2),
    )
    assert split_result.positions[0].quantity == Decimal("20")
    assert split_result.positions[0].average_cost_native == Decimal("50")
    assert split_result.positions[0].cost_basis_native == Decimal("1000")

    sold = PositionRebuilder().rebuild(
        portfolio_id=PORTFOLIO_ID,
        transactions=[
            _transaction("buy", offset=0, quantity="10", price="100", gross="1000"),
            _transaction("sell", offset=1, quantity="10", price="110", gross="1100"),
        ],
        as_of=START + timedelta(hours=2),
    )
    assert sold.positions == ()


def test_opening_balance_and_multi_currency_cash_are_explicit() -> None:
    result = PositionRebuilder().rebuild(
        portfolio_id=PORTFOLIO_ID,
        transactions=[
            _transaction(
                "opening_balance", offset=0, quantity="3", price="100", gross="300"
            ),
            _transaction("deposit", offset=1, gross="1000", currency="CNY"),
            _transaction("deposit", offset=2, gross="200", currency="USD"),
            _transaction("withdrawal", offset=3, gross="20", currency="USD"),
        ],
        as_of=START + timedelta(hours=4),
    )
    assert result.cash_balances == {"CNY": Decimal("1000"), "USD": Decimal("180")}
    assert result.history_completeness == "opening_balance_only"
    assert "opening_balance_indicates_incomplete_history" in result.warnings


def test_as_of_excludes_future_transactions() -> None:
    result = PositionRebuilder().rebuild(
        portfolio_id=PORTFOLIO_ID,
        transactions=[
            _transaction("buy", offset=0, quantity="2", price="100", gross="200"),
            _transaction("buy", offset=5, quantity="8", price="200", gross="1600"),
        ],
        as_of=START + timedelta(hours=1),
    )
    assert result.positions[0].quantity == Decimal("2")
    assert result.positions[0].average_cost_native == Decimal("100")


def test_negative_position_and_negative_absolute_values_fail_closed() -> None:
    with pytest.raises(LedgerValidationError, match="negative position"):
        PositionRebuilder().rebuild(
            portfolio_id=PORTFOLIO_ID,
            transactions=[
                _transaction("sell", offset=0, quantity="1", price="100", gross="100")
            ],
            as_of=START + timedelta(hours=1),
        )

    with pytest.raises(LedgerValidationError, match="gross_amount must be non-negative"):
        PositionRebuilder().rebuild(
            portfolio_id=PORTFOLIO_ID,
            transactions=[_transaction("deposit", offset=0, gross="-1")],
            as_of=START + timedelta(hours=1),
        )


def test_security_currency_cannot_change_inside_ledger() -> None:
    with pytest.raises(LedgerValidationError, match="currency changed"):
        PositionRebuilder().rebuild(
            portfolio_id=PORTFOLIO_ID,
            transactions=[
                _transaction("buy", offset=0, quantity="1", price="10", gross="10"),
                _transaction(
                    "sell",
                    offset=1,
                    quantity="1",
                    price="11",
                    gross="11",
                    currency="USD",
                ),
            ],
            as_of=START + timedelta(hours=2),
        )

