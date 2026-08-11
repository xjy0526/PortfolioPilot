"""Transaction ledger and derived position snapshot models."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import (
    Base,
    MONEY_NUMERIC,
    PRICE_NUMERIC,
    QUANTITY_NUMERIC,
    RATE_NUMERIC,
    UUIDTimestampMixin,
    WEIGHT_NUMERIC,
)


class Transaction(UUIDTimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "source",
            "external_id",
            name="uq_transactions_portfolio_id_source_external_id",
        ),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    transaction_type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    fees: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False, default=Decimal("0"))
    taxes: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate_to_base: Mapped[Decimal | None] = mapped_column(RATE_NUMERIC, nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class PositionSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "position_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "security_id",
            "as_of",
            "source",
            name="uq_position_snapshots_portfolio_security_as_of_source",
        ),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    average_cost: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    market_price: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    market_value: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    cost_basis: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(WEIGHT_NUMERIC, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
