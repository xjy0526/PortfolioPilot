"""Tushare Pro adapter for A-share master data and daily market facts."""
from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from datetime import date
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


class TushareProvider:
    name = "tushare"
    research_only = False

    def __init__(self, token: str) -> None:
        self.token = token.strip()

    def _client(self) -> Any:
        if not self.token:
            raise RuntimeError("TUSHARE_TOKEN is required for the Tushare provider")
        try:
            tushare = importlib.import_module("tushare")
        except ImportError as exc:
            raise RuntimeError("tushare is not installed") from exc
        return tushare.pro_api(self.token)

    async def fetch_security_master(
        self, securities: Sequence[MarketSecurity] = ()
    ) -> list[SecurityMasterRecord]:
        return await asyncio.to_thread(self._fetch_security_master_sync)

    def _fetch_security_master_sync(self) -> list[SecurityMasterRecord]:
        frame = self._client().stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,exchange,list_date",
        )
        records: list[SecurityMasterRecord] = []
        for row in frame.to_dict(orient="records"):
            provider_symbol = str(row.get("ts_code", "")).upper()
            if not provider_symbol:
                continue
            exchange = "SSE" if provider_symbol.endswith(".SH") else "SZSE"
            records.append(
                SecurityMasterRecord(
                    canonical_symbol=provider_symbol,
                    provider_symbol=provider_symbol,
                    name=str(row.get("name") or provider_symbol),
                    exchange=exchange,
                    market="CN-A",
                    currency="CNY",
                    country="CN",
                    sector=str(row.get("industry") or "Unknown"),
                    metadata={
                        "area": _json_value(row.get("area")),
                        "board": _json_value(row.get("market")),
                        "list_date": _json_value(row.get("list_date")),
                    },
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
        client = self._client()
        data_as_of = utc_now()
        records: list[PriceBarRecord] = []
        for security in securities:
            daily = client.daily(
                ts_code=security.provider_symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            factors = client.adj_factor(
                ts_code=security.provider_symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            factor_by_date = {
                str(row.get("trade_date")): _decimal(row.get("adj_factor"))
                for row in factors.to_dict(orient="records")
            }
            dated_factors = {
                trade_date: value
                for trade_date, value in factor_by_date.items()
                if value is not None and value > 0
            }
            latest_factor = dated_factors[max(dated_factors)] if dated_factors else None
            for row in daily.to_dict(orient="records"):
                trade_date = _compact_date(row.get("trade_date"))
                close = _required_positive_decimal(row.get("close"), "close")
                factor = factor_by_date.get(str(row.get("trade_date")))
                adjusted = (
                    close * factor / latest_factor
                    if factor and latest_factor and latest_factor > 0
                    else close
                )
                records.append(
                    PriceBarRecord(
                        security_id=security.security_id,
                        trade_date=trade_date,
                        native_currency=security.native_currency,
                        raw_close=close,
                        adjusted_close=adjusted,
                        adjustment_factor=factor,
                        source=self.name,
                        data_as_of=data_as_of,
                        is_final=trade_date < data_as_of.date(),
                        quality_status="valid",
                        open=_decimal(row.get("open")),
                        high=_decimal(row.get("high")),
                        low=_decimal(row.get("low")),
                        volume=_decimal(row.get("vol")),
                        raw_payload={
                            "provider_symbol": security.provider_symbol,
                            "research_only": False,
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
        # Tushare is not used as the FX source in this phase.
        return []

    async def fetch_corporate_actions(
        self,
        securities: Sequence[MarketSecurity],
        *,
        start: date,
        end: date,
    ) -> list[CorporateActionRecord]:
        bars = await self.fetch_price_bars(securities, start=start, end=end)
        return [
            CorporateActionRecord(
                security_id=bar.security_id,
                action_date=bar.trade_date,
                action_type="adjustment_factor",
                value=bar.adjustment_factor,
                source=self.name,
                data_as_of=bar.data_as_of,
                raw_payload=bar.raw_payload,
            )
            for bar in bars
            if bar.adjustment_factor is not None
        ]

    async def fetch_trade_calendar(self, *, start: date, end: date) -> list[date]:
        frame = await asyncio.to_thread(
            self._client().trade_cal,
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            is_open="1",
        )
        return [_compact_date(value) for value in frame["cal_date"].tolist()]


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
        raise ValueError(f"Tushare returned an invalid {field}")
    return result


def _compact_date(value: object) -> date:
    return date.fromisoformat(f"{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}")


def _json_value(value: object) -> object:
    if value is None:
        return None
    try:
        numeric = Decimal(str(value))
        if not numeric.is_finite():
            return None
    except (ValueError, ArithmeticError):
        pass
    return str(value)
