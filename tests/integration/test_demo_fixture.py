"""PostgreSQL integration coverage for the deterministic demo fixture."""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.db.models import (
    FxRate,
    LLMCallTrace,
    Portfolio,
    PriceBar,
    ProviderSymbol,
    ResearchDocument,
    Security,
    Transaction,
    User,
)
from app.providers.embeddings import HashingEmbeddingProvider
from app.services.demo_fixture import (
    DEMO_DOCUMENT_SOURCE,
    DEMO_FLAGS,
    DEMO_FX_SOURCE,
    DEMO_MODEL_SOURCE,
    DEMO_PORTFOLIO_ID,
    DEMO_PRICE_SOURCE,
    DEMO_PRINCIPAL_USER,
    DEMO_TENANT_ID,
    DEMO_TRANSACTION_SOURCE,
    demo_record_counts,
    reset_demo_fixture,
    seed_demo_fixture,
)
from app.storage.local import LocalObjectStorage
from config import settings
from scripts import demo_smoke

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        _url(), connect_args={"server_settings": {"timezone": "UTC"}}
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def _enable_demo_settings(monkeypatch, storage_root: Path) -> None:
    monkeypatch.setattr(settings, "DEMO_FIXTURE_MODE", True)
    monkeypatch.setattr(settings, "READ_ONLY_DEMO", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "LOCAL_PRINCIPAL_USER", DEMO_PRINCIPAL_USER)
    monkeypatch.setattr(settings, "LOCAL_PRINCIPAL_TENANT", DEMO_TENANT_ID)
    monkeypatch.setattr(
        settings,
        "LOCAL_PRINCIPAL_ROLES",
        "platform_admin,operator,market_data_admin,knowledge_admin,research_reviewer",
    )
    monkeypatch.setattr(settings, "LOCAL_PRINCIPAL_GROUPS", "public")
    monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "OBJECT_STORAGE_LOCAL_ROOT", str(storage_root))
    monkeypatch.setattr(settings, "OBJECT_STORAGE_BUCKET", "deterministic-demo-test")
    monkeypatch.setattr(settings, "OBJECT_STORAGE_PREFIX", "demo-fixture-test")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(settings, "RAG_ALLOW_HASHING_FALLBACK", True)


@pytest.mark.asyncio
async def test_demo_seed_is_idempotent_and_has_complete_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    engine, factory = await _factory()
    _enable_demo_settings(monkeypatch, tmp_path)
    storage = LocalObjectStorage(tmp_path, bucket="deterministic-demo-test")
    embedder = HashingEmbeddingProvider(384)
    try:
        async with factory.begin() as session:
            await reset_demo_fixture(session, storage=storage)
        async with factory.begin() as session:
            first = await seed_demo_fixture(
                session,
                storage=storage,
                embedder=embedder,
            )
        async with factory.begin() as session:
            second = await seed_demo_fixture(
                session,
                storage=storage,
                embedder=embedder,
            )
            counts = await demo_record_counts(session)
            assert first.as_dict() == second.as_dict()
            assert counts == first.record_counts
            assert counts["transactions"] == 5
            assert counts["position_snapshots"] == 3
            assert counts["documents"] == 2
            assert counts["chunks"] >= 2
            assert counts["embeddings"] == counts["chunks"]
            assert counts["prompt_versions"] == 1
            assert counts["workflows"] == 1
            assert counts["traces"] == 1
            assert counts["review_decisions"] == 1
            assert counts["published_reports"] == 1

            transaction_sources = set(
                await session.scalars(
                    select(Transaction.source).where(
                        Transaction.portfolio_id == DEMO_PORTFOLIO_ID
                    )
                )
            )
            price_sources = set(
                await session.scalars(
                    select(PriceBar.source).where(
                        PriceBar.source == DEMO_PRICE_SOURCE
                    )
                )
            )
            fx_sources = set(
                await session.scalars(
                    select(FxRate.source).where(FxRate.source == DEMO_FX_SOURCE)
                )
            )
            assert transaction_sources == {DEMO_TRANSACTION_SOURCE}
            assert price_sources == {DEMO_PRICE_SOURCE}
            assert fx_sources == {DEMO_FX_SOURCE}
            assert (
                await session.scalar(
                    select(func.count(ProviderSymbol.id))
                    .join(Security, Security.id == ProviderSymbol.security_id)
                    .where(Security.security_metadata["is_demo"].astext == "true")
                )
            ) == 0

            documents = list(
                (
                    await session.scalars(
                        select(ResearchDocument).where(
                            ResearchDocument.source_type == DEMO_DOCUMENT_SOURCE
                        )
                    )
                ).all()
            )
            assert len(documents) == 2
            assert all(
                all(item.metadata_json.get(key) is value for key, value in DEMO_FLAGS.items())
                for item in documents
            )
            trace = await session.scalar(
                select(LLMCallTrace).where(LLMCallTrace.provider == DEMO_MODEL_SOURCE)
            )
            assert trace is not None
            assert trace.model == "synthetic-no-network-v1"
            assert trace.response_payload["mock_response_used"] is True
            assert trace.response_payload["real_model_used"] is False
            assert trace.response_payload["human_label_used"] is False
            assert trace.response_payload["production_data_used"] is False
            assert trace.evidence_ids
    finally:
        async with factory.begin() as session:
            await reset_demo_fixture(session, storage=storage)
        await engine.dispose()


@pytest.mark.asyncio
async def test_demo_reset_preserves_non_demo_data(tmp_path: Path, monkeypatch) -> None:
    engine, factory = await _factory()
    _enable_demo_settings(monkeypatch, tmp_path)
    storage = LocalObjectStorage(tmp_path, bucket="deterministic-demo-test")
    sentinel_id = uuid.uuid4()
    try:
        async with factory.begin() as session:
            await reset_demo_fixture(session, storage=storage)
            session.add(
                User(
                    id=sentinel_id,
                    email=f"formal-{sentinel_id}@example.invalid",
                    display_name="Formal sentinel",
                    preferences={"is_demo": False},
                )
            )
        async with factory.begin() as session:
            await seed_demo_fixture(
                session,
                storage=storage,
                embedder=HashingEmbeddingProvider(384),
            )
        async with factory.begin() as session:
            removed = await reset_demo_fixture(session, storage=storage)
            assert removed["portfolios"] == 1
            assert await session.get(User, sentinel_id) is not None
            assert all(value == 0 for value in (await demo_record_counts(session)).values())
    finally:
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == sentinel_id))
            await reset_demo_fixture(session, storage=storage)
        await engine.dispose()


@pytest.mark.asyncio
async def test_demo_smoke_exercises_read_only_http_chain(
    tmp_path: Path, monkeypatch
) -> None:
    engine, factory = await _factory()
    _enable_demo_settings(monkeypatch, tmp_path)
    storage = LocalObjectStorage(tmp_path, bucket="deterministic-demo-test")
    principal = Principal(
        user_id=DEMO_PRINCIPAL_USER,
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id=DEMO_TENANT_ID,
        roles=frozenset(
            {
                "platform_admin",
                "operator",
                "knowledge_admin",
                "research_reviewer",
            }
        ),
    )

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

    try:
        async with factory.begin() as session:
            await reset_demo_fixture(session, storage=storage)
            await seed_demo_fixture(
                session,
                storage=storage,
                embedder=HashingEmbeddingProvider(384),
            )
        main.app.dependency_overrides[get_db_session] = session_override
        main.app.dependency_overrides[get_principal] = principal_override
        result = await demo_smoke._run(
            "http://test",
            10,
            transport=httpx.ASGITransport(app=main.app),
        )
        assert result["status"] == "passed"
        assert result["mock_response_used"] is True
        assert result["real_model_used"] is False
        assert result["human_label_used"] is False
    finally:
        main.app.dependency_overrides.clear()
        async with factory.begin() as session:
            await reset_demo_fixture(session, storage=storage)
            assert await session.scalar(
                select(func.count(Portfolio.id)).where(Portfolio.id == DEMO_PORTFOLIO_ID)
            ) == 0
        await engine.dispose()
