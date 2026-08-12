"""Opt-in proof that committed ledger data survives a real PostgreSQL restart."""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Transaction, User
from app.db.repositories import PortfolioRepository, UserRepository
from app.services.transaction_ledger import TransactionLedgerService

pytestmark = [pytest.mark.postgres, pytest.mark.postgres_restart]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if os.getenv("RUN_POSTGRES_RESTART_TEST") != "1":
        pytest.skip("set RUN_POSTGRES_RESTART_TEST=1 to restart PostgreSQL")
    return value


def _factory(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _wait_until_ready(database_url: str) -> None:
    last_error: Exception | None = None
    for _ in range(30):
        engine, factory = _factory(database_url)
        try:
            async with factory() as session:
                await session.get(User, uuid.uuid4())
            await engine.dispose()
            return
        except Exception as exc:  # pragma: no cover - exercised only around a real restart
            last_error = exc
            await engine.dispose()
            await asyncio.sleep(1)
    raise AssertionError("PostgreSQL did not become ready after restart") from last_error


@pytest.mark.asyncio
async def test_committed_transaction_survives_postgres_container_restart() -> None:
    database_url = _database_url()
    engine, factory = _factory(database_url)
    user_id: uuid.UUID
    transaction_id: uuid.UUID
    async with factory() as session:
        async with session.begin():
            user = await UserRepository(session).get_or_create(
                email=f"restart-{uuid.uuid4()}@example.invalid"
            )
            portfolio = await PortfolioRepository(session).get_or_create(
                user_id=user.id,
                name="Restart Persistence",
                base_currency="CNY",
            )
            transaction, _ = await TransactionLedgerService(session).add_transaction(
                Transaction(
                    portfolio_id=portfolio.id,
                    transaction_type="deposit",
                    occurred_at=datetime.now(UTC),
                    quantity=Decimal("0"),
                    gross_amount=Decimal("100"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="CNY",
                    source="postgres_restart_test",
                    external_id=str(uuid.uuid4()),
                    raw_payload={},
                )
            )
            user_id = user.id
            transaction_id = transaction.id
    await engine.dispose()

    subprocess.run(
        ["docker", "compose", "restart", "postgres"],
        cwd=REPOSITORY_ROOT,
        check=True,
        timeout=90,
    )
    await _wait_until_ready(database_url)

    reconnected, factory = _factory(database_url)
    try:
        async with factory() as session:
            assert await session.get(Transaction, transaction_id) is not None
            user = await session.get(User, user_id)
            assert user is not None
            await session.delete(user)
            await session.commit()
    finally:
        await reconnected.dispose()
