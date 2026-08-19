"""PostgreSQL ledger, import, FX, valuation, and compatibility integration tests."""
from __future__ import annotations

import os
import uuid
from io import BytesIO
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.portfolios import import_transactions
from app.core.principal import Principal, require_portfolio_access
from app.db.models import FxRate, Portfolio, PriceBar, Security, Transaction, User
from app.db.repositories import (
    PortfolioMembershipRepository,
    PortfolioRepository,
    SecurityRepository,
    UserRepository,
)
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
    run_suffix = uuid.uuid4().hex
    price_source = f"yfinance_research_{run_suffix}"
    fx_source = f"integration_test_{run_suffix}"
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
                            source=price_source,
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
                            source=price_source,
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
                            rate_date=(AS_OF - timedelta(days=4)).date(),
                            source=fx_source,
                            rate=Decimal("6.5"),
                            data_as_of=AS_OF - timedelta(days=3),
                            raw_payload={"purpose": "trade_date_cost_basis"},
                        ),
                        FxRate(
                            base_currency="USD",
                            quote_currency="CNY",
                            rate_date=(AS_OF - timedelta(days=2)).date(),
                            source=fx_source,
                            rate=Decimal("7"),
                            data_as_of=AS_OF - timedelta(days=1),
                            raw_payload={},
                        ),
                        FxRate(
                            base_currency="USD",
                            quote_currency="CNY",
                            rate_date=(AS_OF + timedelta(days=1)).date(),
                            source=fx_source,
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
                assert position.cost_basis_native == Decimal("1000")
                assert position.cost_basis_base_at_trade == Decimal("6500")
                assert position.local_price_pnl == Decimal("1400")
                assert position.fx_pnl == Decimal("500")
                assert position.total_pnl_base == Decimal("1900")
                assert position.price_bar_id is not None
                assert position.fx_rate_id is not None
                assert first.valuation.valuation_status == "complete"
                assert first.valuation.coverage_ratio == Decimal("1")
                assert first.valuation.max_staleness_days == 2

                legacy = await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio.id, as_of=AS_OF
                )
                assert legacy is not None
                assert legacy.summary.total_value == 15400.0
                assert legacy.summary.stocks[0].position.ticker == "AAPL"

            await session.delete(user)
            await session.flush()
            await session.execute(delete(PriceBar).where(PriceBar.source == price_source))
            await session.execute(delete(FxRate).where(FxRate.source == fx_source))
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_distinct_files_with_duplicate_external_id_do_not_duplicate_transaction() -> None:
    engine, factory = await _factory()
    try:
        async with factory() as session:
            async with session.begin():
                user, portfolio = await _identity(session, base_currency="USD")
                header = (
                    b"external_id,transaction_type,ticker,exchange,trade_date,settlement_date,"
                    b"quantity,price,fees,taxes,currency,note"
                )
                first_content = b"\n".join(
                    [
                        header,
                        b"broker-001,buy,AAPL,NASDAQ,2026-01-02,,1,100,0,0,USD,first export",
                    ]
                )
                replay_content = b"\n".join(
                    [
                        header,
                        b"broker-001,buy,AAPL,NASDAQ,2026-01-02,,1,100,0,0,USD,second export",
                    ]
                )
                importer = TransactionCsvImporter(session)

                first = await importer.import_bytes(
                    portfolio_id=portfolio.id,
                    filename="broker-first.csv",
                    content=first_content,
                )
                duplicate_record = await importer.import_bytes(
                    portfolio_id=portfolio.id,
                    filename="broker-second.csv",
                    content=replay_content,
                )
                rows = await TransactionLedgerService(session).list_transactions(portfolio.id)

                assert first.inserted_rows == 1
                assert duplicate_record.idempotent_replay is False
                assert duplicate_record.accepted_rows == 1
                assert duplicate_record.inserted_rows == 0
                assert len(rows) == 1
                assert rows[0].external_id == "broker-001"

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
                assert replay.status == "duplicate"
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
                assert valued.valuation.valuation_status == "partial"
                assert valued.valuation.total_market_value is None
                assert valued.valuation.priced_market_value == Decimal("-100")

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
                assert imported.status == "failed"
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


@pytest.mark.asyncio
async def test_portfolio_memberships_prevent_cross_portfolio_reads_and_imports() -> None:
    engine, factory = await _factory()
    suffix = uuid.uuid4().hex
    principal_a = Principal(
        f"principal-a-{suffix}",
        frozenset({"public"}),
        authenticated=True,
        tenant_id="tenant-a",
        roles=frozenset({"operator"}),
    )
    platform_admin = Principal(
        f"platform-admin-{suffix}",
        frozenset({"public"}),
        authenticated=True,
        tenant_id="another-tenant",
        roles=frozenset({"platform_admin"}),
    )
    try:
        async with factory() as session:
            async with session.begin():
                user_a = await UserRepository(session).get_or_create(
                    email=f"membership-a-{suffix}@example.invalid"
                )
                user_b = await UserRepository(session).get_or_create(
                    email=f"membership-b-{suffix}@example.invalid"
                )
                portfolio_a = await PortfolioRepository(session).get_or_create(
                    user_id=user_a.id,
                    name="Membership A",
                    base_currency="CNY",
                )
                portfolio_b = await PortfolioRepository(session).get_or_create(
                    user_id=user_b.id,
                    name="Membership B",
                    base_currency="USD",
                )
                portfolio_a.tenant_id = "tenant-a"
                portfolio_b.tenant_id = "tenant-a"
                await session.flush()
                await PortfolioMembershipRepository(session).grant(
                    portfolio_id=portfolio_a.id,
                    user_id=principal_a.user_id,
                    role="operator",
                    can_read=True,
                    can_write=True,
                    can_admin=False,
                )

                accessible = await PortfolioRepository(session).list_accessible(
                    user_id=principal_a.user_id,
                    tenant_id=principal_a.tenant_id,
                )
                assert [item.id for item in accessible] == [portfolio_a.id]
                assert (
                    await require_portfolio_access(
                        session, principal_a, portfolio_a.id, "read"
                    )
                ).id == portfolio_a.id

                with pytest.raises(HTTPException) as hidden_read:
                    await require_portfolio_access(
                        session, principal_a, portfolio_b.id, "read"
                    )
                assert hidden_read.value.status_code == 404

                upload = UploadFile(
                    file=BytesIO(
                        b"external_id,transaction_type,ticker,exchange,trade_date,"
                        b"settlement_date,quantity,price,fees,taxes,currency,note\n"
                    ),
                    filename="unauthorized.csv",
                )
                with pytest.raises(HTTPException) as hidden_import:
                    await import_transactions(
                        portfolio_b.id,
                        file=upload,
                        source="csv_upload",
                        principal=principal_a,
                        session=session,
                    )
                assert hidden_import.value.status_code == 404

                assert (
                    await require_portfolio_access(
                        session, platform_admin, portfolio_b.id, "read"
                    )
                ).id == portfolio_b.id

            await session.delete(user_a)
            await session.delete(user_b)
            await session.commit()
    finally:
        await engine.dispose()
