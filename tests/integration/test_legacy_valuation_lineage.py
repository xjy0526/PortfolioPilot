"""Upgrade regression for legacy snapshot generation and valuation lineage."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.db.models import (
    ImportBatch,
    LegacySnapshotGeneration,
    Portfolio,
    PortfolioMembership,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    PriceBar,
    Security,
    Transaction,
    User,
)
from app.db.repositories import (
    PortfolioRepository,
    PortfolioValuationRepository,
    TransactionRepository,
)
from app.services.legacy_portfolio_adapter import (
    LegacyPortfolioAdapter,
    PortfolioRebuildRequired,
)
from app.services.legacy_valuation_repair import LegacyValuationRepairService
from app.services.portfolio_valuation import PortfolioValuationService
from scripts.rebuild_stale_legacy_valuations import run_repair

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
REVISION_BEFORE_GENERATIONS = "20260819_0006"
GENERATION_REVISION = "20260821_0007"


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def _application_database_url(url: str, database: str) -> str:
    parsed = make_url(url).set(database=database)
    return parsed.render_as_string(hide_password=False)


def _asyncpg_dsn(url: str, database: str) -> str:
    parsed = make_url(url).set(drivername="postgresql", database=database)
    return parsed.render_as_string(hide_password=False)


async def _create_database(base_url: str, database: str) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(base_url, "postgres"))
    try:
        await connection.execute(f'CREATE DATABASE "{database}"')
    finally:
        await connection.close()


async def _drop_database(base_url: str, database: str) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(base_url, "postgres"))
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await connection.close()


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "TEST_DATABASE_URL": database_url,
            "ENVIRONMENT": "test",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Alembic {' '.join(arguments)} failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _factory(database_url: str):
    engine = create_async_engine(
        database_url,
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


async def _seed_0006_additive_state(
    factory: async_sessionmaker[AsyncSession],
    suffix: str,
    *,
    portfolio_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID, Principal, datetime]:
    first_created = datetime(2026, 8, 18, 10, tzinfo=UTC)
    second_created = first_created + timedelta(days=1)
    old_valuation_as_of = second_created + timedelta(hours=2)
    async with factory.begin() as session:
        user = User(email=f"lineage-upgrade-{suffix}@example.invalid")
        security = Security(
            canonical_symbol=f"LIN{suffix[:8].upper()}",
            name="Legacy Lineage Fixture",
            asset_type="equity",
            market="US",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            country="US",
        )
        session.add_all([user, security])
        await session.flush()
        portfolio = Portfolio(
            user_id=user.id,
            tenant_id=f"tenant-{suffix}",
            name=f"Legacy Lineage {suffix}",
            base_currency="USD",
        )
        if portfolio_id is not None:
            portfolio.id = portfolio_id
        session.add(portfolio)
        await session.flush()
        principal = Principal(
            user_id=f"operator-{suffix}",
            authenticated=True,
            tenant_id=portfolio.tenant_id,
            roles=frozenset({"operator"}),
        )
        session.add(
            PortfolioMembership(
                portfolio_id=portfolio.id,
                user_id=principal.user_id,
                role="operator",
                can_read=True,
                can_write=True,
                can_admin=False,
            )
        )
        first_batch = ImportBatch(
            portfolio_id=portfolio.id,
            source="legacy_dashboard_csv",
            source_filename="legacy-10.csv",
            file_sha256="1" * 64,
            status="completed",
            total_rows=1,
            accepted_rows=1,
            rejected_rows=0,
            completed_at=first_created,
            created_at=first_created,
            updated_at=first_created,
        )
        second_batch = ImportBatch(
            portfolio_id=portfolio.id,
            source="legacy_dashboard_csv",
            source_filename="legacy-12.csv",
            file_sha256="2" * 64,
            status="completed",
            total_rows=1,
            accepted_rows=1,
            rejected_rows=0,
            completed_at=second_created,
            created_at=second_created,
            updated_at=second_created,
        )
        session.add_all([first_batch, second_batch])
        await session.flush()
        first_transaction = Transaction(
            portfolio_id=portfolio.id,
            security_id=security.id,
            transaction_type="opening_balance",
            occurred_at=first_created - timedelta(days=10),
            quantity=Decimal("10"),
            price=Decimal("100"),
            gross_amount=Decimal("1000"),
            fees=Decimal("0"),
            taxes=Decimal("0"),
            currency="USD",
            fx_rate_to_base=Decimal("1"),
            source="legacy_dashboard_csv",
            import_batch_id=first_batch.id,
            source_record_hash="3" * 64,
            raw_payload={"ticker": security.canonical_symbol, "shares": "10"},
            created_at=first_created,
            updated_at=first_created,
        )
        second_transaction = Transaction(
            portfolio_id=portfolio.id,
            security_id=security.id,
            transaction_type="opening_balance",
            occurred_at=first_created - timedelta(days=10),
            quantity=Decimal("12"),
            price=Decimal("100"),
            gross_amount=Decimal("1200"),
            fees=Decimal("0"),
            taxes=Decimal("0"),
            currency="USD",
            fx_rate_to_base=Decimal("1"),
            source="legacy_dashboard_csv",
            import_batch_id=second_batch.id,
            source_record_hash="4" * 64,
            raw_payload={"ticker": security.canonical_symbol, "shares": "12"},
            created_at=second_created,
            updated_at=second_created,
        )
        first_price = PriceBar(
            security_id=security.id,
            trade_date=first_created.date(),
            source=f"legacy_csv_user_supplied:{first_batch.id}",
            currency="USD",
            close=Decimal("125"),
            adjusted_close=Decimal("125"),
            adjustment_factor=Decimal("1"),
            data_as_of=first_created,
            is_final=True,
            quality_status="valid",
            raw_payload={"import_batch_id": str(first_batch.id), "user_supplied": True},
        )
        second_price = PriceBar(
            security_id=security.id,
            trade_date=second_created.date(),
            source=f"legacy_csv_user_supplied:{second_batch.id}",
            currency="USD",
            close=Decimal("125"),
            adjusted_close=Decimal("125"),
            adjustment_factor=Decimal("1"),
            data_as_of=second_created,
            is_final=True,
            quality_status="valid",
            raw_payload={"import_batch_id": str(second_batch.id), "user_supplied": True},
        )
        session.add_all(
            [first_transaction, second_transaction, first_price, second_price]
        )
        await session.flush()
        old_valuation = PortfolioValuationSnapshot(
            portfolio_id=portfolio.id,
            as_of=old_valuation_as_of,
            valuation_date=old_valuation_as_of.date(),
            base_currency="USD",
            total_market_value=Decimal("2750"),
            priced_market_value=Decimal("2750"),
            total_cost_basis=Decimal("2200"),
            cash_value=Decimal("0"),
            unrealized_pnl=Decimal("550"),
            valuation_status="complete",
            priced_asset_count=1,
            unpriced_asset_count=0,
            coverage_ratio=Decimal("1"),
            data_as_of=second_created,
            data_as_of_earliest=second_created,
            data_as_of_latest=second_created,
            max_staleness_days=0,
            source="ledger_rebuild",
            input_hash="5" * 64,
            history_completeness="opening_balance_only",
            config_snapshot={
                "valuation_as_of": old_valuation_as_of.isoformat(),
                "data_source_context": {
                    "transaction_source": "legacy_dashboard_csv"
                },
            },
        )
        session.add(old_valuation)
        await session.flush()
        session.add(
            PositionSnapshot(
                valuation_snapshot_id=old_valuation.id,
                security_id=security.id,
                quantity=Decimal("22"),
                average_cost=Decimal("100"),
                price_bar_id=second_price.id,
                native_price=Decimal("125"),
                native_currency="USD",
                valuation_fx_rate=Decimal("1"),
                market_value_base=Decimal("2750"),
                cost_basis_base=Decimal("2200"),
                unrealized_pnl_base=Decimal("550"),
                cost_basis_native=Decimal("2200"),
                cost_basis_base_at_trade=Decimal("2200"),
                local_price_pnl=Decimal("550"),
                fx_pnl=Decimal("0"),
                total_pnl_base=Decimal("550"),
                weight=Decimal("1"),
                base_currency="USD",
                snapshot_data={"legacy_additive_fixture": True},
            )
        )
    return portfolio.id, security.id, principal, old_valuation_as_of


async def _create_formal_portfolio(
    session: AsyncSession,
    suffix: str,
    cutoff: datetime,
) -> tuple[Portfolio, Principal]:
    user = User(email=f"formal-lineage-{suffix}@example.invalid")
    security = Security(
        canonical_symbol=f"FOR{suffix[:8].upper()}",
        name="Formal Ledger Fixture",
        asset_type="equity",
        market="US",
        exchange="NYSE",
        currency="USD",
        sector="Industrials",
        country="US",
    )
    session.add_all([user, security])
    await session.flush()
    portfolio = Portfolio(
        user_id=user.id,
        tenant_id=f"formal-tenant-{suffix}",
        name=f"Formal Portfolio {suffix}",
        base_currency="USD",
    )
    session.add(portfolio)
    await session.flush()
    principal = Principal(
        user_id=f"formal-viewer-{suffix}",
        authenticated=True,
        tenant_id=portfolio.tenant_id,
        roles=frozenset({"viewer"}),
    )
    session.add_all(
        [
            PortfolioMembership(
                portfolio_id=portfolio.id,
                user_id=principal.user_id,
                role="viewer",
                can_read=True,
                can_write=False,
                can_admin=False,
            ),
            Transaction(
                portfolio_id=portfolio.id,
                security_id=security.id,
                transaction_type="opening_balance",
                occurred_at=cutoff - timedelta(days=2),
                quantity=Decimal("3"),
                price=Decimal("50"),
                gross_amount=Decimal("150"),
                fees=Decimal("0"),
                taxes=Decimal("0"),
                currency="USD",
                fx_rate_to_base=Decimal("1"),
                source="standard_csv",
                source_record_hash="6" * 64,
            ),
            PriceBar(
                security_id=security.id,
                trade_date=(cutoff - timedelta(days=1)).date(),
                source="public_test_fixture",
                currency="USD",
                close=Decimal("60"),
                adjusted_close=Decimal("60"),
                adjustment_factor=Decimal("1"),
                data_as_of=cutoff - timedelta(days=1),
                is_final=True,
                quality_status="valid",
            ),
        ]
    )
    await session.flush()
    await PortfolioValuationService(session).value(
        portfolio_id=portfolio.id,
        as_of=cutoff,
        knowledge_as_of=cutoff,
    )
    return portfolio, principal


@pytest.mark.asyncio
async def test_0006_upgrade_rejects_additive_valuation_until_idempotent_repair() -> None:
    base_url = _database_url()
    database = f"pp_lineage_{uuid.uuid4().hex}"
    database_url = _application_database_url(base_url, database)
    await _create_database(base_url, database)
    engine = None
    try:
        await asyncio.to_thread(
            _run_alembic,
            database_url,
            "upgrade",
            REVISION_BEFORE_GENERATIONS,
        )
        engine, factory = _factory(database_url)
        suffix = uuid.uuid4().hex
        portfolio_id, _, principal, old_as_of = await _seed_0006_additive_state(
            factory, suffix
        )
        async with factory() as session:
            version = await session.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == REVISION_BEFORE_GENERATIONS
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.portfolio_id == portfolio_id)
                )
                == 2
            )
        await engine.dispose()
        engine = None

        await asyncio.to_thread(
            _run_alembic,
            database_url,
            "upgrade",
            GENERATION_REVISION,
        )
        engine, factory = _factory(database_url)
        cutoff = old_as_of + timedelta(days=2)
        async with factory() as session:
            generations = list(
                await session.scalars(
                    select(LegacySnapshotGeneration)
                    .where(LegacySnapshotGeneration.portfolio_id == portfolio_id)
                    .order_by(LegacySnapshotGeneration.generation_number)
                )
            )
            assert [generation.status for generation in generations] == [
                "superseded",
                "active",
            ]
            active_generation = generations[1]
            with pytest.raises(PortfolioRebuildRequired) as exc_info:
                await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio_id,
                    as_of=cutoff,
                    principal=principal,
                )
            assert exc_info.value.active_generation_id == active_generation.id
            assert exc_info.value.reason == "valuation_generation_mismatch"

        conflict = await _api_request(
            factory=factory,
            principal=principal,
            portfolio_id=portfolio_id,
        )
        assert conflict.status_code == 409
        assert conflict.json() == {
            "error": "portfolio_rebuild_required",
            "portfolio_id": str(portfolio_id),
            "active_generation_id": str(active_generation.id),
            "reason": "valuation_generation_mismatch",
        }

        async with factory.begin() as session:
            old_valuation = await PortfolioValuationRepository(
                session
            ).latest_for_source(
                portfolio_id,
                source="ledger_rebuild",
                as_of=cutoff,
            )
            assert old_valuation is not None
            old_valuation.config_snapshot = {
                **old_valuation.config_snapshot,
                "data_source_context": {
                    "transaction_source": "legacy_dashboard_csv",
                    "legacy_snapshot_generation_id": str(generations[0].id),
                    "legacy_snapshot_import_batch_id": str(
                        generations[0].import_batch_id
                    ),
                },
            }
        async with factory() as session:
            with pytest.raises(PortfolioRebuildRequired):
                await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio_id,
                    as_of=cutoff,
                    principal=principal,
                )

        async with factory() as session:
            valuation_count_before = await session.scalar(
                select(func.count())
                .select_from(PortfolioValuationSnapshot)
                .where(PortfolioValuationSnapshot.portfolio_id == portfolio_id)
            )
        dry_run = await run_repair(
            dry_run=True,
            portfolio_id=portfolio_id,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert dry_run["status"] == "dry_run"
        assert dry_run["affected_portfolios"] == 1
        assert dry_run["outcomes"][0]["reason"] == "valuation_generation_mismatch"
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PortfolioValuationSnapshot)
                    .where(PortfolioValuationSnapshot.portfolio_id == portfolio_id)
                )
                == valuation_count_before
            )

        repaired = await run_repair(
            dry_run=False,
            portfolio_id=portfolio_id,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert repaired["status"] == "completed"
        assert repaired["affected_portfolios"] == 1
        assert repaired["outcomes"][0]["status"] == "rebuilt"

        async with factory() as session:
            matching = await PortfolioValuationRepository(
                session
            ).latest_for_legacy_generation(
                portfolio_id,
                active_generation.id,
                cutoff,
            )
            assert matching is not None
            assert matching.config_snapshot["data_source_context"][
                "legacy_snapshot_generation_id"
            ] == str(active_generation.id)
            assert matching.config_snapshot["data_source_context"][
                "legacy_snapshot_import_batch_id"
            ] == str(active_generation.import_batch_id)
            context = await LegacyPortfolioAdapter(session).load(
                portfolio_id=portfolio_id,
                as_of=cutoff,
                principal=principal,
            )
            assert context is not None
            assert context.valuation.id == matching.id
            assert context.summary.stocks[0].position.shares == 12

        dashboard = await _api_request(
            factory=factory,
            principal=principal,
            portfolio_id=portfolio_id,
        )
        assert dashboard.status_code == 200
        assert dashboard.json()["stocks"][0]["position"]["shares"] == 12

        async with factory.begin() as session:
            unchanged = await LegacyValuationRepairService(session).repair_portfolio(
                portfolio_id,
                as_of=cutoff,
            )
            assert unchanged.status == "unchanged"
            assert unchanged.valuation_snapshot_id is not None

        replay = await run_repair(
            dry_run=False,
            portfolio_id=portfolio_id,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert replay["status"] == "completed"
        assert replay["affected_portfolios"] == 0
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PortfolioValuationSnapshot)
                    .where(PortfolioValuationSnapshot.portfolio_id == portfolio_id)
                )
                == valuation_count_before + 1
            )

        async with factory.begin() as session:
            formal, formal_principal = await _create_formal_portfolio(
                session, suffix, cutoff
            )
            not_applicable = await LegacyValuationRepairService(
                session
            ).repair_portfolio(formal.id, as_of=cutoff)
            assert not_applicable.status == "not_applicable"
        async with factory() as session:
            formal_context = await LegacyPortfolioAdapter(session).load(
                portfolio_id=formal.id,
                as_of=cutoff,
                principal=formal_principal,
            )
            assert formal_context is not None
            assert formal_context.summary.stocks[0].position.shares == 3
            formal_scan = await LegacyValuationRepairService(session).scan(
                portfolio_id=formal.id,
                as_of=cutoff,
            )
            assert formal_scan.scanned_portfolios == 0
            assert formal_scan.affected_portfolios == 0

            other_tenant = Principal(
                user_id=f"other-{suffix}",
                authenticated=True,
                tenant_id=f"other-tenant-{suffix}",
                roles=frozenset({"viewer"}),
            )
            assert (
                await LegacyPortfolioAdapter(session).load(
                    portfolio_id=portfolio_id,
                    as_of=cutoff,
                    principal=other_tenant,
                )
                is None
            )
        isolated = await _api_request(
            factory=factory,
            principal=other_tenant,
            portfolio_id=portfolio_id,
        )
        assert isolated.status_code == 503
        assert "active_generation_id" not in isolated.json()
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(base_url, database)


@pytest.mark.asyncio
async def test_limited_repair_advances_to_later_stale_portfolios() -> None:
    base_url = _database_url()
    database = f"pp_lineage_limit_{uuid.uuid4().hex}"
    database_url = _application_database_url(base_url, database)
    portfolio_ids = [uuid.UUID(int=index) for index in range(1, 5)]
    await _create_database(base_url, database)
    engine = None
    try:
        await asyncio.to_thread(
            _run_alembic,
            database_url,
            "upgrade",
            REVISION_BEFORE_GENERATIONS,
        )
        engine, factory = _factory(database_url)
        old_as_of: datetime | None = None
        principals: dict[uuid.UUID, Principal] = {}
        for index, portfolio_id in enumerate(portfolio_ids, start=1):
            suffix = f"{index:08d}{uuid.uuid4().hex}"
            seeded_id, _, principal, seeded_as_of = await _seed_0006_additive_state(
                factory,
                suffix,
                portfolio_id=portfolio_id,
            )
            assert seeded_id == portfolio_id
            principals[portfolio_id] = principal
            old_as_of = seeded_as_of
        assert old_as_of is not None
        await engine.dispose()
        engine = None

        await asyncio.to_thread(
            _run_alembic,
            database_url,
            "upgrade",
            GENERATION_REVISION,
        )
        engine, factory = _factory(database_url)
        cutoff = old_as_of + timedelta(days=2)

        for portfolio_id in portfolio_ids[:2]:
            healthy = await run_repair(
                dry_run=False,
                portfolio_id=portfolio_id,
                limit=1,
                continue_on_error=False,
                as_of=cutoff,
                session_factory=factory,
            )
            assert healthy["status"] == "completed"
            assert healthy["affected_portfolios"] == 1
            assert healthy["outcomes"][0]["status"] == "rebuilt"

        first_dry_run = await run_repair(
            dry_run=True,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert first_dry_run["scanned_portfolios"] == 3
        assert first_dry_run["affected_portfolios"] == 1
        assert first_dry_run["outcomes"][0]["portfolio_id"] == str(
            portfolio_ids[2]
        )

        first_repair = await run_repair(
            dry_run=False,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert first_repair["affected_portfolios"] == 1
        assert first_repair["outcomes"][0]["portfolio_id"] == str(portfolio_ids[2])
        assert first_repair["outcomes"][0]["status"] == "rebuilt"

        second_dry_run = await run_repair(
            dry_run=True,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert second_dry_run["scanned_portfolios"] == 4
        assert second_dry_run["affected_portfolios"] == 1
        assert second_dry_run["outcomes"][0]["portfolio_id"] == str(
            portfolio_ids[3]
        )

        second_repair = await run_repair(
            dry_run=False,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert second_repair["affected_portfolios"] == 1
        assert second_repair["outcomes"][0]["portfolio_id"] == str(portfolio_ids[3])
        assert second_repair["outcomes"][0]["status"] == "rebuilt"

        final_dry_run = await run_repair(
            dry_run=True,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert final_dry_run["scanned_portfolios"] == 4
        assert final_dry_run["affected_portfolios"] == 0

        async with factory.begin() as session:
            formal, formal_principal = await _create_formal_portfolio(
                session,
                uuid.uuid4().hex,
                cutoff,
            )
        async with factory() as session:
            repository = TransactionRepository(session)
            effective = await repository.list_effective_for_portfolio(
                portfolio_ids[2]
            )
            audit = await repository.list_audit_for_portfolio(portfolio_ids[2])
            formal_effective = await repository.list_effective_for_portfolio(formal.id)
            assert [row.quantity for row in effective] == [Decimal("12")]
            assert [row.transaction.quantity for row in audit] == [
                Decimal("10"),
                Decimal("12"),
            ]
            assert [row.effective for row in audit] == [False, True]
            assert [row.quantity for row in formal_effective] == [Decimal("3")]
            formal_context = await LegacyPortfolioAdapter(session).load(
                portfolio_id=formal.id,
                as_of=cutoff,
                principal=formal_principal,
            )
            assert formal_context is not None
            assert formal_context.summary.stocks[0].position.shares == 3

        await engine.dispose()
        engine = None
        engine, factory = _factory(database_url)
        after_reconnect = await run_repair(
            dry_run=True,
            portfolio_id=None,
            limit=1,
            continue_on_error=False,
            as_of=cutoff,
            session_factory=factory,
        )
        assert after_reconnect["affected_portfolios"] == 0
        async with factory() as session:
            context = await LegacyPortfolioAdapter(session).load(
                portfolio_id=portfolio_ids[3],
                as_of=cutoff,
                principal=principals[portfolio_ids[3]],
            )
            assert context is not None
            assert context.summary.stocks[0].position.shares == 12
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(base_url, database)
