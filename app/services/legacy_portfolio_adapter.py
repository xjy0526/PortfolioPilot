"""Build the unchanged dashboard model from PostgreSQL valuation snapshots."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import Portfolio, PortfolioValuationSnapshot, Security
from app.db.repositories import (
    LegacySnapshotGenerationRepository,
    PortfolioRepository,
    PortfolioValuationRepository,
    PositionSnapshotRepository,
)
from config import settings
from models import DataSourceStatus, PortfolioPosition, PortfolioSummary, StockFullData
from time_utils import utc_now


@dataclass(frozen=True, slots=True)
class LegacyPortfolioContext:
    portfolio: Portfolio
    valuation: PortfolioValuationSnapshot
    summary: PortfolioSummary


class PortfolioRebuildRequired(RuntimeError):
    """Raised when the dashboard cannot prove valuation-generation lineage."""

    def __init__(
        self,
        *,
        portfolio_id: uuid.UUID,
        active_generation_id: uuid.UUID,
        reason: str = "valuation_generation_mismatch",
    ) -> None:
        self.portfolio_id = portfolio_id
        self.active_generation_id = active_generation_id
        self.reason = reason
        super().__init__(reason)

    def as_dict(self) -> dict[str, str]:
        return {
            "error": "portfolio_rebuild_required",
            "portfolio_id": str(self.portfolio_id),
            "active_generation_id": str(self.active_generation_id),
            "reason": self.reason,
        }


class LegacyPortfolioAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolios = PortfolioRepository(session)
        self.valuations = PortfolioValuationRepository(session)
        self.positions = PositionSnapshotRepository(session)
        self.generations = LegacySnapshotGenerationRepository(session)

    async def load(
        self,
        *,
        portfolio_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
        principal: Principal | None = None,
    ) -> LegacyPortfolioContext | None:
        portfolio = await self._resolve_portfolio(portfolio_id, principal=principal)
        if portfolio is None:
            return None
        cutoff = _as_utc(as_of or utc_now())
        active_generation = await self.generations.get_active(portfolio.id)
        if active_generation is not None:
            valuation = await self.valuations.latest_for_legacy_generation(
                portfolio.id,
                active_generation.id,
                cutoff,
            )
            if valuation is None:
                raise PortfolioRebuildRequired(
                    portfolio_id=portfolio.id,
                    active_generation_id=active_generation.id,
                )
        else:
            valuation = await self.valuations.latest_for_source(
                portfolio.id,
                source="ledger_rebuild",
                as_of=cutoff,
            )
        if valuation is None:
            return None
        if valuation.valuation_status != "complete" or valuation.total_market_value is None:
            return None
        rows = await self.positions.list_at(valuation.id)
        stocks: list[StockFullData] = []
        for row in rows:
            security = await self.session.get(Security, row.security_id)
            if security is None or row.quantity <= 0:
                continue
            base_price = float(row.market_value_base / row.quantity)
            base_cost = float((row.cost_basis_base or 0) / row.quantity)
            stocks.append(
                StockFullData(
                    position=PortfolioPosition(
                        ticker=security.canonical_symbol,
                        isin=security.isin,
                        name=security.name,
                        asset_type=_legacy_asset_type(security),
                        market=security.market,
                        exchange=security.exchange,
                        country=security.country,
                        shares=float(row.quantity),
                        avg_cost=base_cost,
                        current_price=base_price,
                        currency=valuation.base_currency,
                        price_currency=valuation.base_currency,
                        sector=security.sector,
                    ),
                    data_sources=DataSourceStatus(
                        parqet=False,
                        fmp=False,
                        technical=False,
                        yfinance=(row.snapshot_data or {}).get("price_source")
                        == "yfinance_research",
                        fear_greed=False,
                    ),
                )
            )
        if valuation.cash_value is not None and valuation.cash_value != 0:
            stocks.append(
                StockFullData(
                    position=PortfolioPosition(
                        ticker="CASH",
                        name="Cash",
                        asset_type="cash",
                        market="cash",
                        shares=1.0,
                        avg_cost=float(valuation.cash_value),
                        current_price=float(valuation.cash_value),
                        currency=valuation.base_currency,
                        price_currency=valuation.base_currency,
                        sector="Cash",
                    ),
                    data_sources=DataSourceStatus(parqet=False),
                )
            )
        total_value = float(valuation.total_market_value)
        total_cost = float((valuation.total_cost_basis or 0) + (valuation.cash_value or 0))
        total_pnl = total_value - total_cost
        summary = PortfolioSummary(
            total_value=total_value,
            total_cost=total_cost,
            total_pnl=total_pnl,
            total_pnl_percent=(total_pnl / total_cost * 100) if total_cost > 0 else 0.0,
            num_positions=len([item for item in stocks if item.position.ticker != "CASH"]),
            stocks=stocks,
            display_currency=valuation.base_currency,
            last_updated=_as_utc(valuation.as_of),
            is_demo=False,
        )
        return LegacyPortfolioContext(portfolio, valuation, summary)

    async def _resolve_portfolio(
        self,
        portfolio_id: uuid.UUID | None,
        *,
        principal: Principal | None,
    ) -> Portfolio | None:
        if portfolio_id is not None:
            if principal is not None:
                return await self.portfolios.get_accessible(
                    portfolio_id=portfolio_id,
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    access="read",
                    platform_admin=principal.is_platform_admin,
                )
            return await self.portfolios.get(portfolio_id)
        if settings.DEFAULT_PORTFOLIO_ID:
            try:
                configured_id = uuid.UUID(settings.DEFAULT_PORTFOLIO_ID)
            except ValueError:
                configured_id = None
            if configured_id is not None:
                if principal is None:
                    configured = await self.portfolios.get(configured_id)
                else:
                    configured = await self.portfolios.get_accessible(
                        portfolio_id=configured_id,
                        user_id=principal.user_id,
                        tenant_id=principal.tenant_id,
                        access="read",
                        platform_admin=principal.is_platform_admin,
                    )
                if configured is not None:
                    return configured
        if principal is not None:
            portfolios = await self.portfolios.list_accessible(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                platform_admin=principal.is_platform_admin,
            )
            return portfolios[0] if portfolios else None
        portfolios = await self.portfolios.list_active()
        return portfolios[0] if portfolios else None


def _legacy_asset_type(security: Security) -> str:
    if security.asset_type.lower() == "cash":
        return "cash"
    if security.market.upper() == "CN-A":
        return "cn_equity"
    return security.asset_type


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
