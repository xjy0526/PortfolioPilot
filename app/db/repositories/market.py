"""Repositories for security master, prices, and FX data."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select

from app.db.models import FxRate, PriceBar, ProviderSymbol, Security
from app.db.repositories.base import BaseRepository


class SecurityRepository(BaseRepository[Security]):
    model = Security

    async def find_canonical(
        self,
        canonical_symbol: str,
        exchange: str,
        market: str,
    ) -> Security | None:
        statement = select(Security).where(
            Security.canonical_symbol == canonical_symbol.upper(),
            Security.exchange == exchange,
            Security.market == market,
        )
        return await self.session.scalar(statement)

    async def find_by_provider_symbol(self, provider: str, symbol: str) -> Security | None:
        statement = (
            select(Security)
            .join(ProviderSymbol, ProviderSymbol.security_id == Security.id)
            .where(ProviderSymbol.provider == provider, ProviderSymbol.symbol == symbol)
        )
        return await self.session.scalar(statement)

    async def get_or_create(
        self,
        *,
        canonical_symbol: str,
        exchange: str,
        market: str,
        currency: str,
        name: str = "",
        asset_type: str = "equity",
        sector: str = "Unknown",
        country: str = "",
        security_metadata: dict[str, Any] | None = None,
    ) -> Security:
        normalized = canonical_symbol.strip().upper()
        existing = await self.find_canonical(normalized, exchange, market)
        if existing is not None:
            return existing
        return await self.add(
            Security(
                canonical_symbol=normalized,
                name=name or normalized,
                asset_type=asset_type,
                market=market,
                exchange=exchange,
                currency=currency.upper(),
                sector=sector,
                country=country,
                security_metadata=security_metadata or {},
            )
        )


class ProviderSymbolRepository(BaseRepository[ProviderSymbol]):
    model = ProviderSymbol

    async def find(self, provider: str, symbol: str) -> ProviderSymbol | None:
        statement = select(ProviderSymbol).where(
            ProviderSymbol.provider == provider,
            ProviderSymbol.symbol == symbol,
        )
        return await self.session.scalar(statement)


class PriceBarRepository(BaseRepository[PriceBar]):
    model = PriceBar

    async def find_unique(
        self,
        security_id: uuid.UUID,
        trade_date: date,
        source: str,
    ) -> PriceBar | None:
        statement = select(PriceBar).where(
            PriceBar.security_id == security_id,
            PriceBar.trade_date == trade_date,
            PriceBar.source == source,
        )
        return await self.session.scalar(statement)


class FxRateRepository(BaseRepository[FxRate]):
    model = FxRate

    async def find_unique(
        self,
        base_currency: str,
        quote_currency: str,
        rate_date: date,
        source: str,
    ) -> FxRate | None:
        statement = select(FxRate).where(
            FxRate.base_currency == base_currency.upper(),
            FxRate.quote_currency == quote_currency.upper(),
            FxRate.rate_date == rate_date,
            FxRate.source == source,
        )
        return await self.session.scalar(statement)
