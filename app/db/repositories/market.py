"""Repositories for security master, prices, and FX data."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import FxRate, PriceBar, ProviderSymbol, Security
from app.db.repositories.base import BaseRepository
from time_utils import utc_now


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
        values = {
            "canonical_symbol": normalized,
            "name": name or normalized,
            "asset_type": asset_type,
            "market": market,
            "exchange": exchange,
            "currency": currency.upper(),
            "sector": sector,
            "country": country,
            "security_metadata": security_metadata or {},
        }
        insert_statement = insert(Security).values(**values)
        statement = insert_statement.on_conflict_do_update(
            index_elements=[Security.canonical_symbol, Security.exchange, Security.market],
            set_={
                "name": insert_statement.excluded.name,
                "asset_type": insert_statement.excluded.asset_type,
                "currency": insert_statement.excluded.currency,
                "sector": insert_statement.excluded.sector,
                "country": insert_statement.excluded.country,
                "security_metadata": Security.security_metadata.op("||")(
                    insert_statement.excluded.security_metadata
                ),
                "updated_at": utc_now(),
            },
        ).returning(Security)
        return (await self.session.execute(statement)).scalar_one()

    async def list_with_provider(self, provider: str) -> list[tuple[Security, str]]:
        statement = (
            select(Security, ProviderSymbol.symbol)
            .join(ProviderSymbol, ProviderSymbol.security_id == Security.id)
            .where(ProviderSymbol.provider == provider)
            .order_by(Security.canonical_symbol)
        )
        return [(security, symbol) for security, symbol in await self.session.execute(statement)]


class ProviderSymbolRepository(BaseRepository[ProviderSymbol]):
    model = ProviderSymbol

    async def find(self, provider: str, symbol: str) -> ProviderSymbol | None:
        statement = select(ProviderSymbol).where(
            ProviderSymbol.provider == provider,
            ProviderSymbol.symbol == symbol,
        )
        return await self.session.scalar(statement)

    async def get_or_create(
        self,
        *,
        security_id: uuid.UUID,
        provider: str,
        symbol: str,
        is_primary: bool = True,
        mapping_metadata: dict[str, Any] | None = None,
    ) -> ProviderSymbol:
        statement = (
            insert(ProviderSymbol)
            .values(
                security_id=security_id,
                provider=provider,
                symbol=symbol,
                is_primary=is_primary,
                mapping_metadata=mapping_metadata or {},
            )
            .on_conflict_do_nothing(
                index_elements=[ProviderSymbol.provider, ProviderSymbol.symbol]
            )
            .returning(ProviderSymbol)
        )
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self.find(provider, symbol)
        if existing is None:
            raise RuntimeError("Atomic provider-symbol upsert did not return a row")
        if existing.security_id != security_id:
            raise ValueError(
                f"provider symbol {provider}:{symbol} is already mapped to another security"
            )
        return existing


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

    async def upsert(self, price_bar: PriceBar) -> PriceBar:
        values = self.insert_values(price_bar)
        insert_statement = insert(PriceBar).values(**values)
        statement = insert_statement.on_conflict_do_update(
            index_elements=[PriceBar.security_id, PriceBar.trade_date, PriceBar.source],
            set_={
                "currency": insert_statement.excluded.currency,
                "open": insert_statement.excluded.open,
                "high": insert_statement.excluded.high,
                "low": insert_statement.excluded.low,
                "close": insert_statement.excluded.close,
                "adjusted_close": insert_statement.excluded.adjusted_close,
                "adjustment_factor": insert_statement.excluded.adjustment_factor,
                "volume": insert_statement.excluded.volume,
                "data_as_of": insert_statement.excluded.data_as_of,
                "is_final": insert_statement.excluded.is_final,
                "sync_run_id": insert_statement.excluded.sync_run_id,
                "quality_status": insert_statement.excluded.quality_status,
                "raw_payload": insert_statement.excluded.raw_payload,
                "updated_at": utc_now(),
            },
        ).returning(PriceBar)
        return (await self.session.execute(statement)).scalar_one()

    async def latest_at_or_before(
        self,
        security_id: uuid.UUID,
        as_of_date: date,
        *,
        preferred_source: str | None = None,
        knowledge_as_of: datetime | None = None,
        active_legacy_import_batch_id: uuid.UUID | None = None,
    ) -> PriceBar | None:
        statement = (
            select(PriceBar)
            .where(
                PriceBar.security_id == security_id,
                PriceBar.trade_date <= as_of_date,
                PriceBar.is_final.is_(True),
                PriceBar.quality_status == "valid",
            )
            .order_by(
                (
                    PriceBar.source == preferred_source
                    if preferred_source
                    else PriceBar.is_final
                ).desc(),
                PriceBar.trade_date.desc(),
                PriceBar.data_as_of.desc(),
            )
            .limit(1)
        )
        if knowledge_as_of is not None:
            statement = statement.where(PriceBar.data_as_of <= knowledge_as_of)
        if active_legacy_import_batch_id is None:
            statement = statement.where(PriceBar.source.not_like("legacy_csv_%"))
        else:
            statement = statement.where(
                or_(
                    PriceBar.source.not_like("legacy_csv_%"),
                    PriceBar.raw_payload["import_batch_id"].astext
                    == str(active_legacy_import_batch_id),
                )
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

    async def upsert(self, fx_rate: FxRate) -> FxRate:
        values = self.insert_values(fx_rate)
        insert_statement = insert(FxRate).values(**values)
        statement = insert_statement.on_conflict_do_update(
            index_elements=[
                FxRate.base_currency,
                FxRate.quote_currency,
                FxRate.rate_date,
                FxRate.source,
            ],
            set_={
                "rate": insert_statement.excluded.rate,
                "data_as_of": insert_statement.excluded.data_as_of,
                "sync_run_id": insert_statement.excluded.sync_run_id,
                "raw_payload": insert_statement.excluded.raw_payload,
                "updated_at": utc_now(),
            },
        ).returning(FxRate)
        return (await self.session.execute(statement)).scalar_one()

    async def latest_at_or_before(
        self,
        base_currency: str,
        quote_currency: str,
        as_of_date: date,
        *,
        knowledge_as_of: datetime | None = None,
    ) -> FxRate | None:
        statement = (
            select(FxRate)
            .where(
                FxRate.base_currency == base_currency.upper(),
                FxRate.quote_currency == quote_currency.upper(),
                FxRate.rate_date <= as_of_date,
            )
            .order_by(FxRate.rate_date.desc(), FxRate.data_as_of.desc())
            .limit(1)
        )
        if knowledge_as_of is not None:
            statement = statement.where(FxRate.data_as_of <= knowledge_as_of)
        return await self.session.scalar(statement)
