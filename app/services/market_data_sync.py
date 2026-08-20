"""Persist provider market facts with explicit source and run lineage."""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FxRate, PriceBar
from app.db.repositories import (
    FxRateRepository,
    PortfolioRepository,
    PriceBarRepository,
    SecurityRepository,
)
from app.providers.market_data import (
    FxPair,
    MarketDataProvider,
    MarketSecurity,
    TushareProvider,
    YFinanceProvider,
)
from app.services.security_master import SecurityMasterService
from config import settings


@dataclass(frozen=True, slots=True)
class ProviderSyncStatus:
    provider: str
    status: str
    research_only: bool
    securities_upserted: int = 0
    price_bars_upserted: int = 0
    fx_rates_upserted: int = 0
    corporate_actions_observed: int = 0
    data_as_of_earliest: datetime | None = None
    data_as_of_latest: datetime | None = None
    error_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status,
            "research_only": self.research_only,
            "securities_upserted": self.securities_upserted,
            "price_bars_upserted": self.price_bars_upserted,
            "fx_rates_upserted": self.fx_rates_upserted,
            "corporate_actions_observed": self.corporate_actions_observed,
            "data_as_of_earliest": (
                self.data_as_of_earliest.isoformat() if self.data_as_of_earliest else None
            ),
            "data_as_of_latest": (
                self.data_as_of_latest.isoformat() if self.data_as_of_latest else None
            ),
            "error_type": self.error_type,
        }


@dataclass(frozen=True, slots=True)
class MarketDataSyncResult:
    providers: tuple[str, ...]
    successful_providers: tuple[str, ...]
    failed_providers: tuple[str, ...]
    provider_statuses: tuple[ProviderSyncStatus, ...]
    securities_upserted: int
    price_bars_upserted: int
    fx_rates_upserted: int
    corporate_actions_observed: int
    data_as_of_earliest: datetime | None
    data_as_of_latest: datetime | None

    @property
    def degraded(self) -> bool:
        return bool(self.failed_providers)

    def as_dict(self) -> dict[str, object]:
        return {
            "providers": list(self.providers),
            "successful_providers": list(self.successful_providers),
            "failed_providers": list(self.failed_providers),
            "degraded": self.degraded,
            "provider_statuses": [item.as_dict() for item in self.provider_statuses],
            "securities_upserted": self.securities_upserted,
            "price_bars_upserted": self.price_bars_upserted,
            "fx_rates_upserted": self.fx_rates_upserted,
            "corporate_actions_observed": self.corporate_actions_observed,
            "data_as_of_earliest": (
                self.data_as_of_earliest.isoformat() if self.data_as_of_earliest else None
            ),
            "data_as_of_latest": (
                self.data_as_of_latest.isoformat() if self.data_as_of_latest else None
            ),
        }


class MarketDataSyncFailure(RuntimeError):
    def __init__(self, result: MarketDataSyncResult) -> None:
        self.result = result
        failed = ", ".join(result.failed_providers) or "unknown"
        super().__init__(f"all configured market-data providers failed: {failed}")


class MarketDataSyncService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        providers: dict[str, MarketDataProvider] | None = None,
    ) -> None:
        self.session = session
        self.securities = SecurityRepository(session)
        self.security_master = SecurityMasterService(session)
        self.prices = PriceBarRepository(session)
        self.fx_rates = FxRateRepository(session)
        self.portfolios = PortfolioRepository(session)
        self.providers = providers or default_market_data_providers()

    async def sync(
        self,
        *,
        provider_names: Sequence[str],
        start: date,
        end: date,
        sync_run_id: uuid.UUID,
        portfolio_id: uuid.UUID | None = None,
        include_security_master: bool = True,
    ) -> MarketDataSyncResult:
        if end < start:
            raise ValueError("market-data end date must not precede start date")
        selected = _normalize_provider_names(provider_names)
        statuses: list[ProviderSyncStatus] = []
        for provider_name in selected:
            provider = self.providers.get(provider_name)
            if provider is None:
                statuses.append(
                    ProviderSyncStatus(
                        provider=provider_name,
                        status="failed",
                        research_only=False,
                        error_type="UnsupportedProvider",
                    )
                )
                continue
            savepoint = await self.session.begin_nested()
            try:
                status = await self._sync_provider(
                    provider_name=provider_name,
                    provider=provider,
                    start=start,
                    end=end,
                    sync_run_id=sync_run_id,
                    portfolio_id=portfolio_id,
                    include_security_master=include_security_master,
                )
            except Exception as exc:
                await savepoint.rollback()
                statuses.append(
                    ProviderSyncStatus(
                        provider=provider_name,
                        status="failed",
                        research_only=provider.research_only,
                        error_type=type(exc).__name__,
                    )
                )
            else:
                await savepoint.commit()
                statuses.append(status)

        successful = tuple(item.provider for item in statuses if item.status == "completed")
        failed = tuple(item.provider for item in statuses if item.status == "failed")
        observations = [
            value
            for item in statuses
            for value in (item.data_as_of_earliest, item.data_as_of_latest)
            if value is not None
        ]
        result = MarketDataSyncResult(
            providers=selected,
            successful_providers=successful,
            failed_providers=failed,
            provider_statuses=tuple(statuses),
            securities_upserted=sum(item.securities_upserted for item in statuses),
            price_bars_upserted=sum(item.price_bars_upserted for item in statuses),
            fx_rates_upserted=sum(item.fx_rates_upserted for item in statuses),
            corporate_actions_observed=sum(item.corporate_actions_observed for item in statuses),
            data_as_of_earliest=min(observations, default=None),
            data_as_of_latest=max(observations, default=None),
        )
        if selected and not successful:
            raise MarketDataSyncFailure(result)
        return result

    async def _sync_provider(
        self,
        *,
        provider_name: str,
        provider: MarketDataProvider,
        start: date,
        end: date,
        sync_run_id: uuid.UUID,
        portfolio_id: uuid.UUID | None,
        include_security_master: bool,
    ) -> ProviderSyncStatus:
        securities_upserted = 0
        observations: list[datetime] = []
        if include_security_master and provider_name == "tushare":
            master_rows = await provider.fetch_security_master()
            for master_row in master_rows:
                await self.security_master.resolve_or_create(
                    ticker=master_row.canonical_symbol,
                    exchange=master_row.exchange,
                    currency=master_row.currency,
                    market=master_row.market,
                    country=master_row.country,
                    name=master_row.name,
                    asset_type=master_row.asset_type,
                    sector=master_row.sector,
                    metadata={**master_row.metadata, "master_source": provider.name},
                )
                securities_upserted += 1

        market_securities = await self._provider_securities(provider_name)
        price_rows = await provider.fetch_price_bars(market_securities, start=start, end=end)
        for price_row in price_rows:
            await self.prices.upsert(
                PriceBar(
                    security_id=price_row.security_id,
                    trade_date=price_row.trade_date,
                    source=price_row.source,
                    currency=price_row.native_currency,
                    open=price_row.open,
                    high=price_row.high,
                    low=price_row.low,
                    close=price_row.raw_close,
                    adjusted_close=price_row.adjusted_close,
                    adjustment_factor=price_row.adjustment_factor,
                    volume=price_row.volume,
                    data_as_of=price_row.data_as_of,
                    is_final=price_row.is_final,
                    sync_run_id=sync_run_id,
                    quality_status=price_row.quality_status,
                    raw_payload=price_row.raw_payload,
                )
            )
            observations.append(_as_utc(price_row.data_as_of))

        pairs = await self._fx_pairs(portfolio_id, market_securities)
        fx_rows = await provider.fetch_fx_rates(pairs, start=start, end=end)
        for fx_row in fx_rows:
            await self.fx_rates.upsert(
                FxRate(
                    base_currency=fx_row.base_currency,
                    quote_currency=fx_row.quote_currency,
                    rate_date=fx_row.rate_date,
                    source=fx_row.source,
                    rate=fx_row.rate,
                    data_as_of=fx_row.data_as_of,
                    sync_run_id=sync_run_id,
                    raw_payload=fx_row.raw_payload,
                )
            )
            observations.append(_as_utc(fx_row.data_as_of))

        actions = await provider.fetch_corporate_actions(market_securities, start=start, end=end)
        observations.extend(_as_utc(item.data_as_of) for item in actions)
        return ProviderSyncStatus(
            provider=provider_name,
            status="completed",
            research_only=provider.research_only,
            securities_upserted=securities_upserted,
            price_bars_upserted=len(price_rows),
            fx_rates_upserted=len(fx_rows),
            corporate_actions_observed=len(actions),
            data_as_of_earliest=min(observations, default=None),
            data_as_of_latest=max(observations, default=None),
        )

    async def _provider_securities(self, provider_name: str) -> list[MarketSecurity]:
        rows = await self.securities.list_with_provider(provider_name)
        output: list[MarketSecurity] = []
        for security, provider_symbol in rows:
            if provider_name == "tushare" and security.market.upper() != "CN-A":
                continue
            if provider_name == "yfinance" and security.market.upper() == "CN-A":
                continue
            output.append(
                MarketSecurity(
                    security_id=security.id,
                    canonical_symbol=security.canonical_symbol,
                    provider_symbol=provider_symbol,
                    exchange=security.exchange,
                    market=security.market,
                    native_currency=security.currency,
                    asset_type=security.asset_type,
                )
            )
        return output

    async def _fx_pairs(
        self,
        portfolio_id: uuid.UUID | None,
        securities: Sequence[MarketSecurity],
    ) -> list[FxPair]:
        if portfolio_id is not None:
            portfolio = await self.portfolios.get(portfolio_id)
            portfolios = [portfolio] if portfolio is not None else []
        else:
            portfolios = await self.portfolios.list_active()
        pairs = {
            (security.native_currency.upper(), portfolio.base_currency.upper())
            for portfolio in portfolios
            for security in securities
            if security.native_currency.upper() != portfolio.base_currency.upper()
        }
        return [FxPair(base, quote) for base, quote in sorted(pairs)]


def default_market_data_providers() -> dict[str, MarketDataProvider]:
    return {
        "tushare": TushareProvider(settings.TUSHARE_TOKEN),
        "yfinance": YFinanceProvider(),
    }


def configured_provider_names() -> tuple[str, ...]:
    return _normalize_provider_names(settings.MARKET_DATA_PROVIDERS.split(","))


def _normalize_provider_names(provider_names: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(name.strip().lower() for name in provider_names if name.strip())
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
