"""As-of portfolio valuation with complete price and FX lineage."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Portfolio, PortfolioValuationSnapshot, PositionSnapshot, Security
from app.db.repositories import (
    FxRateRepository,
    PortfolioRepository,
    PortfolioValuationRepository,
    PositionSnapshotRepository,
    PriceBarRepository,
)
from app.domain import PositionRebuildResult
from app.services.transaction_ledger import TransactionLedgerService

ZERO = Decimal("0")
ONE = Decimal("1")


class _ValuationRow(TypedDict):
    security_id: uuid.UUID
    quantity: Decimal
    average_cost: Decimal
    price_bar_id: uuid.UUID
    fx_rate_id: uuid.UUID | None
    native_price: Decimal
    native_currency: str
    valuation_fx_rate: Decimal
    market_value_base: Decimal
    cost_basis_base: Decimal
    unrealized_pnl_base: Decimal
    price_trade_date: str
    price_source: str
    price_data_as_of: str


@dataclass(frozen=True, slots=True)
class ValuationResult:
    valuation: PortfolioValuationSnapshot
    positions: tuple[PositionSnapshot, ...]


class PortfolioValuationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolios = PortfolioRepository(session)
        self.valuations = PortfolioValuationRepository(session)
        self.position_snapshots = PositionSnapshotRepository(session)
        self.prices = PriceBarRepository(session)
        self.fx_rates = FxRateRepository(session)
        self.ledger = TransactionLedgerService(session)

    async def value(
        self,
        *,
        portfolio_id: uuid.UUID,
        as_of: datetime,
        source: str = "ledger_rebuild",
        sync_run_id: uuid.UUID | None = None,
    ) -> ValuationResult:
        cutoff = _as_utc(as_of)
        portfolio = await self.portfolios.get(portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        rebuilt = await self.ledger.rebuild(portfolio_id, as_of=cutoff)
        rows, warnings, data_times, cash_fx_lineage = await self._value_positions(
            portfolio, rebuilt
        )
        cash_value = await self._value_cash(
            rebuilt,
            portfolio.base_currency,
            warnings,
            data_times,
            cash_fx_lineage,
        )
        securities_value = sum((row["market_value_base"] for row in rows), ZERO)
        total_market_value = securities_value + cash_value
        total_cost_basis = sum((row["cost_basis_base"] for row in rows), ZERO)
        unrealized_pnl = sum((row["unrealized_pnl_base"] for row in rows), ZERO)
        input_hash = await self._input_hash(
            portfolio=portfolio,
            rebuilt=rebuilt,
            rows=rows,
            cash_fx_lineage=cash_fx_lineage,
        )
        data_as_of = max(data_times) if data_times else rebuilt.last_transaction_at
        valuation = await self.valuations.upsert(
            PortfolioValuationSnapshot(
                portfolio_id=portfolio.id,
                as_of=cutoff,
                valuation_date=cutoff.date(),
                base_currency=portfolio.base_currency,
                total_market_value=total_market_value,
                total_cost_basis=total_cost_basis,
                cash_value=cash_value,
                unrealized_pnl=unrealized_pnl,
                data_as_of=data_as_of,
                source=source,
                sync_run_id=sync_run_id,
                input_hash=input_hash,
                history_completeness=rebuilt.history_completeness,
                cash_balances={key: str(value) for key, value in rebuilt.cash_balances.items()},
                warnings=list(dict.fromkeys([*rebuilt.warnings, *warnings])),
                config_snapshot={
                    "cost_basis_method": portfolio.cost_basis_method,
                    "display_timezone": portfolio.display_timezone,
                    "benchmark_security_id": (
                        str(portfolio.benchmark_security_id)
                        if portfolio.benchmark_security_id
                        else None
                    ),
                    "cash_fx_lineage": cash_fx_lineage,
                },
            )
        )
        stored_positions: list[PositionSnapshot] = []
        for row in rows:
            weight = (
                row["market_value_base"] / total_market_value
                if total_market_value > ZERO
                else ZERO
            )
            snapshot, _ = await self.position_snapshots.add_idempotent(
                PositionSnapshot(
                    valuation_snapshot_id=valuation.id,
                    security_id=row["security_id"],
                    quantity=row["quantity"],
                    average_cost=row["average_cost"],
                    price_bar_id=row["price_bar_id"],
                    fx_rate_id=row["fx_rate_id"],
                    native_price=row["native_price"],
                    native_currency=row["native_currency"],
                    valuation_fx_rate=row["valuation_fx_rate"],
                    market_value_base=row["market_value_base"],
                    cost_basis_base=row["cost_basis_base"],
                    unrealized_pnl_base=row["unrealized_pnl_base"],
                    weight=weight,
                    base_currency=portfolio.base_currency,
                    snapshot_data={
                        "price_trade_date": row["price_trade_date"],
                        "price_source": row["price_source"],
                        "price_data_as_of": row["price_data_as_of"],
                    },
                )
            )
            stored_positions.append(snapshot)
        await self.position_snapshots.delete_not_in(
            valuation.id, {row["security_id"] for row in rows}
        )
        return ValuationResult(valuation=valuation, positions=tuple(stored_positions))

    async def _value_positions(
        self,
        portfolio: Portfolio,
        rebuilt: PositionRebuildResult,
    ) -> tuple[list[_ValuationRow], list[str], list[datetime], dict[str, object]]:
        rows: list[_ValuationRow] = []
        warnings: list[str] = []
        data_times: list[datetime] = []
        fx_lineage: dict[str, object] = {}
        for position in rebuilt.positions:
            security = await self.session.get(Security, position.security_id)
            if security is None:
                warnings.append(f"missing_security:{position.security_id}")
                continue
            preferred_source = (
                "tushare" if security.market.upper() == "CN-A" else "yfinance_research"
            )
            price = await self.prices.latest_at_or_before(
                security.id,
                rebuilt.as_of.date(),
                preferred_source=preferred_source,
                knowledge_as_of=rebuilt.as_of,
            )
            if price is None:
                warnings.append(f"missing_price:{security.canonical_symbol}")
                continue
            native_price = price.adjusted_close or price.close
            fx_rate, fx_row_id = await self._valuation_fx(
                security.currency,
                portfolio.base_currency,
                rebuilt.as_of,
                warnings,
                fx_lineage,
            )
            if fx_rate is None:
                warnings.append(
                    f"missing_fx:{security.currency}/{portfolio.base_currency}"
                )
                continue
            native_market_value = position.quantity * native_price
            market_value_base = native_market_value * fx_rate
            cost_basis_base = position.cost_basis_native * fx_rate
            data_times.append(_as_utc(price.data_as_of))
            if fx_row_id is not None:
                fx_row = await self.fx_rates.get(fx_row_id)
                if fx_row is not None:
                    data_times.append(_as_utc(fx_row.data_as_of))
            rows.append(
                {
                    "security_id": security.id,
                    "quantity": position.quantity,
                    "average_cost": position.average_cost_native,
                    "price_bar_id": price.id,
                    "fx_rate_id": fx_row_id,
                    "native_price": native_price,
                    "native_currency": security.currency,
                    "valuation_fx_rate": fx_rate,
                    "market_value_base": market_value_base,
                    "cost_basis_base": cost_basis_base,
                    "unrealized_pnl_base": market_value_base - cost_basis_base,
                    "price_trade_date": price.trade_date.isoformat(),
                    "price_source": price.source,
                    "price_data_as_of": _as_utc(price.data_as_of).isoformat(),
                }
            )
        return rows, warnings, data_times, fx_lineage

    async def _value_cash(
        self,
        rebuilt: PositionRebuildResult,
        base_currency: str,
        warnings: list[str],
        data_times: list[datetime],
        fx_lineage: dict[str, object],
    ) -> Decimal:
        total = ZERO
        for currency, amount in rebuilt.cash_balances.items():
            rate, rate_id = await self._valuation_fx(
                currency, base_currency, rebuilt.as_of, warnings, fx_lineage
            )
            if rate is None:
                warnings.append(f"missing_cash_fx:{currency}/{base_currency}")
                continue
            total += amount * rate
            if rate_id:
                fx_row = await self.fx_rates.get(rate_id)
                if fx_row:
                    data_times.append(_as_utc(fx_row.data_as_of))
        return total

    async def _valuation_fx(
        self,
        native_currency: str,
        base_currency: str,
        as_of: datetime,
        warnings: list[str],
        lineage: dict[str, object],
    ) -> tuple[Decimal | None, uuid.UUID | None]:
        native = native_currency.upper()
        base = base_currency.upper()
        if native == base:
            return ONE, None
        direct = await self.fx_rates.latest_at_or_before(
            native,
            base,
            as_of.date(),
            knowledge_as_of=as_of,
        )
        if direct is not None:
            lineage[f"{native}/{base}"] = {
                "fx_rate_id": str(direct.id),
                "rate_date": direct.rate_date.isoformat(),
                "source": direct.source,
                "inverted": False,
                "rate": str(direct.rate),
                "data_as_of": _as_utc(direct.data_as_of).isoformat(),
            }
            return direct.rate, direct.id
        inverse = await self.fx_rates.latest_at_or_before(
            base,
            native,
            as_of.date(),
            knowledge_as_of=as_of,
        )
        if inverse is not None and inverse.rate > ZERO:
            warnings.append(f"inverted_fx_rate:{base}/{native}")
            lineage[f"{native}/{base}"] = {
                "fx_rate_id": str(inverse.id),
                "rate_date": inverse.rate_date.isoformat(),
                "source": inverse.source,
                "inverted": True,
                "rate": str(ONE / inverse.rate),
                "data_as_of": _as_utc(inverse.data_as_of).isoformat(),
            }
            return ONE / inverse.rate, inverse.id
        return None, None

    async def _input_hash(
        self,
        *,
        portfolio: Portfolio,
        rebuilt: PositionRebuildResult,
        rows: list[_ValuationRow],
        cash_fx_lineage: dict[str, object],
    ) -> str:
        transactions = await self.ledger.list_transactions(
            portfolio.id, as_of=rebuilt.as_of
        )
        payload = {
            "portfolio": {
                "id": str(portfolio.id),
                "base_currency": portfolio.base_currency,
                "cost_basis_method": portfolio.cost_basis_method,
            },
            "as_of": rebuilt.as_of.isoformat(),
            "transactions": [
                {
                    "id": str(item.id),
                    "updated_at": _as_utc(item.updated_at).isoformat(),
                    "source_record_hash": item.source_record_hash,
                }
                for item in transactions
            ],
            "positions": [
                {
                    "security_id": str(row["security_id"]),
                    "price_bar_id": str(row["price_bar_id"]),
                    "fx_rate_id": str(row["fx_rate_id"]) if row["fx_rate_id"] else None,
                    "quantity": str(row["quantity"]),
                    "native_price": str(row["native_price"]),
                    "valuation_fx_rate": str(row["valuation_fx_rate"]),
                    "price_data_as_of": row["price_data_as_of"],
                }
                for row in rows
            ],
            "cash_balances": {
                key: str(value) for key, value in rebuilt.cash_balances.items()
            },
            "cash_fx_lineage": cash_fx_lineage,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
