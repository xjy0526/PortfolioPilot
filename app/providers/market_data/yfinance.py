"""Research-only yfinance adapter for US securities, ETFs, and FX."""
from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.providers.market_data.base import (
    CorporateActionRecord,
    FxPair,
    FxRateRecord,
    MarketSecurity,
    PriceBarRecord,
    SecurityMasterRecord,
)
from time_utils import utc_now


class YFinanceProvider:
    name = "yfinance_research"
    research_only = True

    @staticmethod
    def _module() -> Any:
        try:
            return importlib.import_module("yfinance")
        except ImportError as exc:
            raise RuntimeError("yfinance is not installed") from exc

    async def fetch_security_master(
        self, securities: Sequence[MarketSecurity] = ()
    ) -> list[SecurityMasterRecord]:
        return await asyncio.to_thread(self._fetch_security_master_sync, tuple(securities))

    def _fetch_security_master_sync(
        self, securities: Sequence[MarketSecurity]
    ) -> list[SecurityMasterRecord]:
        yf = self._module()
        records: list[SecurityMasterRecord] = []
        for security in securities:
            try:
                info = yf.Ticker(security.provider_symbol).get_info()
            except Exception:
                info = {}
            records.append(
                SecurityMasterRecord(
                    canonical_symbol=security.canonical_symbol,
                    provider_symbol=security.provider_symbol,
                    name=str(info.get("longName") or security.canonical_symbol),
                    exchange=security.exchange,
                    market=security.market,
                    currency=security.native_currency,
                    country=str(info.get("country") or "US")[:2].upper(),
                    asset_type=security.asset_type,
                    sector=str(info.get("sector") or "Unknown"),
                    metadata={"research_only": True},
                )
            )
        return records

    async def fetch_price_bars(
        self,
        securities: Sequence[MarketSecurity],
        *,
        start: date,
        end: date,
    ) -> list[PriceBarRecord]:
        return await asyncio.to_thread(
            self._fetch_price_bars_sync, tuple(securities), start, end
        )

    def _fetch_price_bars_sync(
        self,
        securities: Sequence[MarketSecurity],
        start: date,
        end: date,
    ) -> list[PriceBarRecord]:
        yf = self._module()
        data_as_of = utc_now()
        records: list[PriceBarRecord] = []
        for security in securities:
            frame = yf.Ticker(security.provider_symbol).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
                repair=True,
            )
            for index, row in frame.iterrows():
                trade_date = _index_date(index)
                close = _required_positive_decimal(row.get("Close"), "close")
                adjusted = _decimal(row.get("Adj Close")) or close
                records.append(
                    PriceBarRecord(
                        security_id=security.security_id,
                        trade_date=trade_date,
                        native_currency=security.native_currency,
                        raw_close=close,
                        adjusted_close=adjusted,
                        adjustment_factor=adjusted / close if close > 0 else None,
                        source=self.name,
                        data_as_of=data_as_of,
                        is_final=trade_date < data_as_of.date(),
                        quality_status="valid",
                        open=_decimal(row.get("Open")),
                        high=_decimal(row.get("High")),
                        low=_decimal(row.get("Low")),
                        volume=_decimal(row.get("Volume")),
                        raw_payload={
                            "provider_symbol": security.provider_symbol,
                            "research_only": True,
                        },
                    )
                )
        return records

    async def fetch_fx_rates(
        self,
        pairs: Sequence[FxPair],
        *,
        start: date,
        end: date,
    ) -> list[FxRateRecord]:
        return await asyncio.to_thread(self._fetch_fx_rates_sync, tuple(pairs), start, end)

    def _fetch_fx_rates_sync(
        self, pairs: Sequence[FxPair], start: date, end: date
    ) -> list[FxRateRecord]:
        yf = self._module()
        data_as_of = utc_now()
        records: list[FxRateRecord] = []
        for pair in pairs:
            symbol = f"{pair.base_currency.upper()}{pair.quote_currency.upper()}=X"
            frame = yf.Ticker(symbol).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                repair=True,
            )
            for index, row in frame.iterrows():
                rate = _required_positive_decimal(row.get("Close"), "FX rate")
                records.append(
                    FxRateRecord(
                        base_currency=pair.base_currency.upper(),
                        quote_currency=pair.quote_currency.upper(),
                        rate_date=_index_date(index),
                        rate=rate,
                        source=self.name,
                        data_as_of=data_as_of,
                        raw_payload={"provider_symbol": symbol, "research_only": True},
                    )
                )
        return records

    async def fetch_corporate_actions(
        self,
        securities: Sequence[MarketSecurity],
        *,
        start: date,
        end: date,
    ) -> list[CorporateActionRecord]:
        return await asyncio.to_thread(
            self._fetch_corporate_actions_sync, tuple(securities), start, end
        )

    def _fetch_corporate_actions_sync(
        self,
        securities: Sequence[MarketSecurity],
        start: date,
        end: date,
    ) -> list[CorporateActionRecord]:
        yf = self._module()
        data_as_of = utc_now()
        records: list[CorporateActionRecord] = []
        for security in securities:
            frame = yf.Ticker(security.provider_symbol).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                actions=True,
                repair=True,
            )
            for index, row in frame.iterrows():
                for column, action_type in (("Dividends", "dividend"), ("Stock Splits", "split")):
                    value = _decimal(row.get(column))
                    if value is None or value == 0:
                        continue
                    records.append(
                        CorporateActionRecord(
                            security_id=security.security_id,
                            action_date=_index_date(index),
                            action_type=action_type,
                            value=value,
                            source=self.name,
                            data_as_of=data_as_of,
                            raw_payload={
                                "provider_symbol": security.provider_symbol,
                                "research_only": True,
                            },
                        )
                    )
        return records


def _index_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        numeric = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    if not numeric.is_finite():
        return None
    return numeric


def _required_positive_decimal(value: object, field: str) -> Decimal:
    result = _decimal(value)
    if result is None or result <= 0:
        raise ValueError(f"yfinance returned an invalid {field}")
    return result
