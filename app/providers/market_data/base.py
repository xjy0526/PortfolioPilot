"""Typed, provider-neutral market-data contract."""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class MarketSecurity:
    security_id: uuid.UUID
    canonical_symbol: str
    provider_symbol: str
    exchange: str
    market: str
    native_currency: str
    asset_type: str


@dataclass(frozen=True, slots=True)
class FxPair:
    base_currency: str
    quote_currency: str


@dataclass(frozen=True, slots=True)
class SecurityMasterRecord:
    canonical_symbol: str
    provider_symbol: str
    name: str
    exchange: str
    market: str
    currency: str
    country: str
    asset_type: str = "equity"
    sector: str = "Unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PriceBarRecord:
    security_id: uuid.UUID
    trade_date: date
    native_currency: str
    raw_close: Decimal
    adjusted_close: Decimal | None
    adjustment_factor: Decimal | None
    source: str
    data_as_of: datetime
    is_final: bool
    quality_status: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FxRateRecord:
    base_currency: str
    quote_currency: str
    rate_date: date
    rate: Decimal
    source: str
    data_as_of: datetime
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    security_id: uuid.UUID
    action_date: date
    action_type: str
    value: Decimal
    source: str
    data_as_of: datetime
    raw_payload: dict[str, Any] = field(default_factory=dict)


class MarketDataProvider(Protocol):
    name: str
    research_only: bool

    async def fetch_security_master(
        self, securities: Sequence[MarketSecurity] = ()
    ) -> list[SecurityMasterRecord]: ...

    async def fetch_price_bars(
        self,
        securities: Sequence[MarketSecurity],
        *,
        start: date,
        end: date,
    ) -> list[PriceBarRecord]: ...

    async def fetch_fx_rates(
        self,
        pairs: Sequence[FxPair],
        *,
        start: date,
        end: date,
    ) -> list[FxRateRecord]: ...

    async def fetch_corporate_actions(
        self,
        securities: Sequence[MarketSecurity],
        *,
        start: date,
        end: date,
    ) -> list[CorporateActionRecord]: ...

