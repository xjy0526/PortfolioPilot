"""Integration tests executed against the CI pgvector PostgreSQL service."""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    BacktestRebalanceSnapshot,
    BacktestRun,
    BacktestStrategyResult,
    Base,
    Portfolio,
    Transaction,
    User,
)
from app.db.repositories import (
    PortfolioRepository,
    TransactionRepository,
    UserRepository,
)
from scripts.migrate_sqlite_to_postgres import LEGACY_USER_EMAIL, migrate
from app.services.backtest_persistence import BacktestPersistenceService

pytestmark = pytest.mark.postgres


def _test_database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        _test_database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return engine, factory


@pytest.mark.asyncio
async def test_alembic_created_tables_and_pgvector_extension():
    engine, _ = await _factory()
    try:
        async with engine.connect() as connection:
            table_names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        assert set(Base.metadata.tables).issubset(table_names)
        assert vector_version
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transaction_rollback_is_visible_to_a_new_session():
    engine, factory = await _factory()
    email = f"rollback-{uuid.uuid4()}@example.invalid"
    try:
        async with factory() as session:
            transaction = await session.begin()
            user = await UserRepository(session).add(User(email=email, display_name="Rollback"))
            user_id = user.id
            await transaction.rollback()

        async with factory() as verification_session:
            assert await verification_session.get(User, user_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_external_transaction_repository_is_idempotent_and_uses_decimal():
    engine, factory = await _factory()
    email = f"idempotency-{uuid.uuid4()}@example.invalid"
    try:
        async with factory() as session:
            async with session.begin():
                user = await UserRepository(session).get_or_create(email=email)
                portfolio = await PortfolioRepository(session).get_or_create(
                    user_id=user.id,
                    name="Idempotency Test",
                    base_currency="CNY",
                )
                row = Transaction(
                    portfolio_id=portfolio.id,
                    security_id=None,
                    transaction_type="deposit",
                    occurred_at=datetime.now(UTC),
                    quantity=Decimal("0"),
                    price=None,
                    gross_amount=Decimal("10000.12345678"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="CNY",
                    source="integration_test",
                    external_id="deposit-001",
                )
                first, first_created = await TransactionRepository(session).add_idempotent(row)
                duplicate = Transaction(
                    portfolio_id=portfolio.id,
                    security_id=None,
                    transaction_type="deposit",
                    occurred_at=row.occurred_at,
                    quantity=Decimal("0"),
                    price=None,
                    gross_amount=Decimal("10000.12345678"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="CNY",
                    source="integration_test",
                    external_id="deposit-001",
                )
                second, second_created = await TransactionRepository(session).add_idempotent(duplicate)
                assert first.id == second.id
                assert first_created is True
                assert second_created is False

            stored = await session.scalar(select(Transaction).where(Transaction.id == first.id))
            assert stored is not None
            assert stored.gross_amount == Decimal("10000.12345678")
            assert stored.created_at.utcoffset() == timedelta(0)

            await session.delete(portfolio)
            await session.flush()
            await session.delete(user)
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backtest_report_persistence_is_idempotent_and_keeps_rebalance_lineage():
    engine, factory = await _factory()
    input_hash = uuid.uuid4().hex + uuid.uuid4().hex
    report = {
        "input_hash": input_hash,
        "data_as_of": "2026-06-30T23:59:59+00:00",
        "code_version": "integration-test",
        "config_hash": "c" * 64,
        "price_source": "historical_csv",
        "execution_convention": "t close execution; effective from t+1",
        "cost_assumptions": {"transaction_cost_bps": 5.0},
        "config": {"train_window": 20},
        "status": "completed",
        "mock_price_data_used": False,
        "benchmark": {"name": "SPY"},
        "rebalance_snapshots": [
            {
                "execution_date": "2026-06-01",
                "effective_from": "2026-06-02",
                "eligible_universe": ["AAPL"],
                "excluded_assets": {"MSFT": "insufficient_observations"},
                "input_hash": "s" * 64,
                "strategies": {
                    "equal_weight": {
                        "pre_trade_weights": {"AAPL": 1.0},
                        "target_weights": {"AAPL": 1.0},
                        "executed_weights": {"AAPL": 1.0},
                        "post_return_weights": {"AAPL": 1.0},
                        "turnover": 0.0,
                        "executed_turnover": 0.0,
                        "costs": {"amount": 0.0},
                    }
                },
            }
        ],
        "strategies": [
            {
                "strategy": "equal_weight",
                "annual_return": 0.1,
                "weights": {"AAPL": 1.0},
                "nav_series": [{"date": "2026-06-02", "nav": 1_000_000}],
                "turnover": 0.0,
                "total_costs": 0.0,
            }
        ],
    }
    try:
        async with factory() as session:
            async with session.begin():
                service = BacktestPersistenceService(session)
                first, replayed_first = await service.persist(report)
                second, replayed_second = await service.persist(report)
                assert first.id == second.id
                assert replayed_first is False
                assert replayed_second is True
                assert await session.scalar(
                    select(func.count()).select_from(BacktestRebalanceSnapshot).where(
                        BacktestRebalanceSnapshot.backtest_run_id == first.id
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(BacktestStrategyResult).where(
                        BacktestStrategyResult.backtest_run_id == first.id
                    )
                ) == 1
                await session.delete(first)
    finally:
        await engine.dispose()


def _create_legacy_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE portfolio_snapshots (
                date TEXT PRIMARY KEY, total_value REAL NOT NULL, total_cost REAL NOT NULL,
                total_pnl REAL NOT NULL, num_positions INTEGER NOT NULL,
                eur_usd_rate REAL DEFAULT 1.0, timestamp TEXT NOT NULL
            );
            CREATE TABLE shadow_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                action TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT NOT NULL,
                shares REAL NOT NULL, price_eur REAL NOT NULL, total_eur REAL NOT NULL,
                reason TEXT, score REAL, confidence REAL
            );
            INSERT INTO portfolio_snapshots VALUES
                ('2026-01-02', 12000.25, 10000.00, 2000.25, 2, 1.08,
                 '2026-01-02T08:00:00+00:00');
            INSERT INTO shadow_transactions
                (timestamp, action, ticker, name, shares, price_eur, total_eur, reason)
            VALUES ('2026-01-02T09:00:00+00:00', 'buy', 'AAPL', 'Apple', 2, 180, 360,
                    'legacy simulation');
            """
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_sqlite_migration_script_is_idempotent(tmp_path):
    sqlite_path = tmp_path / "legacy.db"
    _create_legacy_sqlite(sqlite_path)
    url = _test_database_url()

    first = await migrate(sqlite_path=sqlite_path, database_url=url, base_currency="CNY")
    second = await migrate(sqlite_path=sqlite_path, database_url=url, base_currency="CNY")

    assert first["snapshots_created"] == 1
    assert first["transactions_created"] == 1
    assert second["snapshots_existing"] == 1
    assert second["transactions_existing"] == 1

    engine, factory = await _factory()
    try:
        async with factory.begin() as session:
            user = await UserRepository(session).get_by_email(LEGACY_USER_EMAIL)
            assert user is not None
            portfolios = await PortfolioRepository(session).list_for_user(user.id)
            for portfolio in portfolios:
                await session.delete(portfolio)
            await session.flush()
            await session.delete(user)
    finally:
        await engine.dispose()
