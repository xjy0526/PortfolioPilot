"""Security master, provider symbols, prices, and FX rates."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import (
    Base,
    PRICE_NUMERIC,
    QUANTITY_NUMERIC,
    RATE_NUMERIC,
    UUIDTimestampMixin,
)


class Security(UUIDTimestampMixin, Base):
    __tablename__ = "securities"
    __table_args__ = (
        UniqueConstraint(
            "canonical_symbol",
            "exchange",
            "market",
            name="uq_securities_canonical_symbol_exchange_market",
        ),
    )

    canonical_symbol: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, default="equity")
    market: Mapped[str] = mapped_column(String(50), nullable=False, default="global")
    exchange: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    sector: Mapped[str] = mapped_column(String(120), nullable=False, default="Unknown")
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, unique=True)
    security_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class ProviderSymbol(UUIDTimestampMixin, Base):
    __tablename__ = "provider_symbols"
    __table_args__ = (
        UniqueConstraint("provider", "symbol", name="uq_provider_symbols_provider_symbol"),
        UniqueConstraint(
            "security_id",
            "provider",
            name="uq_provider_symbols_security_id_provider",
        ),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str] = mapped_column(String(120), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mapping_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class PriceBar(UUIDTimestampMixin, Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "trade_date",
            "source",
            name="uq_price_bars_security_id_trade_date_source",
        ),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    close: Mapped[Decimal] = mapped_column(PRICE_NUMERIC, nullable=False)
    adjusted_close: Mapped[Decimal | None] = mapped_column(PRICE_NUMERIC, nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(QUANTITY_NUMERIC, nullable=True)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class FxRate(UUIDTimestampMixin, Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint(
            "base_currency",
            "quote_currency",
            "rate_date",
            "source",
            name="uq_fx_rates_pair_date_source",
        ),
    )

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE_NUMERIC, nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
