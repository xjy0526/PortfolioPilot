"""PostgreSQL ledger, import, FX, valuation, and compatibility integration tests."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import FxRate, Portfolio, PriceBar, Security, Transaction, User
from app.db.repositories import PortfolioRepository, SecurityRepository, UserRepository
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from app.services.portfolio_valuation import PortfolioValuationService
from app.services.transaction_import import TransactionCsvImporter
from app.services.transaction_ledger import TransactionLedgerService

pytestmark = pytest.mark.postgres
AS_OF = datetime(2026, 1, 12, 12, tzinfo=UTC)


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        _database_url(), connect_args={"server_settings": {"timezone": "UTC"}}
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _identity(session: AsyncSession, *, base_currency: str = "CNY") -> tuple[User, Portfolio]:
    user = await UserRepository(session).get_or_create(
        email=f"ledger-{uuid.uuid4()}@example.invalid"
    )
    portfolio = await PortfolioRepository(session).get_or_create(
        user_id=user.id,
        name="Ledger Integration",
        base_currency=base_currency,
    )
    return user, portfolio


def _transaction(
    portfolio_id: uuid.UUID,
    *,
    transaction_type: str,
    occurred_at: datetime,
    currency: str,
    gross: str,
    security_id: uuid.UUID | None = None,
    quantity: str = "0",
    price: str | None = None,
    external_id: str,
) -> Transaction:
    return Transaction(
        portfolio_id=portfolio_id,
        security_id=security_id,
        transaction_type=transaction_type,
        occurred_at=occurred_at,
        quantity=Decimal(quantity),
        price=Decimal(price) if price else None,
        gross_amount=Decimal(gross),
        fees=Decimal("0"),
        taxes=Decimal("0"),
        currency=currency,
        source="integration_test",
        external_id=external_id,
        raw_payload={},
    )


@pytest.mark.asyncio
async def test_historical_fx_future_isolation_and_reproducible_snapshot() -> None:
    engine, factory = await _factory()
    user_id = None
    try:
        async with factory() as session:
            async with session.begin():
                user, portfolio = await _identity(session)
                user_id = user.id
                security = await SecurityRepository(session).get_or_create(
                    canonical_symbol="AAPL",
                    exchange="NASDAQ",
                    market="US",
                    currency="USD",
                    name="Apple",
                )
                ledger = TransactionLedgerService(session)
                await ledger.add_transaction(
                    _transaction(
                        portfolio.id,
                        transaction_type="deposit",
                        occurred_at=AS_OF - timedelta(days=5),
                        currency="USD",
                        gross="2000",
                        external_id="deposit",
                    )
                )
                await ledger.add_transaction(
                    _transaction(
                        portfolio.id,
                        transaction_type="buy",
                        occurred_at=AS_OF - timedelta(days=4),
                        currency="USD",
                        gross="1000",
                        security_id=security.id,
                        quantity="10",
                        price="100",
                        external_id="buy",
                    )
                )
                session.add_all(
                    [
                        PriceBar(
                            security_id=security.id,
                            trade_date=(AS_OF - timedelta(days=2)).date(),
                            source="yfinance_research",
                            currency="USD",
                            close=Decimal("120"),
                            adjusted_close=Decimal("120"),
                            data_as_of=AS_OF - timedelta(days=1),
                            is_final=True,
                            quality_status="valid",
                            raw_payload={"research_only": True},
                        ),
                        PriceBar(
                            security_id=security.id,
                            trade_date=(AS_OF + timedelta(days=1)).date(),
                            source="yfinance_research",
                            currency="USD",
                            close=Decimal("200"),
                            adjusted_close=Decimal("200"),
                            data_as_of=AS_OF + timedelta(days=1),
                            is_final=True,
                            quality_status="valid",
                            raw_payload={"research_only": True},
                        ),
                        FxRate(
                            base_currency="USD",
                            quote_currency="CNY",
                            rate_date=(AS_OF - timedelta(days=2)).date(),
                            source="integration_test",
                            rate=Decimal("7"),
                            data_as_of=AS_OF - timedelta(days=1),
                            raw_payload={},
                        ),
                        FxRate(
                            base_currency="USD",
                            quote_currency="CNY",
                            rate_date=(AS_OF + timedelta(days=1)).date(),
                            source="integration_test",
                            rate=Decimal("8"),
                            data_as_of=AS_OF + timedelta(days=1),
                            raw_payload={},
                        ),
                    ]
                )
                await session.flush()
                first = await PortfolioValuationService(session).value(
                    portfolio_id=portfolio.id,
                    as_of=AS_OF,
                )
                second = await PortfolioValuationService(session).value(
                    portfolio_id=portfolio.id,
                    as_of=AS_OF,
                )

                assert first.valuation.id == second.valuation.id
                assert first.valuation.input_hash == second.valuation.input_hash
                assert first.valuation.total_market_value == Decimal("15400")
                assert first.valuation.cash_value == Decimal("7000")
                assert len(first.positions) == 1
                position = first.positions[0]
                assert position.native_price == Decimal("120")
                assert position.valuation_fx_rate == Decimal("7")
                assert position.market_value_base == Decimal("8400")
                assert position.price_bar_id is not None
                assert position.fx_rate_id is not None

                legacy = await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio.id, as_of=AS_OF
                )
                assert legacy is not None
                assert legacy.summary.total_value == 15400.0
                assert legacy.summary.stocks[0].position.ticker == "AAPL"

            await session.delete(user)
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_csv_import_is_atomic_idempotent_and_marks_incomplete_history() -> None:
    engine, factory = await _factory()
    try:
        async with factory() as session:
            async with session.begin():
                user, portfolio = await _identity(session, base_currency="USD")
                content = b"\n".join(
                    [
                        b"ticker,shares,buy_price,buy_date,currency,sector,name,asset_type,market,exchange,country",
                        b"AAPL,10,100,2026-01-02,USD,Technology,Apple,equity,US,NASDAQ,US",
                        b"CASH,1,5000,2026-01-02,USD,Cash,Cash,cash,Cash,Cash,US",
                    ]
                )
                importer = TransactionCsvImporter(session)
                first = await importer.import_bytes(
                    portfolio_id=portfolio.id,
                    filename="legacy.csv",
                    content=content,
                )
                replay = await importer.import_bytes(
                    portfolio_id=portfolio.id,
                    filename="legacy.csv",
                    content=content,
                )
                rows = await TransactionLedgerService(session).list_transactions(portfolio.id)

                assert first.status == "completed"
                assert first.accepted_rows == 2
                assert first.history_completeness == "opening_balance_only"
                assert replay.idempotent_replay is True
                assert replay.import_batch_id == first.import_batch_id
                assert len(rows) == 2
                assert {row.transaction_type for row in rows} == {"opening_balance", "deposit"}

            await session.delete(user)
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_advisory_lock_allows_only_one_sync_owner() -> None:
    engine, factory = await _factory()
    try:
        async with factory() as first, factory() as second:
            async with first.begin(), second.begin():
                acquired_first = await first.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": "integration-market-sync"},
                )
                acquired_second = await second.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": "integration-market-sync"},
                )
                assert acquired_first is True
                assert acquired_second is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_price_is_warning_and_invalid_csv_batch_rolls_back() -> None:
    engine, factory = await _factory()
    try:
        async with factory() as session:
            async with session.begin():
                user, portfolio = await _identity(session, base_currency="USD")
                security = await SecurityRepository(session).get_or_create(
                    canonical_symbol="MSFT",
                    exchange="NASDAQ",
                    market="US",
                    currency="USD",
                )
                await TransactionLedgerService(session).add_transaction(
                    _transaction(
                        portfolio.id,
                        transaction_type="buy",
                        occurred_at=AS_OF - timedelta(days=1),
                        currency="USD",
                        gross="100",
                        security_id=security.id,
                        quantity="1",
                        price="100",
                        external_id="missing-price-buy",
                    )
                )
                valued = await PortfolioValuationService(session).value(
                    portfolio_id=portfolio.id,
                    as_of=AS_OF,
                )
                assert valued.positions == ()
                assert "missing_price:MSFT" in valued.valuation.warnings

                invalid_csv = b"\n".join(
                    [
                        b"external_id,transaction_type,ticker,exchange,trade_date,settlement_date,quantity,price,fees,taxes,currency,note",
                        b"csv-buy,buy,AAPL,NASDAQ,2026-01-01,,1,100,0,0,USD,buy",
                        b"csv-sell,sell,AAPL,NASDAQ,2026-01-02,,2,100,0,0,USD,oversell",
                    ]
                )
                failed_portfolio = await PortfolioRepository(session).get_or_create(
                    user_id=user.id,
                    name="Invalid Batch",
                    base_currency="USD",
                )
                imported = await TransactionCsvImporter(session).import_bytes(
                    portfolio_id=failed_portfolio.id,
                    filename="invalid.csv",
                    content=invalid_csv,
                )
                stored = await TransactionLedgerService(session).list_transactions(
                    failed_portfolio.id
                )
                assert imported.accepted_rows == 0
                assert imported.rejected_rows == 2
                assert imported.errors[0]["line"] == 0
                assert stored == []

            await session.delete(user)
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committed_ledger_survives_engine_disposal_and_reconnection() -> None:
    engine, factory = await _factory()
    user_id = None
    transaction_id = None
    async with factory() as session:
        async with session.begin():
            user, portfolio = await _identity(session)
            user_id = user.id
            transaction, _ = await TransactionLedgerService(session).add_transaction(
                _transaction(
                    portfolio.id,
                    transaction_type="deposit",
                    occurred_at=AS_OF,
                    currency="CNY",
                    gross="100",
                    external_id="persistent-deposit",
                )
            )
            transaction_id = transaction.id
    await engine.dispose()

    reconnected = create_async_engine(
        _database_url(), connect_args={"server_settings": {"timezone": "UTC"}}
    )
    new_factory = async_sessionmaker(reconnected, expire_on_commit=False)
    try:
        async with new_factory() as session:
            assert await session.get(Transaction, transaction_id) is not None
            user = await session.get(User, user_id)
            assert user is not None
            await session.delete(user)
            await session.commit()
    finally:
        await reconnected.dispose()
