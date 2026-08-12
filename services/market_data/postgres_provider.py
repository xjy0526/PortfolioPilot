"""Adjusted-close history backed exclusively by PostgreSQL price facts."""
from __future__ import annotations

from datetime import UTC, date, datetime, time

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PriceBar, Security


class PostgresPriceHistoryProvider:
    source = "postgres_price_bars"

    def __init__(self, session: AsyncSession, *, as_of: datetime | None = None) -> None:
        self.session = session
        self.as_of = _as_utc(as_of) if as_of else None

    async def fetch_adjusted_close(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        normalized = [ticker.strip().upper() for ticker in tickers if ticker.strip()]
        if not normalized:
            return pd.DataFrame()
        knowledge_cutoff = self.as_of or datetime.combine(end_date, time.max, tzinfo=UTC)
        statement = (
            select(Security, PriceBar)
            .join(PriceBar, PriceBar.security_id == Security.id)
            .where(
                Security.canonical_symbol.in_(normalized),
                PriceBar.trade_date >= start_date,
                PriceBar.trade_date <= end_date,
                PriceBar.data_as_of <= knowledge_cutoff,
                PriceBar.is_final.is_(True),
                PriceBar.quality_status == "valid",
            )
            .order_by(
                Security.canonical_symbol,
                PriceBar.trade_date,
                PriceBar.data_as_of.desc(),
            )
        )
        selected: dict[tuple[str, date], tuple[int, datetime, float]] = {}
        for security, bar in await self.session.execute(statement):
            preferred = (
                "tushare" if security.market.upper() == "CN-A" else "yfinance_research"
            )
            priority = int(bar.source == preferred)
            value = bar.adjusted_close or bar.close
            key = (security.canonical_symbol.upper(), bar.trade_date)
            candidate = (priority, _as_utc(bar.data_as_of), float(value))
            current = selected.get(key)
            if current is None or candidate[:2] > current[:2]:
                selected[key] = candidate
        if not selected:
            return pd.DataFrame()
        records = [
            {"date": trade_date, "ticker": ticker, "adjusted_close": candidate[2]}
            for (ticker, trade_date), candidate in selected.items()
        ]
        frame = pd.DataFrame(records)
        return frame.pivot_table(
            index="date", columns="ticker", values="adjusted_close", aggfunc="last"
        ).sort_index()


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("timestamp is required")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

