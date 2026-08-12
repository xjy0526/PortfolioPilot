"""Transaction ledger and derived position snapshot models."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
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
        CheckConstraint(
            "transaction_type IN ('opening_balance','buy','sell','deposit','withdrawal',"
            "'dividend','fee','tax','split','transfer_in','transfer_out')",
            name="transaction_type_allowed",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso3"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("price IS NULL OR price >= 0", name="price_nonnegative"),
        CheckConstraint("gross_amount >= 0", name="gross_amount_nonnegative"),
        CheckConstraint("fees >= 0", name="fees_nonnegative"),
        CheckConstraint("taxes >= 0", name="taxes_nonnegative"),
        CheckConstraint(
            "fx_rate_to_base IS NULL OR fx_rate_to_base > 0",
            name="fx_rate_positive",
        ),
        CheckConstraint(
            "transaction_type NOT IN ('opening_balance','buy','sell','split') "
            "OR security_id IS NOT NULL",
            name="security_required",
        ),
        Index(
            "uq_transactions_external_id_not_null",
            "portfolio_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_transactions_source_record_hash_not_null",
            "portfolio_id",
            "source",
            "source_record_hash",
            unique=True,
            postgresql_where=text("source_record_hash IS NOT NULL"),
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
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_record_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class ImportBatch(UUIDTimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "source", "file_sha256", name="uq_import_batches_portfolio_source_hash"
        ),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="processing")
    total_rows: Mapped[int] = mapped_column(nullable=False, default=0)
    accepted_rows: Mapped[int] = mapped_column(nullable=False, default=0)
    rejected_rows: Mapped[int] = mapped_column(nullable=False, default=0)
    error_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PortfolioValuationSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "portfolio_valuation_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id", "as_of", "source", name="uq_portfolio_valuations_portfolio_as_of_source"
        ),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_market_value: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    total_cost_basis: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    cash_value: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    history_completeness: Mapped[str] = mapped_column(
        String(40), nullable=False, default="complete"
    )
    cash_balances: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class PositionSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "position_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "valuation_snapshot_id",
            "security_id",
            name="uq_position_snapshots_valuation_security",
        ),
    )

    valuation_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "portfolio_valuation_snapshots.id",
            name="fk_position_snapshots_valuation_id_portfolio_valuations",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY_NUMERIC, nullable=False)
    average_cost: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    price_bar_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("price_bars.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fx_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fx_rates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    native_price: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    native_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    valuation_fx_rate: Mapped[Decimal] = mapped_column(RATE_NUMERIC, nullable=False)
    market_value_base: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
    cost_basis_base: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    unrealized_pnl_base: Mapped[Decimal | None] = mapped_column(MONEY_NUMERIC, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(WEIGHT_NUMERIC, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
