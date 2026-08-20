"""As-of portfolio valuation with integrity status and complete lineage."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    ticker: str
    quantity: Decimal
    average_cost: Decimal
    price_bar_id: uuid.UUID
    fx_rate_id: uuid.UUID | None
    native_price: Decimal
    native_currency: str
    valuation_fx_rate: Decimal
    market_value_base: Decimal
    cost_basis_native: Decimal
    cost_basis_base_at_trade: Decimal | None
    local_price_pnl: Decimal | None
    fx_pnl: Decimal | None
    total_pnl_base: Decimal | None
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
        knowledge_as_of: datetime | None = None,
        source: str = "ledger_rebuild",
        sync_run_id: uuid.UUID | None = None,
        data_source_context: dict[str, object] | None = None,
    ) -> ValuationResult:
        cutoff = _as_utc(as_of)
        knowledge_cutoff = _as_utc(knowledge_as_of or cutoff)
        portfolio = await self.portfolios.get(portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        rebuilt = await self.ledger.rebuild(
            portfolio_id,
            as_of=cutoff,
            knowledge_as_of=knowledge_cutoff,
        )
        rows, warnings, lineage_dates, unpriced_assets, fx_lineage = (
            await self._value_positions(portfolio, rebuilt, knowledge_cutoff)
        )
        priced_cash_value, cash_complete = await self._value_cash(
            rebuilt,
            portfolio.base_currency,
            warnings,
            lineage_dates,
            unpriced_assets,
            fx_lineage,
            knowledge_cutoff,
        )
        priced_market_value = sum((row["market_value_base"] for row in rows), ZERO)
        priced_market_value += priced_cash_value
        expected_assets = len(rebuilt.positions) + sum(
            1 for amount in rebuilt.cash_balances.values() if amount != ZERO
        )
        priced_assets = len(rows) + sum(
            1 for amount in rebuilt.cash_balances.values() if amount != ZERO
        )
        if not cash_complete:
            priced_assets -= sum(
                1
                for item in unpriced_assets
                if item.get("asset_type") == "cash"
            )
        unpriced_count = len(unpriced_assets)
        coverage = (
            Decimal(priced_assets) / Decimal(expected_assets)
            if expected_assets > 0
            else ONE
        )
        complete_cost = all(row["cost_basis_base_at_trade"] is not None for row in rows)
        if unpriced_count == 0 and complete_cost:
            valuation_status = "complete"
        elif priced_assets > 0 or expected_assets == 0:
            valuation_status = "partial"
        else:
            valuation_status = "unavailable"
        total_market_value = (
            priced_market_value if valuation_status == "complete" else None
        )
        total_cost_basis = (
            sum(
                (row["cost_basis_base_at_trade"] or ZERO for row in rows),
                ZERO,
            )
            if valuation_status == "complete" and complete_cost
            else None
        )
        total_pnl = (
            sum((row["total_pnl_base"] or ZERO for row in rows), ZERO)
            if valuation_status == "complete" and complete_cost
            else None
        )
        earliest = min((item[0] for item in lineage_dates), default=None)
        latest = max((item[0] for item in lineage_dates), default=None)
        stale_days = [max(0, (cutoff.date() - item[1]).days) for item in lineage_dates]
        max_staleness_days = max(stale_days) if stale_days else None
        input_hash = await self._input_hash(
            portfolio=portfolio,
            rebuilt=rebuilt,
            rows=rows,
            unpriced_assets=unpriced_assets,
            cash_fx_lineage=fx_lineage,
            knowledge_as_of=knowledge_cutoff,
        )
        snapshot_warnings = list(dict.fromkeys([*rebuilt.warnings, *warnings]))
        if not complete_cost:
            snapshot_warnings.append("valuation_partial:missing_historical_cost_fx")
        if valuation_status != "complete":
            snapshot_warnings.append(
                f"valuation_{valuation_status}:coverage={float(coverage):.4f}"
            )
        valuation = await self.valuations.upsert(
            PortfolioValuationSnapshot(
                portfolio_id=portfolio.id,
                as_of=cutoff,
                valuation_date=cutoff.date(),
                base_currency=portfolio.base_currency,
                total_market_value=total_market_value,
                priced_market_value=priced_market_value,
                total_cost_basis=total_cost_basis,
                cash_value=priced_cash_value if cash_complete else None,
                unrealized_pnl=total_pnl,
                valuation_status=valuation_status,
                priced_asset_count=priced_assets,
                unpriced_asset_count=unpriced_count,
                unpriced_assets=unpriced_assets,
                data_as_of=latest or rebuilt.last_transaction_at,
                data_as_of_earliest=earliest,
                data_as_of_latest=latest,
                max_staleness_days=max_staleness_days,
                coverage_ratio=coverage,
                source=source,
                sync_run_id=sync_run_id,
                input_hash=input_hash,
                history_completeness=rebuilt.history_completeness,
                cash_balances={key: str(value) for key, value in rebuilt.cash_balances.items()},
                warnings=snapshot_warnings,
                config_snapshot={
                    "cost_basis_method": portfolio.cost_basis_method,
                    "display_timezone": portfolio.display_timezone,
                    "benchmark_security_id": (
                        str(portfolio.benchmark_security_id)
                        if portfolio.benchmark_security_id
                        else None
                    ),
                    "cash_fx_lineage": fx_lineage,
                    "valuation_as_of": cutoff.isoformat(),
                    "knowledge_as_of": knowledge_cutoff.isoformat(),
                    "data_source_context": dict(data_source_context or {}),
                },
            )
        )
        stored_positions: list[PositionSnapshot] = []
        for row in rows:
            weight = (
                row["market_value_base"] / total_market_value
                if total_market_value is not None and total_market_value > ZERO
                else None
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
                    cost_basis_base=row["cost_basis_base_at_trade"],
                    unrealized_pnl_base=row["total_pnl_base"],
                    cost_basis_native=row["cost_basis_native"],
                    cost_basis_base_at_trade=row["cost_basis_base_at_trade"],
                    local_price_pnl=row["local_price_pnl"],
                    fx_pnl=row["fx_pnl"],
                    total_pnl_base=row["total_pnl_base"],
                    weight=weight,
                    base_currency=portfolio.base_currency,
                    snapshot_data={
                        "price_trade_date": row["price_trade_date"],
                        "price_source": row["price_source"],
                        "price_data_as_of": row["price_data_as_of"],
                        "knowledge_as_of": knowledge_cutoff.isoformat(),
                        "cost_basis_semantics": "historical_trade_date_fx",
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
        knowledge_as_of: datetime,
    ) -> tuple[
        list[_ValuationRow],
        list[str],
        list[tuple[datetime, date]],
        list[dict[str, str]],
        dict[str, object],
    ]:
        rows: list[_ValuationRow] = []
        warnings: list[str] = []
        lineage_dates: list[tuple[datetime, date]] = []
        unpriced: list[dict[str, str]] = []
        fx_lineage: dict[str, object] = {}
        for position in rebuilt.positions:
            security = await self.session.get(Security, position.security_id)
            if security is None:
                warning = f"missing_security:{position.security_id}"
                warnings.append(warning)
                unpriced.append(
                    {
                        "asset_type": "security",
                        "security_id": str(position.security_id),
                        "ticker": "",
                        "reason": "missing_security_master",
                    }
                )
                continue
            price = await self.prices.latest_at_or_before(
                security.id,
                rebuilt.as_of.date(),
                preferred_source=(
                    "tushare" if security.market.upper() == "CN-A" else "yfinance_research"
                ),
                knowledge_as_of=knowledge_as_of,
            )
            if price is None:
                warnings.append(f"missing_price:{security.canonical_symbol}")
                unpriced.append(_unpriced_security(security, "missing_price"))
                continue
            native_price = price.adjusted_close or price.close
            fx_rate, fx_row_id, fx_date, fx_data_as_of = await self._valuation_fx(
                security.currency,
                portfolio.base_currency,
                rebuilt.as_of,
                knowledge_as_of,
                warnings,
                fx_lineage,
            )
            if fx_rate is None:
                warnings.append(f"missing_fx:{security.currency}/{portfolio.base_currency}")
                unpriced.append(_unpriced_security(security, "missing_valuation_fx"))
                continue
            if price.source.startswith("legacy_csv_"):
                warnings.append(
                    f"non_market_price_source:{security.canonical_symbol}:{price.source}"
                )
            native_market_value = position.quantity * native_price
            market_value_base = native_market_value * fx_rate
            historical_cost = (
                position.cost_basis_base_at_trade
                if position.historical_fx_complete
                else None
            )
            local_price_pnl = (
                (native_market_value - position.cost_basis_native) * fx_rate
                if historical_cost is not None
                else None
            )
            fx_pnl = (
                position.cost_basis_native * fx_rate - historical_cost
                if historical_cost is not None
                else None
            )
            total_pnl = (
                market_value_base - historical_cost
                if historical_cost is not None
                else None
            )
            lineage_dates.append((_as_utc(price.data_as_of), price.trade_date))
            if fx_data_as_of is not None and fx_date is not None:
                lineage_dates.append((_as_utc(fx_data_as_of), fx_date))
            rows.append(
                {
                    "security_id": security.id,
                    "ticker": security.canonical_symbol,
                    "quantity": position.quantity,
                    "average_cost": position.average_cost_native,
                    "price_bar_id": price.id,
                    "fx_rate_id": fx_row_id,
                    "native_price": native_price,
                    "native_currency": security.currency,
                    "valuation_fx_rate": fx_rate,
                    "market_value_base": market_value_base,
                    "cost_basis_native": position.cost_basis_native,
                    "cost_basis_base_at_trade": historical_cost,
                    "local_price_pnl": local_price_pnl,
                    "fx_pnl": fx_pnl,
                    "total_pnl_base": total_pnl,
                    "price_trade_date": price.trade_date.isoformat(),
                    "price_source": price.source,
                    "price_data_as_of": _as_utc(price.data_as_of).isoformat(),
                }
            )
        return rows, warnings, lineage_dates, unpriced, fx_lineage

    async def _value_cash(
        self,
        rebuilt: PositionRebuildResult,
        base_currency: str,
        warnings: list[str],
        lineage_dates: list[tuple[datetime, date]],
        unpriced: list[dict[str, str]],
        fx_lineage: dict[str, object],
        knowledge_as_of: datetime,
    ) -> tuple[Decimal, bool]:
        total = ZERO
        complete = True
        for currency, amount in rebuilt.cash_balances.items():
            if amount == ZERO:
                continue
            rate, _, rate_date, data_as_of = await self._valuation_fx(
                currency,
                base_currency,
                rebuilt.as_of,
                knowledge_as_of,
                warnings,
                fx_lineage,
            )
            if rate is None:
                complete = False
                warnings.append(f"missing_cash_fx:{currency}/{base_currency}")
                unpriced.append(
                    {
                        "asset_type": "cash",
                        "currency": currency,
                        "reason": "missing_valuation_fx",
                    }
                )
                continue
            total += amount * rate
            if data_as_of is not None and rate_date is not None:
                lineage_dates.append((_as_utc(data_as_of), rate_date))
        return total, complete

    async def _valuation_fx(
        self,
        native_currency: str,
        base_currency: str,
        valuation_as_of: datetime,
        knowledge_as_of: datetime,
        warnings: list[str],
        lineage: dict[str, object],
    ) -> tuple[Decimal | None, uuid.UUID | None, date | None, datetime | None]:
        native = native_currency.upper()
        base = base_currency.upper()
        if native == base:
            return ONE, None, valuation_as_of.date(), None
        direct = await self.fx_rates.latest_at_or_before(
            native,
            base,
            valuation_as_of.date(),
            knowledge_as_of=knowledge_as_of,
        )
        if direct is not None:
            lineage[f"{native}/{base}"] = _fx_lineage(direct, direct.rate, inverted=False)
            return direct.rate, direct.id, direct.rate_date, direct.data_as_of
        inverse = await self.fx_rates.latest_at_or_before(
            base,
            native,
            valuation_as_of.date(),
            knowledge_as_of=knowledge_as_of,
        )
        if inverse is not None and inverse.rate > ZERO:
            warnings.append(f"inverted_fx_rate:{base}/{native}")
            rate = ONE / inverse.rate
            lineage[f"{native}/{base}"] = _fx_lineage(inverse, rate, inverted=True)
            return rate, inverse.id, inverse.rate_date, inverse.data_as_of
        return None, None, None, None

    async def _input_hash(
        self,
        *,
        portfolio: Portfolio,
        rebuilt: PositionRebuildResult,
        rows: list[_ValuationRow],
        unpriced_assets: list[dict[str, str]],
        cash_fx_lineage: dict[str, object],
        knowledge_as_of: datetime,
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
            "knowledge_as_of": knowledge_as_of.isoformat(),
            "transactions": [
                {
                    "id": str(item.id),
                    "updated_at": _as_utc(item.updated_at).isoformat(),
                    "source_record_hash": item.source_record_hash,
                    "fx_rate_to_base": (
                        str(item.fx_rate_to_base) if item.fx_rate_to_base else None
                    ),
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
                    "cost_basis_base_at_trade": str(row["cost_basis_base_at_trade"]),
                    "price_data_as_of": row["price_data_as_of"],
                }
                for row in rows
            ],
            "unpriced_assets": sorted(
                unpriced_assets,
                key=lambda item: json.dumps(item, sort_keys=True),
            ),
            "cash_balances": {
                key: str(value) for key, value in rebuilt.cash_balances.items()
            },
            "cash_fx_lineage": cash_fx_lineage,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _unpriced_security(security: Security, reason: str) -> dict[str, str]:
    return {
        "asset_type": "security",
        "security_id": str(security.id),
        "ticker": security.canonical_symbol,
        "currency": security.currency,
        "reason": reason,
    }


def _fx_lineage(row, rate: Decimal, *, inverted: bool) -> dict[str, object]:
    return {
        "fx_rate_id": str(row.id),
        "rate_date": row.rate_date.isoformat(),
        "source": row.source,
        "inverted": inverted,
        "rate": str(rate),
        "data_as_of": _as_utc(row.data_as_of).isoformat(),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
