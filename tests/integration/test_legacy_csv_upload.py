"""PostgreSQL regression coverage for the legacy dashboard CSV endpoint."""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal
from app.core.principal import get_principal
from app.db.models import (
    ImportBatch,
    LegacySnapshotGeneration,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    PriceBar,
    Security,
    Transaction,
    User,
)
from app.db.repositories import (
    PortfolioMembershipRepository,
    PortfolioRepository,
    TransactionRepository,
    UserRepository,
)
from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter
from app.services.legacy_csv_import import LegacyCsvPortfolioImportService
from app.services.security_master import SecurityMasterService
from app.services.transaction_ledger import TransactionLedgerService
from config import settings
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
    return await _api_request(
        factory=factory,
        principal=principal,
        method="POST",
        path="/api/portfolio/upload-csv",
        json=payload,
    )


async def _api_request(
    *,
    factory: async_sessionmaker[AsyncSession],
    principal: Principal,
    method: str,
    path: str,
    json: dict[str, object] | None = None,
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
            return await client.request(method, path, json=json)
    finally:
        main.app.dependency_overrides.clear()


async def _get_legacy_portfolio(
    *,
    factory: async_sessionmaker[AsyncSession],
    principal: Principal,
    portfolio_id: uuid.UUID,
) -> httpx.Response:
    return await _api_request(
        factory=factory,
        principal=principal,
        method="GET",
        path=f"/api/portfolio?portfolio_id={portfolio_id}",
    )


def _position(
    ticker: str,
    shares: str,
    *,
    buy_price: str = "100",
    current_price: str = "125",
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "shares": shares,
        "buy_price": buy_price,
        "current_price": current_price,
        "buy_date": "2026-08-20",
        "currency": "USD",
        "sector": "Technology",
        "name": f"{ticker} Fixture",
        "asset_type": "equity",
        "market": "US",
        "exchange": "NASDAQ",
        "country": "US",
    }


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    security_ids: set[uuid.UUID] | None = None,
) -> None:
    async with factory.begin() as session:
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)
        for security_id in security_ids or set():
            security = await session.get(Security, security_id)
            if security is not None:
                await session.delete(security)


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
            assert price.source.startswith("legacy_csv_user_supplied:")
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

        replay = await _post_legacy_upload(
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
        assert replay.status_code == 422
        replay_payload = replay.json()
        assert replay_payload["status"] == "failed"
        assert replay_payload["import_status"] == "failed"
        assert replay_payload["idempotent_replay"] is True
        assert replay_payload["accepted_rows"] == 0
        assert replay_payload["duplicate_rows"] == 0
        assert replay_payload["persisted_rows"] == 0
        assert replay_payload["position_rebuild"]["status"] == "not_run"
        assert replay_payload["valuation_snapshot"]["status"] == "not_created"
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
                    .select_from(LegacySnapshotGeneration)
                    .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                )
                == 0
            )
            failed_batch = await session.scalar(
                select(ImportBatch).where(ImportBatch.portfolio_id == portfolio_id)
            )
            assert failed_batch is not None
            assert failed_batch.status == "failed"
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


@pytest.mark.asyncio
async def test_snapshot_replacement_replay_and_formal_transactions_are_isolated() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    ticker = f"REP{suffix[:8].upper()}"
    security_id = None
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id
            security = await SecurityMasterService(setup).resolve_or_create(
                ticker=ticker,
                exchange="NASDAQ",
                currency="USD",
                market="US",
                country="US",
                name=f"{ticker} Fixture",
                asset_type="equity",
                sector="Technology",
            )
            security_id = security.id
            _, created = await TransactionRepository(setup).add_idempotent(
                Transaction(
                    portfolio_id=portfolio.id,
                    security_id=security.id,
                    transaction_type="opening_balance",
                    occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
                    quantity=Decimal("3"),
                    price=Decimal("100"),
                    gross_amount=Decimal("300"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="USD",
                    source="standard_csv",
                    external_id=f"formal-{suffix}",
                    source_record_hash=None,
                    note="Formal transaction that must not be superseded",
                    raw_payload={},
                )
            )
            assert created is True

        first = await _post_legacy_upload(
            factory=factory,
            principal=principal,
            payload={
                "portfolio_id": str(portfolio_id),
                "positions": [_position(ticker, "10")],
            },
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["accepted_rows"] == first_payload["persisted_rows"] == 1
        assert first_payload["duplicate_rows"] == first_payload["rejected_rows"] == 0

        second = await _post_legacy_upload(
            factory=factory,
            principal=principal,
            payload={
                "portfolio_id": str(portfolio_id),
                "positions": [_position(ticker, "12")],
            },
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["accepted_rows"] == second_payload["persisted_rows"] == 1
        assert second_payload["snapshot_generation"]["generation_number"] == 2

        async with factory() as session:
            rebuilt = await TransactionLedgerService(session).rebuild(
                portfolio_id,
                as_of=datetime.now(UTC),
            )
            assert len(rebuilt.positions) == 1
            assert rebuilt.positions[0].quantity == Decimal("15")
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.portfolio_id == portfolio_id)
                )
                == 3
            )
            generations = list(
                await session.scalars(
                    select(LegacySnapshotGeneration)
                    .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                    .order_by(LegacySnapshotGeneration.generation_number)
                )
            )
            assert [item.status for item in generations] == ["superseded", "active"]
            assert generations[0].superseded_by_id == generations[1].id
            valuation = await session.scalar(
                select(PortfolioValuationSnapshot)
                .where(PortfolioValuationSnapshot.portfolio_id == portfolio_id)
                .order_by(PortfolioValuationSnapshot.as_of.desc())
                .limit(1)
            )
            assert valuation is not None
            assert (
                valuation.config_snapshot["data_source_context"][
                    "legacy_snapshot_generation_id"
                ]
                == str(generations[1].id)
            )
            snapshot_position = await session.scalar(
                select(PositionSnapshot).where(
                    PositionSnapshot.valuation_snapshot_id == valuation.id
                )
            )
            assert snapshot_position is not None
            assert snapshot_position.snapshot_data["price_source"].endswith(
                str(generations[1].import_batch_id)
            )

        replay = await _post_legacy_upload(
            factory=factory,
            principal=principal,
            payload={
                "portfolio_id": str(portfolio_id),
                "positions": [_position(ticker, "12")],
            },
        )
        assert replay.status_code == 200
        replay_payload = replay.json()
        assert replay_payload["idempotent_replay"] is True
        assert replay_payload["accepted_rows"] == replay_payload["persisted_rows"] == 0
        assert replay_payload["duplicate_rows"] == 1
        assert replay_payload["snapshot_generation"]["created"] is False
        assert replay_payload["valuation_snapshot"]["status"] == "unchanged"
        assert (
            replay_payload["accepted_rows"]
            + replay_payload["duplicate_rows"]
            + replay_payload["rejected_rows"]
            == replay_payload["total_rows"]
        )

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.portfolio_id == portfolio_id)
                )
                == 3
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(
                        Transaction.portfolio_id == portfolio_id,
                        Transaction.source == "standard_csv",
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(LegacySnapshotGeneration)
                    .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                )
                == 2
            )
    finally:
        if "user_id" in locals():
            await _cleanup(
                factory,
                user_id=user_id,
                security_ids={security_id} if security_id else set(),
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_frontend_post_put_delete_are_postgres_backed_and_restart_safe() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    ticker = f"UI{suffix[:8].upper()}"
    security_ids: set[uuid.UUID] = set()
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id

        created = await _api_request(
            factory=factory,
            principal=principal,
            method="POST",
            path="/api/portfolio/csv-positions",
            json={
                "portfolio_id": str(portfolio_id),
                "position": _position(ticker, "2"),
            },
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["action"] == "created"
        assert created_payload["persisted_rows"] == 1
        assert "csv_path" not in created_payload
        assert "write_csv_path" not in created_payload

        portfolio_response = await _get_legacy_portfolio(
            factory=factory,
            principal=principal,
            portfolio_id=portfolio_id,
        )
        assert portfolio_response.status_code == 200
        assert portfolio_response.json()["stocks"][0]["position"]["shares"] == 2

        updated = await _api_request(
            factory=factory,
            principal=principal,
            method="PUT",
            path=(
                f"/api/portfolio/csv-positions/{ticker}"
                f"?portfolio_id={portfolio_id}"
            ),
            json={"position": _position(ticker, "4", current_price="130")},
        )
        assert updated.status_code == 200
        updated_payload = updated.json()
        assert updated_payload["action"] == "updated"
        assert updated_payload["accepted_rows"] == updated_payload["persisted_rows"] == 1
        assert updated_payload["duplicate_rows"] == updated_payload["rejected_rows"] == 0
        managed = await _api_request(
            factory=factory,
            principal=principal,
            method="GET",
            path=f"/api/portfolio/csv-positions?portfolio_id={portfolio_id}",
        )
        assert managed.status_code == 200
        managed_payload = managed.json()
        assert managed_payload["positions"][0]["shares"] == 4
        assert "csv_path" not in managed_payload
        assert "write_csv_path" not in managed_payload

        async with factory() as session:
            security_ids = set(
                await session.scalars(
                    select(Transaction.security_id).where(
                        Transaction.portfolio_id == portfolio_id,
                        Transaction.security_id.is_not(None),
                    )
                )
            )
        await engine.dispose()

        restarted_engine, restarted_factory = _factory()
        try:
            after_restart = await _get_legacy_portfolio(
                factory=restarted_factory,
                principal=principal,
                portfolio_id=portfolio_id,
            )
            assert after_restart.status_code == 200
            assert after_restart.json()["stocks"][0]["position"]["shares"] == 4

            deleted = await _api_request(
                factory=restarted_factory,
                principal=principal,
                method="DELETE",
                path=(
                    f"/api/portfolio/csv-positions/{ticker}"
                    f"?portfolio_id={portfolio_id}"
                ),
            )
            assert deleted.status_code == 200
            deleted_payload = deleted.json()
            assert deleted_payload["action"] == "deleted"
            assert deleted_payload["positions"] == 0
            assert deleted_payload["accepted_rows"] == 0
            assert deleted_payload["duplicate_rows"] == 0
            assert deleted_payload["rejected_rows"] == 0
            assert deleted_payload["persisted_rows"] == 0
        finally:
            await restarted_engine.dispose()

        final_engine, final_factory = _factory()
        try:
            after_delete_restart = await _get_legacy_portfolio(
                factory=final_factory,
                principal=principal,
                portfolio_id=portfolio_id,
            )
            assert after_delete_restart.status_code == 200
            assert after_delete_restart.json()["stocks"] == []
            async with final_factory() as session:
                active_count = await session.scalar(
                    select(func.count())
                    .select_from(LegacySnapshotGeneration)
                    .where(
                        LegacySnapshotGeneration.portfolio_id == portfolio_id,
                        LegacySnapshotGeneration.status == "active",
                    )
                )
                assert active_count == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(LegacySnapshotGeneration)
                        .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                    )
                    == 3
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Transaction)
                        .where(Transaction.portfolio_id == portfolio_id)
                    )
                    == 2
                )
        finally:
            await _cleanup(
                final_factory,
                user_id=user_id,
                security_ids=security_ids,
            )
            await final_engine.dispose()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_snapshot_mutations_require_auth_write_access_and_writable_mode(
    monkeypatch,
) -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    ticker = f"AUTH{suffix[:6].upper()}"
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id
            readonly_principal = Principal(
                user_id=f"readonly-{suffix}",
                authenticated=True,
                tenant_id=portfolio.tenant_id,
                roles=frozenset({"operator"}),
            )
            await PortfolioMembershipRepository(setup).grant(
                portfolio_id=portfolio.id,
                user_id=readonly_principal.user_id,
                role="viewer",
                can_read=True,
                can_write=False,
                can_admin=False,
            )

        request_json = {
            "portfolio_id": str(portfolio_id),
            "position": _position(ticker, "1"),
        }
        unauthenticated = Principal(
            user_id=principal.user_id,
            authenticated=False,
            tenant_id=principal.tenant_id,
            roles=frozenset({"operator"}),
        )
        unauthenticated_response = await _api_request(
            factory=factory,
            principal=unauthenticated,
            method="POST",
            path="/api/portfolio/csv-positions",
            json=request_json,
        )
        assert unauthenticated_response.status_code == 401

        no_write = await _api_request(
            factory=factory,
            principal=readonly_principal,
            method="POST",
            path="/api/portfolio/csv-positions",
            json=request_json,
        )
        assert no_write.status_code == 422

        monkeypatch.setattr(settings, "READ_ONLY_DEMO", True)
        read_only = await _api_request(
            factory=factory,
            principal=principal,
            method="POST",
            path="/api/portfolio/csv-positions",
            json=request_json,
        )
        assert read_only.status_code == 403

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
                    .select_from(LegacySnapshotGeneration)
                    .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                )
                == 0
            )
    finally:
        if "user_id" in locals():
            await _cleanup(factory, user_id=user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_snapshot_retries_keep_exactly_one_active_generation() -> None:
    engine, factory = _factory()
    suffix = uuid.uuid4().hex
    ticker = f"CON{suffix[:8].upper()}"
    security_ids: set[uuid.UUID] = set()
    try:
        async with factory.begin() as setup:
            user, portfolio, principal = await _portfolio_with_operator(setup, suffix)
            user_id = user.id
            portfolio_id = portfolio.id

        gate = asyncio.Event()

        async def replace(shares: str):
            async with factory() as session:
                await gate.wait()
                result = await LegacyCsvPortfolioImportService(session).replace_positions(
                    positions=[_position(ticker, shares)],
                    principal=principal,
                    portfolio_id=portfolio_id,
                )
                await session.commit()
                return result

        identical_tasks = [
            asyncio.create_task(replace("10")),
            asyncio.create_task(replace("10")),
        ]
        gate.set()
        identical = await asyncio.gather(*identical_tasks)
        assert sorted(item.imported.persisted_rows for item in identical) == [0, 1]
        assert sorted(item.imported.duplicate_rows for item in identical) == [0, 1]

        gate = asyncio.Event()
        different_tasks = [
            asyncio.create_task(replace("11")),
            asyncio.create_task(replace("12")),
        ]
        gate.set()
        different = await asyncio.gather(*different_tasks)
        assert [item.imported.persisted_rows for item in different] == [1, 1]

        async with factory() as session:
            active_count = await session.scalar(
                select(func.count())
                .select_from(LegacySnapshotGeneration)
                .where(
                    LegacySnapshotGeneration.portfolio_id == portfolio_id,
                    LegacySnapshotGeneration.status == "active",
                )
            )
            total_generations = await session.scalar(
                select(func.count())
                .select_from(LegacySnapshotGeneration)
                .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
            )
            assert active_count == 1
            assert total_generations == 3
            rebuilt = await TransactionLedgerService(session).rebuild(
                portfolio_id,
                as_of=datetime.now(UTC),
            )
            assert len(rebuilt.positions) == 1
            assert rebuilt.positions[0].quantity in {Decimal("11"), Decimal("12")}
            security_ids = {
                item
                for item in await session.scalars(
                    select(Transaction.security_id).where(
                        Transaction.portfolio_id == portfolio_id,
                        Transaction.security_id.is_not(None),
                    )
                )
                if item is not None
            }
    finally:
        if "user_id" in locals():
            await _cleanup(
                factory,
                user_id=user_id,
                security_ids=security_ids,
            )
        await engine.dispose()
