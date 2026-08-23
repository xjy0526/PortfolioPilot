"""PostgreSQL semantics for effective activity and complete audit history."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.db.models import Security, Transaction, User
from app.db.repositories import (
    PortfolioMembershipRepository,
    PortfolioRepository,
    UserRepository,
)
from app.services.legacy_csv_import import LegacyCsvPortfolioImportService
from app.services.security_master import SecurityMasterService
from app.services.transaction_ledger import TransactionLedgerService

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


async def _api_request(
    *,
    factory: async_sessionmaker[AsyncSession],
    principal: Principal,
    path: str,
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
            return await client.get(path)
    finally:
        main.app.dependency_overrides.clear()


def _principal(
    suffix: str,
    name: str,
    *,
    tenant_id: str,
    role: str,
) -> Principal:
    return Principal(
        user_id=f"{name}-{suffix}",
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id=tenant_id,
        roles=frozenset({role}),
    )


def _legacy_position(ticker: str, quantity: str, trade_date: str) -> dict[str, object]:
    return {
        "ticker": ticker,
        "shares": quantity,
        "buy_price": "100",
        "current_price": "125",
        "buy_date": trade_date,
        "currency": "USD",
        "sector": "Technology",
        "name": f"{ticker} Effective Activity Fixture",
        "asset_type": "equity",
        "market": "US",
        "exchange": "NASDAQ",
        "country": "US",
    }


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    security_id: uuid.UUID,
) -> None:
    async with factory.begin() as session:
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)
        await session.flush()
        security = await session.get(Security, security_id)
        if security is not None:
            await session.delete(security)


@pytest.mark.asyncio
async def test_effective_activity_and_admin_audit_survive_database_reconnect() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    tenant_id = f"tenant-effective-{suffix}"
    ticker = f"EA{suffix[:8].upper()}"
    viewer = _principal(suffix, "viewer", tenant_id=tenant_id, role="viewer")
    operator = _principal(suffix, "operator", tenant_id=tenant_id, role="operator")
    portfolio_admin = _principal(
        suffix,
        "portfolio-admin",
        tenant_id=tenant_id,
        role="admin",
    )
    platform_admin = _principal(
        suffix,
        "platform-admin",
        tenant_id="platform-operations",
        role="platform_admin",
    )
    other_tenant_admin = _principal(
        suffix,
        "other-admin",
        tenant_id=f"other-{tenant_id}",
        role="admin",
    )
    user_id: uuid.UUID | None = None
    portfolio_id: uuid.UUID | None = None
    security_id: uuid.UUID | None = None
    try:
        async with factory.begin() as session:
            user = await UserRepository(session).get_or_create(
                email=f"effective-activities-{suffix}@example.invalid"
            )
            portfolio = await PortfolioRepository(session).get_or_create(
                user_id=user.id,
                name=f"Effective Activities {suffix}",
                base_currency="USD",
            )
            portfolio.tenant_id = tenant_id
            security = await SecurityMasterService(session).resolve_or_create(
                ticker=ticker,
                exchange="NASDAQ",
                currency="USD",
                market="US",
                country="US",
                name=f"{ticker} Activity Fixture",
                sector="Technology",
            )
            user_id = user.id
            portfolio_id = portfolio.id
            security_id = security.id
            memberships = PortfolioMembershipRepository(session)
            await memberships.grant(
                portfolio_id=portfolio.id,
                user_id=viewer.user_id,
                role="viewer",
                can_read=True,
                can_write=False,
                can_admin=False,
            )
            await memberships.grant(
                portfolio_id=portfolio.id,
                user_id=operator.user_id,
                role="operator",
                can_read=True,
                can_write=True,
                can_admin=False,
            )
            await memberships.grant(
                portfolio_id=portfolio.id,
                user_id=portfolio_admin.user_id,
                role="admin",
                can_read=True,
                can_write=True,
                can_admin=True,
            )
            await TransactionLedgerService(session).add_transaction(
                Transaction(
                    portfolio_id=portfolio.id,
                    security_id=security.id,
                    transaction_type="opening_balance",
                    occurred_at=datetime(2026, 8, 19, 8, tzinfo=UTC),
                    quantity=Decimal("3"),
                    price=Decimal("100"),
                    gross_amount=Decimal("300"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="USD",
                    source="standard_csv",
                    external_id=f"formal-{suffix}",
                    note="Formal opening balance",
                )
            )

        async with factory.begin() as session:
            service = LegacyCsvPortfolioImportService(session)
            generation_one = await service.replace_positions(
                positions=[_legacy_position(ticker, "10", "2026-08-20")],
                principal=operator,
                portfolio_id=portfolio_id,
                as_of=datetime(2026, 8, 20, 12, tzinfo=UTC),
            )
            generation_two = await service.replace_positions(
                positions=[_legacy_position(ticker, "12", "2026-08-21")],
                principal=operator,
                portfolio_id=portfolio_id,
                as_of=datetime(2026, 8, 21, 12, tzinfo=UTC),
            )
            generation_one_id = generation_one.snapshot_generation["id"]
            generation_two_id = generation_two.snapshot_generation["id"]

        activities_path = f"/api/portfolio/activities?portfolio_id={portfolio_id}"
        activity_response = await _api_request(
            factory=factory,
            principal=viewer,
            path=activities_path,
        )
        assert activity_response.status_code == 200
        activities = activity_response.json()
        assert [(row["source"], row["quantity"]) for row in activities] == [
            ("standard_csv", 3.0),
            ("legacy_dashboard_csv", 12.0),
        ]
        assert all(row["effective"] is True for row in activities)
        assert all("generation_id" not in row for row in activities)
        assert all("generation_status" not in row for row in activities)
        assert all("import_batch_id" not in row for row in activities)

        repeated_activity = await _api_request(
            factory=factory,
            principal=viewer,
            path=activities_path,
        )
        assert repeated_activity.status_code == 200
        assert [row["id"] for row in repeated_activity.json()] == [
            row["id"] for row in activities
        ]

        transactions_response = await _api_request(
            factory=factory,
            principal=viewer,
            path=f"/api/portfolios/{portfolio_id}/transactions",
        )
        assert transactions_response.status_code == 200
        assert [row["quantity"] for row in transactions_response.json()] == [
            "3.000000000000",
            "12.000000000000",
        ]

        audit_path = f"/api/portfolios/{portfolio_id}/transactions/audit"
        for denied_principal in (viewer, operator, other_tenant_admin):
            denied = await _api_request(
                factory=factory,
                principal=denied_principal,
                path=audit_path,
            )
            assert denied.status_code == 404

        hidden_activity = await _api_request(
            factory=factory,
            principal=other_tenant_admin,
            path=activities_path,
        )
        assert hidden_activity.status_code == 200
        assert hidden_activity.json() == []

        portfolio_admin_audit = await _api_request(
            factory=factory,
            principal=portfolio_admin,
            path=audit_path,
        )
        assert portfolio_admin_audit.status_code == 200
        assert portfolio_admin_audit.json()["scope"] == "audit_transaction_history"

        platform_audit = await _api_request(
            factory=factory,
            principal=platform_admin,
            path=audit_path,
        )
        assert platform_audit.status_code == 200
        audit_rows = platform_audit.json()["transactions"]
        assert [(row["source"], row["quantity"]) for row in audit_rows] == [
            ("standard_csv", "3.000000000000"),
            ("legacy_dashboard_csv", "10.000000000000"),
            ("legacy_dashboard_csv", "12.000000000000"),
        ]

        formal, superseded, active = audit_rows
        assert formal["effective"] is True
        assert formal["generation_id"] is None
        assert formal["generation_status"] is None
        assert superseded["effective"] is False
        assert superseded["generation_id"] == generation_one_id
        assert superseded["generation_number"] == 1
        assert superseded["generation_status"] == "superseded"
        assert superseded["superseded_at"] is not None
        assert superseded["superseded_by_id"] == generation_two_id
        assert active["effective"] is True
        assert active["generation_id"] == generation_two_id
        assert active["generation_number"] == 2
        assert active["generation_status"] == "active"
        assert active["superseded_at"] is None
        assert active["superseded_by_id"] is None

        before_restart_activity_ids = [row["id"] for row in activities]
        before_restart_audit_ids = [row["id"] for row in audit_rows]
        await engine.dispose()

        engine, factory = _factory()
        restarted_activity = await _api_request(
            factory=factory,
            principal=viewer,
            path=activities_path,
        )
        restarted_audit = await _api_request(
            factory=factory,
            principal=portfolio_admin,
            path=audit_path,
        )
        assert restarted_activity.status_code == 200
        assert restarted_audit.status_code == 200
        assert [row["id"] for row in restarted_activity.json()] == before_restart_activity_ids
        assert [
            row["id"] for row in restarted_audit.json()["transactions"]
        ] == before_restart_audit_ids
    finally:
        if user_id is not None and security_id is not None:
            await _cleanup(factory, user_id=user_id, security_id=security_id)
        await engine.dispose()
