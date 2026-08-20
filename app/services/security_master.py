"""Canonical security identity and explicit provider-symbol mappings."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Security
from app.db.repositories import ProviderSymbolRepository, SecurityRepository

_SHANGHAI = {"SH", "SSE", "XSHG", "SHANGHAI"}
_SHENZHEN = {"SZ", "SZSE", "XSHE", "SHENZHEN"}
_US_EXCHANGES = {"NASDAQ", "NYSE", "NYSEARCA", "AMEX", "US"}


@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    canonical_symbol: str
    exchange: str
    market: str
    currency: str
    country: str
    provider_symbols: dict[str, str]


class SecurityMasterService:
    def __init__(self, session: AsyncSession) -> None:
        self.securities = SecurityRepository(session)
        self.provider_symbols = ProviderSymbolRepository(session)

    async def resolve_or_create(
        self,
        *,
        ticker: str,
        exchange: str,
        currency: str,
        market: str = "",
        country: str = "",
        name: str = "",
        asset_type: str = "equity",
        sector: str = "Unknown",
        metadata: dict[str, Any] | None = None,
    ) -> Security:
        identity = canonicalize_security(
            ticker=ticker,
            exchange=exchange,
            currency=currency,
            market=market,
            country=country,
        )
        security = await self.securities.get_or_create(
            canonical_symbol=identity.canonical_symbol,
            exchange=identity.exchange,
            market=identity.market,
            currency=identity.currency,
            name=name or identity.canonical_symbol,
            asset_type=asset_type,
            sector=sector,
            country=identity.country,
            security_metadata=metadata or {},
        )
        for provider, symbol in identity.provider_symbols.items():
            await self.provider_symbols.get_or_create(
                security_id=security.id,
                provider=provider,
                symbol=symbol,
                mapping_metadata={"canonical_symbol": identity.canonical_symbol},
            )
        return security


def canonicalize_security(
    *, ticker: str, exchange: str, currency: str, market: str = "", country: str = ""
) -> SecurityIdentity:
    symbol = ticker.strip().upper()
    normalized_exchange = exchange.strip().upper()
    normalized_market = market.strip().upper()
    normalized_currency = currency.strip().upper()
    normalized_country = country.strip().upper()
    if not symbol:
        raise ValueError("ticker is required for security transactions")
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("currency must be an explicit three-letter ISO code")

    if normalized_market == "POLYMARKET" or normalized_exchange == "POLYMARKET":
        return SecurityIdentity(
            canonical_symbol=symbol,
            exchange="POLYMARKET",
            market="POLYMARKET",
            currency=normalized_currency,
            country=normalized_country or "",
            provider_symbols={},
        )

    is_a_share_market = normalized_market in {"CN-A", "CHINA-A"}
    if (
        normalized_exchange in _SHANGHAI
        or (is_a_share_market and symbol.endswith((".SH", ".SS")))
        or symbol.endswith((".SH", ".SS"))
    ):
        code = symbol.removesuffix(".SH").removesuffix(".SS")
        canonical = f"{code}.SH"
        return SecurityIdentity(
            canonical_symbol=canonical,
            exchange="SSE",
            market="CN-A",
            currency=normalized_currency,
            country=normalized_country or "CN",
            provider_symbols={"tushare": canonical, "yfinance": f"{code}.SS"},
        )
    if (
        normalized_exchange in _SHENZHEN
        or (is_a_share_market and symbol.endswith(".SZ"))
        or symbol.endswith(".SZ")
    ):
        code = symbol.removesuffix(".SZ")
        canonical = f"{code}.SZ"
        return SecurityIdentity(
            canonical_symbol=canonical,
            exchange="SZSE",
            market="CN-A",
            currency=normalized_currency,
            country=normalized_country or "CN",
            provider_symbols={"tushare": canonical, "yfinance": canonical},
        )

    canonical = symbol
    normalized_exchange = normalized_exchange or "US"
    normalized_market = normalized_market or (
        "US" if normalized_exchange in _US_EXCHANGES else "GLOBAL"
    )
    return SecurityIdentity(
        canonical_symbol=canonical,
        exchange=normalized_exchange,
        market=normalized_market,
        currency=normalized_currency,
        country=normalized_country or ("US" if normalized_market == "US" else ""),
        provider_symbols={"yfinance": canonical, "fmp": canonical},
    )


async def provider_symbol_for(
    session: AsyncSession, security_id: uuid.UUID, provider: str
) -> str | None:
    from sqlalchemy import select

    from app.db.models import ProviderSymbol

    statement = select(ProviderSymbol.symbol).where(
        ProviderSymbol.security_id == security_id,
        ProviderSymbol.provider == provider,
    )
    return await session.scalar(statement)
