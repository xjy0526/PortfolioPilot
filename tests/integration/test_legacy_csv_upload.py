"""PostgreSQL regression coverage for the legacy dashboard CSV endpoint."""

from __future__ import annotations

import copy
import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal
from app.core.principal import get_principal
from app.db.models import PortfolioValuationSnapshot, PriceBar, Security, Transaction, User
from app.db.repositories import (
    PortfolioMembershipRepository,
    PortfolioRepository,
    UserRepository,
)
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from state import portfolio_data

pytestmark = pytest.mark.postgres


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def _factory():
    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def _portfolio_with_operator(session: AsyncSession, suffix: str):
    user = await UserRepository(session).get_or_create(
        email=f"legacy-upload-{suffix}@example.invalid"
    )
    portfolio = await PortfolioRepository(session).get_or_create(
        user_id=user.id,
        name=f"Legacy Upload {suffix}",
        base_currency="USD",
    )
    portfolio.tenant_id = f"tenant-{suffix}"
    principal = Principal(
        user_id=f"operator-{suffix}",
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id=portfolio.tenant_id,
        roles=frozenset({"operator"}),
    )
    await PortfolioMembershipRepository(session).grant(
        portfolio_id=portfolio.id,
        user_id=principal.user_id,
        role="operator",
        can_read=True,
        can_write=True,
        can_admin=False,
    )
    await session.flush()
    return user, portfolio, principal


async def _post_legacy_upload(
    *,
    factory: async_sessionmaker[AsyncSession],
    principal: Principal,
    payload: dict[str, object],
) -> httpx.Response:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise
            else:
                if session.in_transaction():
                    await session.commit()

    async def principal_override() -> Principal:
        return principal

    main.app.dependency_overrides[get_db_session] = session_override
    main.app.dependency_overrides[get_principal] = principal_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            return await client.post("/api/portfolio/upload-csv", json=payload)
    finally:
        main.app.dependency_overrides.clear()


async def _get_legacy_portfolio(
    *,
    factory: async_sessionmaker[AsyncSession],
    principal: Principal,
    portfolio_id: uuid.UUID,
) -> httpx.Response:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise
            else:
                if session.in_transaction():
                    await session.commit()

    async def principal_override() -> Principal:
        return principal

    main.app.dependency_overrides[get_db_session] = session_override
    main.app.dependency_overrides[get_principal] = principal_override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            return await client.get(f"/api/portfolio?portfolio_id={portfolio_id}")
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_legacy_dashboard_upload_persists_rebuilds_values_and_survives_restart() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    ticker = f"WEB{suffix[:8].upper()}"
    user_id = None
    portfolio_id = None
    security_id = None
    principal = None
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id

        response = await _post_legacy_upload(
            factory=factory,
            principal=principal,
            payload={
                "portfolio_id": str(portfolio_id),
                "positions": [
                    {
                        "ticker": ticker,
                        "shares": "2",
                        "buy_price": "100",
                        "current_price": "125",
                        "buy_date": "2026-08-20",
                        "currency": "USD",
                        "sector": "Technology",
                        "name": "Web Import Fixture",
                        "asset_type": "equity",
                        "market": "US",
                        "exchange": "NASDAQ",
                        "country": "US",
                    }
                ],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["import_status"] == "completed"
        assert payload["portfolio_id"] == str(portfolio_id)
        assert payload["positions_imported"] == 1
        assert payload["accepted_rows"] == 1
        assert payload["persisted_rows"] == 1
        assert payload["duplicate_rows"] == 0
        assert payload["rejected_rows"] == 0
        assert payload["position_rebuild"]["status"] == "completed"
        assert payload["position_rebuild"]["position_count"] == 1
        assert payload["valuation_snapshot"]["status"] == "created"
        assert payload["valuation_snapshot"]["valuation_status"] == "complete"

        async with factory() as session:
            transaction = await session.scalar(
                select(Transaction).where(Transaction.portfolio_id == portfolio_id)
            )
            assert transaction is not None
            assert transaction.source == "legacy_dashboard_csv"
            security_id = transaction.security_id
            assert security_id is not None
            price = await session.scalar(
                select(PriceBar).where(PriceBar.security_id == security_id)
            )
            assert price is not None
            assert price.source == "legacy_csv_user_supplied"
            assert price.raw_payload["user_supplied"] is True
            assert price.raw_payload["fallback"] is False

        await engine.dispose()
        restarted_engine, restarted_factory = _factory()
        try:
            async with restarted_factory() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Transaction)
                        .where(Transaction.portfolio_id == portfolio_id)
                    )
                    == 1
                )
                valuation = await session.scalar(
                    select(PortfolioValuationSnapshot).where(
                        PortfolioValuationSnapshot.portfolio_id == portfolio_id
                    )
                )
                assert valuation is not None
                assert valuation.total_market_value is not None
                assert (
                    valuation.config_snapshot["data_source_context"]["transaction_source"]
                    == "legacy_dashboard_csv"
                )
                context = await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio_id,
                    principal=principal,
                )
                assert context is not None
                assert context.summary.stocks[0].position.ticker == ticker
                dashboard_response = await _get_legacy_portfolio(
                    factory=restarted_factory,
                    principal=principal,
                    portfolio_id=portfolio_id,
                )
                assert dashboard_response.status_code == 200
                dashboard = dashboard_response.json()
                assert dashboard["stocks"][0]["position"]["ticker"] == ticker
        finally:
            async with restarted_factory.begin() as cleanup:
                user = await cleanup.get(User, user_id)
                if user is not None:
                    await cleanup.delete(user)
                security = await cleanup.get(Security, security_id)
                if security is not None:
                    await cleanup.delete(security)
            await restarted_engine.dispose()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_legacy_dashboard_upload_leaves_database_and_memory_unchanged() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    before_state = copy.deepcopy(portfolio_data)
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id

        response = await _post_legacy_upload(
            factory=factory,
            principal=principal,
            payload={
                "portfolio_id": str(portfolio_id),
                "positions": [
                    {
                        "ticker": f"BAD{suffix[:8].upper()}",
                        "shares": "-1",
                        "buy_price": "100",
                        "buy_date": "2026-08-20",
                        "currency": "USD",
                        "market": "US",
                        "exchange": "NASDAQ",
                    }
                ],
            },
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["import_status"] == "failed"
        assert payload["accepted_rows"] == 0
        assert payload["persisted_rows"] == 0
        assert payload["rejected_rows"] == 1
        assert payload["position_rebuild"]["status"] == "not_run"
        assert payload["valuation_snapshot"]["status"] == "not_created"
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.portfolio_id == portfolio_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PortfolioValuationSnapshot)
                    .where(PortfolioValuationSnapshot.portfolio_id == portfolio_id)
                )
                == 0
            )
        assert portfolio_data == before_state

        async with factory.begin() as cleanup:
            user = await cleanup.get(User, user_id)
            assert user is not None
            await cleanup.delete(user)
    finally:
        await engine.dispose()
