"""Deterministic, explicitly synthetic fixture for the local read-only demo."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    FxRate,
    ImportBatch,
    IngestionJob,
    LLMCallTrace,
    Portfolio,
    PortfolioMembership,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    PriceBar,
    PromptDeployment,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    Security,
    Transaction,
    User,
    WorkflowRun,
    WorkflowStep,
)
from app.db.repositories import (
    FxRateRepository,
    ImportBatchRepository,
    PriceBarRepository,
    TransactionRepository,
)
from app.providers.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from app.services.portfolio_valuation import PortfolioValuationService
from app.services.research_knowledge import PostgresKnowledgeService
from app.services.transaction_ledger import TransactionLedgerService
from app.storage import ObjectStorage, build_object_storage
from config import Settings, settings

DEMO_NAMESPACE = uuid.UUID("77867775-d396-5a15-86a9-d3a7827bbb16")
DEMO_AS_OF = datetime(2026, 8, 22, 12, tzinfo=UTC)
DEMO_USER_ID = uuid.uuid5(DEMO_NAMESPACE, "user")
DEMO_PORTFOLIO_ID = uuid.uuid5(DEMO_NAMESPACE, "portfolio")
DEMO_MEMBERSHIP_ID = uuid.uuid5(DEMO_NAMESPACE, "membership")
DEMO_IMPORT_BATCH_ID = uuid.uuid5(DEMO_NAMESPACE, "transaction-import")
DEMO_PROMPT_ID = uuid.uuid5(DEMO_NAMESPACE, "prompt")
DEMO_PROMPT_VERSION_ID = uuid.uuid5(DEMO_NAMESPACE, "prompt-version-1")
DEMO_PROMPT_DEPLOYMENT_ID = uuid.uuid5(DEMO_NAMESPACE, "prompt-deployment-1")
DEMO_WORKFLOW_ID = uuid.uuid5(DEMO_NAMESPACE, "workflow")
DEMO_TRACE_ID = uuid.uuid5(DEMO_NAMESPACE, "trace")
DEMO_REVIEW_TASK_ID = uuid.uuid5(DEMO_NAMESPACE, "review-task")
DEMO_REVIEW_DECISION_ID = uuid.uuid5(DEMO_NAMESPACE, "review-decision")
DEMO_REPORT_ID = uuid.uuid5(DEMO_NAMESPACE, "published-report")

DEMO_USER_EMAIL = "synthetic-demo@portfoliopilot.example.invalid"
DEMO_PRINCIPAL_USER = "demo-recruiter"
DEMO_TENANT_ID = "demo"
DEMO_PORTFOLIO_NAME = "PortfolioPilot Synthetic Research Demo"
DEMO_TRANSACTION_SOURCE = "demo_fixture_transactions"
DEMO_PRICE_SOURCE = "demo_fixture_prices"
DEMO_FX_SOURCE = "demo_fixture_fx"
DEMO_DOCUMENT_SOURCE = "demo_fixture_documents"
DEMO_MODEL_SOURCE = "demo_fixture_model"
DEMO_WORKFLOW_SCENE = "demo_fixture_research_workflow"
DEMO_PROMPT_KEY = "demo-fixture-portfolio-research"
DEMO_TRACE_KEY = "demo-fixture:portfolio-research:trace-v1"

DEMO_FLAGS: dict[str, bool] = {
    "is_demo": True,
    "synthetic_data_used": True,
    "mock_response_used": True,
    "real_model_used": False,
    "human_label_used": False,
    "production_data_used": False,
}

_SECURITY_FIXTURES: tuple[dict[str, str], ...] = (
    {
        "key": "cn-chip",
        "ticker": "DEMO-CN-CHIP",
        "name": "Synthetic China Semiconductor Basket",
        "asset_type": "equity",
        "market": "DEMO-CN-A",
        "exchange": "XDEMO-CN",
        "currency": "CNY",
        "sector": "Technology",
        "country": "CN",
        "start_price": "320.00",
        "daily_step": "0.55",
    },
    {
        "key": "us-tech",
        "ticker": "DEMO-US-TECH",
        "name": "Synthetic US Technology Basket",
        "asset_type": "equity",
        "market": "DEMO-US",
        "exchange": "XDEMO-US",
        "currency": "USD",
        "sector": "Technology",
        "country": "US",
        "start_price": "185.00",
        "daily_step": "0.38",
    },
    {
        "key": "cn-etf",
        "ticker": "DEMO-CN-ETF",
        "name": "Synthetic China Broad Market ETF",
        "asset_type": "ETF",
        "market": "DEMO-CN-ETF",
        "exchange": "XDEMO-ETF",
        "currency": "CNY",
        "sector": "Diversified",
        "country": "CN",
        "start_price": "4.25",
        "daily_step": "0.006",
    },
)

_TRANSACTION_FIXTURES: tuple[dict[str, str], ...] = (
    {
        "external_id": "demo-deposit-cny-1",
        "transaction_type": "deposit",
        "security_key": "",
        "occurred_at": "2026-07-01T01:00:00+00:00",
        "quantity": "0",
        "price": "",
        "gross_amount": "220000",
        "fees": "0",
        "taxes": "0",
        "currency": "CNY",
    },
    {
        "external_id": "demo-deposit-usd-1",
        "transaction_type": "deposit",
        "security_key": "",
        "occurred_at": "2026-07-01T01:05:00+00:00",
        "quantity": "0",
        "price": "",
        "gross_amount": "20000",
        "fees": "0",
        "taxes": "0",
        "currency": "USD",
    },
    {
        "external_id": "demo-buy-cn-chip-1",
        "transaction_type": "buy",
        "security_key": "cn-chip",
        "occurred_at": "2026-07-02T02:30:00+00:00",
        "quantity": "300",
        "price": "320",
        "gross_amount": "96000",
        "fees": "18",
        "taxes": "0",
        "currency": "CNY",
    },
    {
        "external_id": "demo-buy-us-tech-1",
        "transaction_type": "buy",
        "security_key": "us-tech",
        "occurred_at": "2026-07-03T14:30:00+00:00",
        "quantity": "60",
        "price": "185",
        "gross_amount": "11100",
        "fees": "10",
        "taxes": "0",
        "currency": "USD",
    },
    {
        "external_id": "demo-buy-cn-etf-1",
        "transaction_type": "buy",
        "security_key": "cn-etf",
        "occurred_at": "2026-07-06T02:35:00+00:00",
        "quantity": "10000",
        "price": "4.25",
        "gross_amount": "42500",
        "fees": "8",
        "taxes": "0",
        "currency": "CNY",
    },
)

_DOCUMENT_FIXTURES: tuple[dict[str, str], ...] = (
    {
        "key": "demo-fixture-semiconductor-risk-v1",
        "filename": "demo_semiconductor_risk.md",
        "title": "Synthetic Semiconductor Concentration Note",
        "ticker": "DEMO-CN-CHIP",
        "content": (
            "# Synthetic semiconductor concentration note\n\n"
            "This PortfolioPilot training fixture states that DEMO-CN-CHIP and "
            "DEMO-US-TECH share a synthetic technology-sector exposure. A combined "
            "technology weight above the configured research threshold should be "
            "flagged for human review. This text is self-authored synthetic evidence, "
            "not a market forecast or investment recommendation."
        ),
    },
    {
        "key": "demo-fixture-fx-risk-v1",
        "filename": "demo_fx_risk.md",
        "title": "Synthetic USD CNY Valuation Note",
        "ticker": "DEMO-US-TECH",
        "content": (
            "# Synthetic USD/CNY valuation note\n\n"
            "The DEMO-US-TECH position is priced in USD while the portfolio base "
            "currency is CNY. Portfolio valuation must cite the stored historical "
            "USD/CNY rate and must not infer currency from a ticker suffix. This text "
            "is a self-authored synthetic fixture and contains no live market data."
        ),
    },
)


@dataclass(frozen=True, slots=True)
class DemoManifest:
    portfolio_id: uuid.UUID
    import_batch_id: uuid.UUID
    valuation_snapshot_id: uuid.UUID
    document_ids: tuple[uuid.UUID, ...]
    prompt_version_id: uuid.UUID
    workflow_id: uuid.UUID
    trace_id: uuid.UUID
    review_task_id: uuid.UUID
    report_id: uuid.UUID
    evidence_ids: tuple[str, ...]
    record_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": str(self.portfolio_id),
            "import_batch_id": str(self.import_batch_id),
            "valuation_snapshot_id": str(self.valuation_snapshot_id),
            "document_ids": [str(item) for item in self.document_ids],
            "prompt_version_id": str(self.prompt_version_id),
            "workflow_id": str(self.workflow_id),
            "trace_id": str(self.trace_id),
            "review_task_id": str(self.review_task_id),
            "report_id": str(self.report_id),
            "evidence_ids": list(self.evidence_ids),
            "record_counts": dict(self.record_counts),
            "data_as_of": DEMO_AS_OF.isoformat(),
            "fixture_sources": fixture_sources(),
            **DEMO_FLAGS,
        }


def fixture_sources() -> dict[str, str]:
    return {
        "transactions": DEMO_TRANSACTION_SOURCE,
        "prices": DEMO_PRICE_SOURCE,
        "fx": DEMO_FX_SOURCE,
        "documents": DEMO_DOCUMENT_SOURCE,
        "model": DEMO_MODEL_SOURCE,
    }


def require_demo_fixture_mode(configuration: Settings = settings) -> None:
    if not configuration.DEMO_FIXTURE_MODE:
        raise RuntimeError("DEMO_FIXTURE_MODE=true is required for fixture operations")
    if configuration.ENVIRONMENT == "production":
        raise RuntimeError("DEMO_FIXTURE_MODE is forbidden in production")


async def seed_demo_fixture(
    session: AsyncSession,
    *,
    storage: ObjectStorage | None = None,
    embedder: EmbeddingProvider | None = None,
    configuration: Settings = settings,
) -> DemoManifest:
    """Seed one complete governed demo chain without external provider calls."""
    require_demo_fixture_mode(configuration)
    object_storage = storage or build_object_storage(configuration)
    deterministic_embedder = embedder or HashingEmbeddingProvider(
        configuration.RAG_EMBEDDING_DIMENSION
    )
    user = await _upsert_user(session)
    portfolio = await _upsert_portfolio(session, user)
    await _upsert_membership(session, portfolio)
    securities = await _upsert_securities(session)
    await _seed_fx_rates(session)
    import_batch = await _seed_transactions(session, portfolio, securities)
    await _seed_price_bars(session, securities)
    valuation_result = await PortfolioValuationService(session).value(
        portfolio_id=portfolio.id,
        as_of=DEMO_AS_OF,
        knowledge_as_of=DEMO_AS_OF,
        source="ledger_rebuild",
        data_source_context={
            **DEMO_FLAGS,
            "fixture_sources": fixture_sources(),
            "fixture_data_cutoff": DEMO_AS_OF.isoformat(),
        },
    )
    valuation_result.valuation.config_snapshot = {
        **valuation_result.valuation.config_snapshot,
        **DEMO_FLAGS,
        "fixture_sources": fixture_sources(),
    }
    for position in valuation_result.positions:
        position.snapshot_data = {
            **position.snapshot_data,
            **DEMO_FLAGS,
            "fixture_price_source": DEMO_PRICE_SOURCE,
            "fixture_fx_source": DEMO_FX_SOURCE,
        }
    if valuation_result.valuation.valuation_status != "complete":
        raise RuntimeError(
            "Demo valuation is incomplete: "
            + ", ".join(valuation_result.valuation.warnings)
        )
    citations = await _seed_research_documents(
        session,
        storage=object_storage,
        embedder=deterministic_embedder,
    )
    await _seed_governance_chain(
        session,
        portfolio=portfolio,
        valuation=valuation_result.valuation,
        citations=citations,
    )
    import_batch.error_summary = {
        **import_batch.error_summary,
        **DEMO_FLAGS,
        "fixture_sources": fixture_sources(),
    }
    await session.flush()
    return await load_demo_manifest(session)


async def load_demo_manifest(session: AsyncSession) -> DemoManifest:
    portfolio = await _required(
        session,
        select(Portfolio).where(Portfolio.id == DEMO_PORTFOLIO_ID),
        "portfolio",
    )
    import_batch = await _required(
        session,
        select(ImportBatch).where(
            ImportBatch.portfolio_id == portfolio.id,
            ImportBatch.source == DEMO_TRANSACTION_SOURCE,
        ),
        "transaction import batch",
    )
    valuation = await _required(
        session,
        select(PortfolioValuationSnapshot).where(
            PortfolioValuationSnapshot.portfolio_id == portfolio.id,
            PortfolioValuationSnapshot.as_of == DEMO_AS_OF,
            PortfolioValuationSnapshot.source == "ledger_rebuild",
        ),
        "valuation snapshot",
    )
    documents = list(
        (
            await session.scalars(
                select(ResearchDocument)
                .where(
                    ResearchDocument.document_key.in_(
                        [item["key"] for item in _DOCUMENT_FIXTURES]
                    )
                )
                .order_by(ResearchDocument.document_key)
            )
        ).all()
    )
    if len(documents) != len(_DOCUMENT_FIXTURES):
        raise RuntimeError("Demo research documents are incomplete")
    prompt_version = await _required(
        session,
        select(PromptVersion).where(PromptVersion.id == DEMO_PROMPT_VERSION_ID),
        "prompt version",
    )
    workflow = await _required(
        session,
        select(WorkflowRun).where(WorkflowRun.id == DEMO_WORKFLOW_ID),
        "workflow",
    )
    trace = await _required(
        session,
        select(LLMCallTrace).where(LLMCallTrace.id == DEMO_TRACE_ID),
        "LLM trace",
    )
    review_task = await _required(
        session,
        select(ReviewTask).where(ReviewTask.id == DEMO_REVIEW_TASK_ID),
        "review task",
    )
    report = await _required(
        session,
        select(PublishedReport).where(PublishedReport.id == DEMO_REPORT_ID),
        "published report",
    )
    await _assert_demo_lineage(
        session,
        portfolio=portfolio,
        valuation=valuation,
        documents=documents,
        prompt_version=prompt_version,
        workflow=workflow,
        trace=trace,
        review_task=review_task,
        report=report,
    )
    counts = await demo_record_counts(session)
    return DemoManifest(
        portfolio_id=portfolio.id,
        import_batch_id=import_batch.id,
        valuation_snapshot_id=valuation.id,
        document_ids=tuple(item.id for item in documents),
        prompt_version_id=prompt_version.id,
        workflow_id=workflow.id,
        trace_id=trace.id,
        review_task_id=review_task.id,
        report_id=report.id,
        evidence_ids=tuple(trace.evidence_ids),
        record_counts=counts,
    )


async def demo_record_counts(session: AsyncSession) -> dict[str, int]:
    document_ids = select(ResearchDocument.id).where(
        ResearchDocument.document_key.in_([item["key"] for item in _DOCUMENT_FIXTURES])
    )
    workflow_ids = select(WorkflowRun.id).where(
        WorkflowRun.business_scene == DEMO_WORKFLOW_SCENE
    )
    prompt_ids = select(PromptTemplate.id).where(
        PromptTemplate.prompt_key == DEMO_PROMPT_KEY
    )
    return {
        "users": await _count(session, select(func.count(User.id)).where(User.id == DEMO_USER_ID)),
        "portfolios": await _count(
            session, select(func.count(Portfolio.id)).where(Portfolio.id == DEMO_PORTFOLIO_ID)
        ),
        "transactions": await _count(
            session,
            select(func.count(Transaction.id)).where(
                Transaction.portfolio_id == DEMO_PORTFOLIO_ID,
                Transaction.source == DEMO_TRANSACTION_SOURCE,
            ),
        ),
        "price_bars": await _count(
            session,
            select(func.count(PriceBar.id)).where(PriceBar.source == DEMO_PRICE_SOURCE),
        ),
        "fx_rates": await _count(
            session,
            select(func.count(FxRate.id)).where(FxRate.source == DEMO_FX_SOURCE),
        ),
        "valuation_snapshots": await _count(
            session,
            select(func.count(PortfolioValuationSnapshot.id)).where(
                PortfolioValuationSnapshot.portfolio_id == DEMO_PORTFOLIO_ID,
                PortfolioValuationSnapshot.as_of == DEMO_AS_OF,
            ),
        ),
        "position_snapshots": await _count(
            session,
            select(func.count(PositionSnapshot.id)).join(
                PortfolioValuationSnapshot,
                PortfolioValuationSnapshot.id == PositionSnapshot.valuation_snapshot_id,
            ).where(
                PortfolioValuationSnapshot.portfolio_id == DEMO_PORTFOLIO_ID,
                PortfolioValuationSnapshot.as_of == DEMO_AS_OF,
            ),
        ),
        "documents": await _count(
            session, select(func.count(ResearchDocument.id)).where(ResearchDocument.id.in_(document_ids))
        ),
        "chunks": await _count(
            session, select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id.in_(document_ids))
        ),
        "embeddings": await _count(
            session,
            select(func.count(ChunkEmbedding.id))
            .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
            .where(DocumentChunk.document_id.in_(document_ids)),
        ),
        "prompt_versions": await _count(
            session, select(func.count(PromptVersion.id)).where(PromptVersion.prompt_id.in_(prompt_ids))
        ),
        "workflows": await _count(
            session, select(func.count(WorkflowRun.id)).where(WorkflowRun.id.in_(workflow_ids))
        ),
        "workflow_steps": await _count(
            session, select(func.count(WorkflowStep.id)).where(WorkflowStep.workflow_run_id.in_(workflow_ids))
        ),
        "traces": await _count(
            session, select(func.count(LLMCallTrace.id)).where(LLMCallTrace.workflow_run_id.in_(workflow_ids))
        ),
        "review_tasks": await _count(
            session, select(func.count(ReviewTask.id)).where(ReviewTask.workflow_run_id.in_(workflow_ids))
        ),
        "review_decisions": await _count(
            session, select(func.count(ReviewDecision.id)).where(ReviewDecision.workflow_run_id.in_(workflow_ids))
        ),
        "published_reports": await _count(
            session, select(func.count(PublishedReport.id)).where(PublishedReport.workflow_run_id.in_(workflow_ids))
        ),
    }


async def reset_demo_fixture(
    session: AsyncSession,
    *,
    storage: ObjectStorage | None = None,
    configuration: Settings = settings,
) -> dict[str, int]:
    """Delete only the explicit demo namespace and leave all other rows intact."""
    require_demo_fixture_mode(configuration)
    object_storage = storage or build_object_storage(configuration)
    before = await demo_record_counts(session)
    jobs = list(
        (
            await session.scalars(
                select(IngestionJob).where(IngestionJob.user_id == DEMO_PRINCIPAL_USER)
            )
        ).all()
    )
    await session.execute(
        delete(LLMCallTrace).where(LLMCallTrace.business_scene == DEMO_WORKFLOW_SCENE)
    )
    await session.execute(
        delete(PromptDeployment).where(PromptDeployment.prompt_id == DEMO_PROMPT_ID)
    )
    await session.execute(
        delete(WorkflowRun).where(WorkflowRun.business_scene == DEMO_WORKFLOW_SCENE)
    )
    await session.execute(
        delete(PromptTemplate).where(PromptTemplate.prompt_key == DEMO_PROMPT_KEY)
    )
    await session.execute(
        delete(IngestionJob).where(IngestionJob.user_id == DEMO_PRINCIPAL_USER)
    )
    await session.execute(
        delete(ResearchDocument).where(
            ResearchDocument.document_key.in_([item["key"] for item in _DOCUMENT_FIXTURES])
        )
    )
    await session.execute(delete(User).where(User.id == DEMO_USER_ID))
    await session.flush()
    await session.execute(delete(FxRate).where(FxRate.source == DEMO_FX_SOURCE))
    await session.execute(
        delete(Security).where(
            Security.id.in_([_security_id(item["key"]) for item in _SECURITY_FIXTURES])
        )
    )
    await session.flush()
    for job in jobs:
        if job.object_key:
            await object_storage.delete_object(job.object_key, version=job.object_version)
    return before


async def _upsert_user(session: AsyncSession) -> User:
    statement = (
        insert(User)
        .values(
            id=DEMO_USER_ID,
            email=DEMO_USER_EMAIL,
            display_name="Synthetic Demo User",
            is_active=True,
            preferences={**DEMO_FLAGS, "fixture_source": DEMO_TRANSACTION_SOURCE},
            created_at=DEMO_AS_OF,
            updated_at=DEMO_AS_OF,
        )
        .on_conflict_do_update(
            index_elements=[User.email],
            set_={
                "display_name": "Synthetic Demo User",
                "is_active": True,
                "preferences": {**DEMO_FLAGS, "fixture_source": DEMO_TRANSACTION_SOURCE},
            },
        )
        .returning(User)
    )
    return (await session.execute(statement)).scalar_one()


async def _upsert_portfolio(session: AsyncSession, user: User) -> Portfolio:
    portfolio_settings = {
        **DEMO_FLAGS,
        "fixture_sources": fixture_sources(),
        "fixture_data_cutoff": DEMO_AS_OF.isoformat(),
    }
    statement = (
        insert(Portfolio)
        .values(
            id=DEMO_PORTFOLIO_ID,
            user_id=user.id,
            tenant_id=DEMO_TENANT_ID,
            name=DEMO_PORTFOLIO_NAME,
            description=(
                "Deterministic synthetic portfolio for PortfolioPilot engineering demos; "
                "not an investment recommendation."
            ),
            base_currency="CNY",
            cost_basis_method="weighted_average",
            display_timezone="Asia/Shanghai",
            is_active=True,
            settings=portfolio_settings,
            created_at=DEMO_AS_OF,
            updated_at=DEMO_AS_OF,
        )
        .on_conflict_do_update(
            index_elements=[Portfolio.user_id, Portfolio.name],
            set_={
                "tenant_id": DEMO_TENANT_ID,
                "description": (
                    "Deterministic synthetic portfolio for PortfolioPilot engineering "
                    "demos; not an investment recommendation."
                ),
                "base_currency": "CNY",
                "cost_basis_method": "weighted_average",
                "display_timezone": "Asia/Shanghai",
                "is_active": True,
                "settings": portfolio_settings,
            },
        )
        .returning(Portfolio)
    )
    portfolio = (await session.execute(statement)).scalar_one()
    if portfolio.id != DEMO_PORTFOLIO_ID:
        raise RuntimeError("Demo portfolio natural key is owned by a non-demo record")
    return portfolio


async def _upsert_membership(session: AsyncSession, portfolio: Portfolio) -> None:
    statement = insert(PortfolioMembership).values(
        id=DEMO_MEMBERSHIP_ID,
        portfolio_id=portfolio.id,
        user_id=DEMO_PRINCIPAL_USER,
        role="admin",
        can_read=True,
        can_write=True,
        can_admin=True,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                PortfolioMembership.portfolio_id,
                PortfolioMembership.user_id,
            ],
            set_={
                "role": "admin",
                "can_read": True,
                "can_write": True,
                "can_admin": True,
            },
        )
    )


async def _upsert_securities(session: AsyncSession) -> dict[str, Security]:
    output: dict[str, Security] = {}
    for fixture in _SECURITY_FIXTURES:
        security_id = _security_id(fixture["key"])
        metadata = {
            **DEMO_FLAGS,
            "fixture_source": DEMO_PRICE_SOURCE,
            "canonicalization": "synthetic_demo_namespace",
        }
        statement = (
            insert(Security)
            .values(
                id=security_id,
                canonical_symbol=fixture["ticker"],
                name=fixture["name"],
                asset_type=fixture["asset_type"],
                market=fixture["market"],
                exchange=fixture["exchange"],
                currency=fixture["currency"],
                sector=fixture["sector"],
                country=fixture["country"],
                security_metadata=metadata,
                created_at=DEMO_AS_OF,
                updated_at=DEMO_AS_OF,
            )
            .on_conflict_do_update(
                index_elements=[
                    Security.canonical_symbol,
                    Security.exchange,
                    Security.market,
                ],
                set_={
                    "name": fixture["name"],
                    "asset_type": fixture["asset_type"],
                    "currency": fixture["currency"],
                    "sector": fixture["sector"],
                    "country": fixture["country"],
                    "security_metadata": metadata,
                },
            )
            .returning(Security)
        )
        security = (await session.execute(statement)).scalar_one()
        if security.id != security_id:
            raise RuntimeError(f"Demo security key collision: {fixture['ticker']}")
        output[fixture["key"]] = security
    return output


async def _seed_fx_rates(session: AsyncSession) -> None:
    repository = FxRateRepository(session)
    for index, rate_date in enumerate(_business_dates(date(2026, 7, 1), date(2026, 8, 21))):
        rate = Decimal("7.1500") + Decimal(index % 7 - 3) * Decimal("0.006")
        await repository.upsert(
            FxRate(
                id=uuid.uuid5(DEMO_NAMESPACE, f"fx:{rate_date.isoformat()}"),
                base_currency="USD",
                quote_currency="CNY",
                rate_date=rate_date,
                source=DEMO_FX_SOURCE,
                rate=rate,
                data_as_of=datetime.combine(
                    rate_date, datetime.min.time(), tzinfo=UTC
                )
                + timedelta(hours=16),
                raw_payload={
                    **DEMO_FLAGS,
                    "fixture_source": DEMO_FX_SOURCE,
                },
                created_at=DEMO_AS_OF,
                updated_at=DEMO_AS_OF,
            )
        )


async def _seed_transactions(
    session: AsyncSession,
    portfolio: Portfolio,
    securities: dict[str, Security],
) -> ImportBatch:
    fixture_bytes = json.dumps(
        _TRANSACTION_FIXTURES, sort_keys=True, separators=(",", ":")
    ).encode()
    file_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    batch, _ = await ImportBatchRepository(session).get_or_create(
        ImportBatch(
            id=DEMO_IMPORT_BATCH_ID,
            portfolio_id=portfolio.id,
            source=DEMO_TRANSACTION_SOURCE,
            source_filename="demo_fixture_transactions.json",
            file_sha256=file_sha256,
            status="processing",
            total_rows=len(_TRANSACTION_FIXTURES),
            accepted_rows=0,
            rejected_rows=0,
            error_summary={},
            created_at=DEMO_AS_OF,
            updated_at=DEMO_AS_OF,
        )
    )
    ledger = TransactionLedgerService(session)
    inserted = 0
    for index, fixture in enumerate(_TRANSACTION_FIXTURES, start=1):
        security_key = fixture["security_key"]
        external_id = fixture["external_id"]
        transaction = Transaction(
            id=uuid.uuid5(DEMO_NAMESPACE, f"transaction:{external_id}"),
            portfolio_id=portfolio.id,
            security_id=securities[security_key].id if security_key else None,
            transaction_type=fixture["transaction_type"],
            occurred_at=datetime.fromisoformat(fixture["occurred_at"]),
            quantity=Decimal(fixture["quantity"]),
            price=Decimal(fixture["price"]) if fixture["price"] else None,
            gross_amount=Decimal(fixture["gross_amount"]),
            fees=Decimal(fixture["fees"]),
            taxes=Decimal(fixture["taxes"]),
            currency=fixture["currency"],
            source=DEMO_TRANSACTION_SOURCE,
            external_id=external_id,
            import_batch_id=batch.id,
            source_record_hash=hashlib.sha256(
                f"{file_sha256}:{index}:{external_id}".encode()
            ).hexdigest(),
            note="Synthetic deterministic demo transaction; not a real holding.",
            raw_payload={
                **DEMO_FLAGS,
                "fixture_source": DEMO_TRANSACTION_SOURCE,
                "source_file_sha256": file_sha256,
                "source_row_number": index,
            },
            created_at=DEMO_AS_OF,
            updated_at=DEMO_AS_OF,
        )
        _, created = await ledger.add_transaction(transaction)
        inserted += int(created)
    batch.status = "completed"
    batch.total_rows = len(_TRANSACTION_FIXTURES)
    batch.accepted_rows = len(_TRANSACTION_FIXTURES)
    batch.rejected_rows = 0
    batch.completed_at = DEMO_AS_OF
    batch.error_summary = {
        **DEMO_FLAGS,
        "fixture_sources": fixture_sources(),
        "history_completeness": "complete",
        "persisted_rows": len(_TRANSACTION_FIXTURES),
        "inserted_this_run": inserted,
        "errors": [],
    }
    await session.flush()
    return batch


async def _seed_price_bars(
    session: AsyncSession,
    securities: dict[str, Security],
) -> None:
    repository = PriceBarRepository(session)
    trade_dates = _business_dates(date(2026, 7, 13), date(2026, 8, 21))
    for fixture in _SECURITY_FIXTURES:
        security = securities[fixture["key"]]
        start = Decimal(fixture["start_price"])
        step = Decimal(fixture["daily_step"])
        for index, trade_date in enumerate(trade_dates):
            cycle = Decimal((index % 5) - 2) * step / Decimal("2")
            close = start + Decimal(index) * step + cycle
            await repository.upsert(
                PriceBar(
                    id=uuid.uuid5(
                        DEMO_NAMESPACE,
                        f"price:{fixture['key']}:{trade_date.isoformat()}",
                    ),
                    security_id=security.id,
                    trade_date=trade_date,
                    source=DEMO_PRICE_SOURCE,
                    currency=security.currency,
                    open=close - step / Decimal("3"),
                    high=close + step,
                    low=max(Decimal("0.0001"), close - step),
                    close=close,
                    adjusted_close=close,
                    adjustment_factor=Decimal("1"),
                    volume=Decimal("1000000") + Decimal(index * 1000),
                    data_as_of=datetime.combine(
                        trade_date, datetime.min.time(), tzinfo=UTC
                    )
                    + timedelta(hours=20),
                    is_final=True,
                    quality_status="valid",
                    raw_payload={
                        **DEMO_FLAGS,
                        "fixture_source": DEMO_PRICE_SOURCE,
                        "license_note": "self-authored synthetic price path",
                    },
                    created_at=DEMO_AS_OF,
                    updated_at=DEMO_AS_OF,
                )
            )


async def _seed_research_documents(
    session: AsyncSession,
    *,
    storage: ObjectStorage,
    embedder: EmbeddingProvider,
) -> list[dict[str, Any]]:
    principal = Principal(
        user_id=DEMO_PRINCIPAL_USER,
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id=DEMO_TENANT_ID,
        roles=frozenset({"knowledge_admin"}),
    )
    service = PostgresKnowledgeService(
        session,
        embedder=embedder,
        storage=storage,
    )
    for fixture in _DOCUMENT_FIXTURES:
        job, _ = await service.queue_upload(
            content=fixture["content"].encode(),
            filename=fixture["filename"],
            metadata={
                "document_id": fixture["key"],
                "title": fixture["title"],
                "source_type": DEMO_DOCUMENT_SOURCE,
                "department": "PortfolioPilot Demo",
                "author": "PortfolioPilot synthetic fixture",
                "tickers": [fixture["ticker"]],
                "publish_date": DEMO_AS_OF.date().isoformat(),
                "effective_from": DEMO_AS_OF.date().isoformat(),
                "confidentiality": "public",
                "permission_groups": ["public"],
                **DEMO_FLAGS,
                "fixture_source": DEMO_DOCUMENT_SOURCE,
            },
            principal=principal,
            idempotency_key=fixture["key"],
        )
        if job.status in {"pending", "processing", "failed"}:
            await service.process_job(job)
        if job.document_id is None or job.version_id is None:
            raise RuntimeError(f"Demo document ingestion failed: {fixture['key']}")
        document = await service.repository.set_published(job.document_id)
        if document is None:
            raise RuntimeError(f"Demo document publication failed: {fixture['key']}")
        document.metadata_json = {
            **document.metadata_json,
            **DEMO_FLAGS,
            "fixture_source": DEMO_DOCUMENT_SOURCE,
        }
        version = await session.get(DocumentVersion, job.version_id)
        if version is None:
            raise RuntimeError(f"Demo document version is missing: {fixture['key']}")
        version.metadata_json = {
            **version.metadata_json,
            **DEMO_FLAGS,
            "fixture_source": DEMO_DOCUMENT_SOURCE,
        }
        job.metadata_json = {
            **job.metadata_json,
            **DEMO_FLAGS,
            "fixture_source": DEMO_DOCUMENT_SOURCE,
        }
    await session.flush()
    retrieval = await service.retrieve_with_status(
        "synthetic",
        principal=principal,
        top_k=5,
        as_of=DEMO_AS_OF.date(),
        score_threshold=0.0,
    )
    citations = list(retrieval.get("citations", []))
    if not citations:
        raise RuntimeError("Demo RAG retrieval produced no evidence")
    return citations


async def _seed_governance_chain(
    session: AsyncSession,
    *,
    portfolio: Portfolio,
    valuation: PortfolioValuationSnapshot,
    citations: list[dict[str, Any]],
) -> None:
    prompt = await _upsert_prompt(session)
    prompt_version = await _upsert_prompt_version(session, prompt)
    await _upsert_prompt_deployment(session, prompt, prompt_version)
    evidence_ids = [str(item["chunk_id"]) for item in citations]
    document_ids = sorted({str(item["document_id"]) for item in citations})
    workflow_context = {
        **DEMO_FLAGS,
        "fixture_source": DEMO_MODEL_SOURCE,
        "portfolio_id": str(portfolio.id),
        "valuation_snapshot_id": str(valuation.id),
        "prompt_version_id": str(prompt_version.id),
        "evidence_ids": evidence_ids,
        "research_observations": [
            "Synthetic technology exposure exceeds the demo review threshold.",
            "The synthetic USD position requires stored USD/CNY valuation lineage.",
        ],
        "human_label_used": False,
        "disclaimer": "Synthetic engineering demo only; not investment advice.",
    }
    workflow_statement = insert(WorkflowRun).values(
        id=DEMO_WORKFLOW_ID,
        user_id=DEMO_PRINCIPAL_USER,
        tenant_id=DEMO_TENANT_ID,
        business_scene=DEMO_WORKFLOW_SCENE,
        idempotency_key="deterministic-demo-workflow-v1",
        status="PUBLISHED",
        max_steps=10,
        timeout_seconds=60,
        node_timeout_seconds=15,
        cost_budget=Decimal("0"),
        cost_amount=Decimal("0"),
        cost_is_estimated=True,
        current_step="publish_report",
        iteration=1,
        context_json=workflow_context,
        started_at=DEMO_AS_OF,
        completed_at=DEMO_AS_OF,
        error_type="",
        code_version=settings.resolved_code_version,
        attempt=1,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    workflow = (
        await session.execute(
            workflow_statement.on_conflict_do_update(
                index_elements=[
                    WorkflowRun.user_id,
                    WorkflowRun.business_scene,
                    WorkflowRun.idempotency_key,
                ],
                set_={
                    "status": "PUBLISHED",
                    "current_step": "publish_report",
                    "context_json": workflow_context,
                    "completed_at": DEMO_AS_OF,
                    "code_version": settings.resolved_code_version,
                },
            ).returning(WorkflowRun)
        )
    ).scalar_one()
    if workflow.id != DEMO_WORKFLOW_ID:
        raise RuntimeError("Demo workflow idempotency key is owned by another record")

    steps = (
        ("retrieve_evidence", {"evidence_ids": evidence_ids}),
        ("validate_analysis", {"output_schema_valid": True, **DEMO_FLAGS}),
        ("publish_report", {"status": "published", "human_label_used": False}),
    )
    for index, (name, output) in enumerate(steps):
        step_id = uuid.uuid5(DEMO_NAMESPACE, f"workflow-step:{name}")
        step_statement = insert(WorkflowStep).values(
            id=step_id,
            workflow_run_id=workflow.id,
            step_name=name,
            iteration=1,
            status="COMPLETED",
            input_summary={"fixture_source": DEMO_MODEL_SOURCE, **DEMO_FLAGS},
            output_summary=output,
            started_at=DEMO_AS_OF + timedelta(seconds=index),
            completed_at=DEMO_AS_OF + timedelta(seconds=index + 1),
            error_type="",
            created_at=DEMO_AS_OF,
            updated_at=DEMO_AS_OF,
        )
        await session.execute(
            step_statement.on_conflict_do_update(
                index_elements=[
                    WorkflowStep.workflow_run_id,
                    WorkflowStep.step_name,
                    WorkflowStep.iteration,
                ],
                set_={
                    "status": "COMPLETED",
                    "input_summary": {"fixture_source": DEMO_MODEL_SOURCE, **DEMO_FLAGS},
                    "output_summary": output,
                    "completed_at": DEMO_AS_OF + timedelta(seconds=index + 1),
                    "error_type": "",
                },
            )
        )

    response_payload = {
        **DEMO_FLAGS,
        "portfolio_summary": "Synthetic portfolio research draft generated from fixtures.",
        "risk_score": 6.2,
        "main_risks": ["synthetic technology concentration", "synthetic FX exposure"],
        "research_observations": workflow_context["research_observations"],
        "evidence_used": evidence_ids,
        "disclaimer": "Synthetic engineering demo only; not investment advice.",
    }
    response_encoded = json.dumps(response_payload, sort_keys=True, separators=(",", ":"))
    trace_statement = insert(LLMCallTrace).values(
        id=DEMO_TRACE_ID,
        trace_key=DEMO_TRACE_KEY,
        workflow_run_id=workflow.id,
        prompt_version_id=prompt_version.id,
        user_id=DEMO_PRINCIPAL_USER,
        business_scene=DEMO_WORKFLOW_SCENE,
        provider=DEMO_MODEL_SOURCE,
        model="synthetic-no-network-v1",
        model_parameters={"temperature": 0, **DEMO_FLAGS},
        request_hash=hashlib.sha256(
            json.dumps(workflow_context, sort_keys=True).encode()
        ).hexdigest(),
        response_hash=hashlib.sha256(response_encoded.encode()).hexdigest(),
        response_payload=response_payload,
        status="success",
        duration_ms=1,
        data_as_of=DEMO_AS_OF,
        code_version=settings.resolved_code_version,
        provider_usage={},
        usage_source="estimated",
        input_tokens=None,
        output_tokens=None,
        cost_amount=Decimal("0"),
        cost_currency="USD",
        cost_source="synthetic_fixture_zero_cost",
        evidence_ids=evidence_ids,
        retrieved_document_ids=document_ids,
        tool_calls=[],
        output_schema_valid=True,
        fallback_used=False,
        review_decision="approved_demo_fixture",
        review_feedback="Synthetic review fixture; no human reviewer participated.",
        error_message="",
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    await session.execute(
        trace_statement.on_conflict_do_update(
            index_elements=[LLMCallTrace.trace_key],
            set_={
                "workflow_run_id": workflow.id,
                "prompt_version_id": prompt_version.id,
                "provider": DEMO_MODEL_SOURCE,
                "model": "synthetic-no-network-v1",
                "model_parameters": {"temperature": 0, **DEMO_FLAGS},
                "request_hash": trace_statement.excluded.request_hash,
                "response_hash": trace_statement.excluded.response_hash,
                "response_payload": response_payload,
                "status": "success",
                "data_as_of": DEMO_AS_OF,
                "evidence_ids": evidence_ids,
                "retrieved_document_ids": document_ids,
                "output_schema_valid": True,
                "fallback_used": False,
                "review_decision": "approved_demo_fixture",
                "review_feedback": (
                    "Synthetic review fixture; no human reviewer participated."
                ),
            },
        )
    )

    review_statement = insert(ReviewTask).values(
        id=DEMO_REVIEW_TASK_ID,
        workflow_run_id=workflow.id,
        iteration=1,
        status="COMPLETED",
        assigned_group="research_reviewer",
        completed_at=DEMO_AS_OF,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    review_task = (
        await session.execute(
            review_statement.on_conflict_do_update(
                index_elements=[ReviewTask.workflow_run_id, ReviewTask.iteration],
                set_={"status": "COMPLETED", "completed_at": DEMO_AS_OF},
            ).returning(ReviewTask)
        )
    ).scalar_one()
    if review_task.id != DEMO_REVIEW_TASK_ID:
        raise RuntimeError("Demo review idempotency key is owned by another record")

    decision_statement = insert(ReviewDecision).values(
        id=DEMO_REVIEW_DECISION_ID,
        review_task_id=review_task.id,
        workflow_run_id=workflow.id,
        decision="approved",
        reviewer_id="demo_fixture_reviewer",
        feedback="Synthetic approval record; human_label_used=false.",
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    await session.execute(
        decision_statement.on_conflict_do_update(
            index_elements=[ReviewDecision.review_task_id],
            set_={
                "decision": "approved",
                "reviewer_id": "demo_fixture_reviewer",
                "feedback": "Synthetic approval record; human_label_used=false.",
            },
        )
    )

    report_json = {
        **DEMO_FLAGS,
        "human_label_used": False,
        "portfolio_id": str(portfolio.id),
        "valuation_snapshot_id": str(valuation.id),
        "prompt_version_id": str(prompt_version.id),
        "trace_key": DEMO_TRACE_KEY,
        "evidence_ids": evidence_ids,
        "title": "Synthetic Portfolio Risk Research Report",
        "summary": response_payload["portfolio_summary"],
        "research_observations": response_payload["research_observations"],
        "disclaimer": "Research-system demonstration only; not investment advice.",
    }
    report_statement = insert(PublishedReport).values(
        id=DEMO_REPORT_ID,
        workflow_run_id=workflow.id,
        report_json=report_json,
        published_by="demo_fixture_reviewer",
        published_at=DEMO_AS_OF,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    await session.execute(
        report_statement.on_conflict_do_update(
            index_elements=[PublishedReport.workflow_run_id],
            set_={
                "report_json": report_json,
                "published_by": "demo_fixture_reviewer",
                "published_at": DEMO_AS_OF,
            },
        )
    )


async def _upsert_prompt(session: AsyncSession) -> PromptTemplate:
    statement = insert(PromptTemplate).values(
        id=DEMO_PROMPT_ID,
        prompt_key=DEMO_PROMPT_KEY,
        name="Synthetic Portfolio Research Prompt",
        business_scene=DEMO_WORKFLOW_SCENE,
        owner="PortfolioPilot demo fixture",
        status="published",
        current_version=1,
        published_version=1,
        published_at=DEMO_AS_OF,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    prompt = (
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[PromptTemplate.prompt_key],
                set_={
                    "name": "Synthetic Portfolio Research Prompt",
                    "business_scene": DEMO_WORKFLOW_SCENE,
                    "owner": "PortfolioPilot demo fixture",
                    "status": "published",
                    "current_version": 1,
                    "published_version": 1,
                    "published_at": DEMO_AS_OF,
                },
            ).returning(PromptTemplate)
        )
    ).scalar_one()
    if prompt.id != DEMO_PROMPT_ID:
        raise RuntimeError("Demo prompt key is owned by another record")
    return prompt


async def _upsert_prompt_version(
    session: AsyncSession, prompt: PromptTemplate
) -> PromptVersion:
    baseline = {
        **DEMO_FLAGS,
        "evaluation_mode": "synthetic_smoke",
        "dataset_name": "deterministic_demo_fixture",
        "dataset_version": "1.0.0",
        "human_label_used": False,
    }
    statement = insert(PromptVersion).values(
        id=DEMO_PROMPT_VERSION_ID,
        prompt_id=prompt.id,
        version=1,
        template=(
            "Explain the deterministic portfolio risk facts in {risk_summary} using only "
            "the supplied {evidence}. Label the output synthetic and do not provide "
            "investment advice."
        ),
        variables=["risk_summary", "evidence"],
        input_schema={"type": "object", "required": ["risk_summary", "evidence"]},
        output_schema={
            "type": "object",
            "required": ["portfolio_summary", "main_risks", "evidence_used", "disclaimer"],
        },
        model="synthetic-no-network-v1",
        temperature=Decimal("0"),
        owner="PortfolioPilot demo fixture",
        status="published",
        change_log="Deterministic no-network demo prompt.",
        baseline_metrics=baseline,
        published_at=DEMO_AS_OF,
        created_at=DEMO_AS_OF,
        updated_at=DEMO_AS_OF,
    )
    prompt_version = (
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[PromptVersion.prompt_id, PromptVersion.version],
                set_={
                    "template": statement.excluded.template,
                    "variables": statement.excluded.variables,
                    "input_schema": statement.excluded.input_schema,
                    "output_schema": statement.excluded.output_schema,
                    "model": "synthetic-no-network-v1",
                    "temperature": Decimal("0"),
                    "owner": "PortfolioPilot demo fixture",
                    "status": "published",
                    "change_log": "Deterministic no-network demo prompt.",
                    "baseline_metrics": baseline,
                    "published_at": DEMO_AS_OF,
                },
            ).returning(PromptVersion)
        )
    ).scalar_one()
    if prompt_version.id != DEMO_PROMPT_VERSION_ID:
        raise RuntimeError("Demo prompt version is owned by another record")
    return prompt_version


async def _upsert_prompt_deployment(
    session: AsyncSession,
    prompt: PromptTemplate,
    prompt_version: PromptVersion,
) -> None:
    deployment = await session.get(PromptDeployment, DEMO_PROMPT_DEPLOYMENT_ID)
    if deployment is None:
        session.add(
            PromptDeployment(
                id=DEMO_PROMPT_DEPLOYMENT_ID,
                prompt_id=prompt.id,
                prompt_version_id=prompt_version.id,
                previous_version=None,
                action="publish",
                environment="demo",
                deployed_by="demo_fixture_seeder",
                created_at=DEMO_AS_OF,
                updated_at=DEMO_AS_OF,
            )
        )
        await session.flush()


async def _assert_demo_lineage(
    session: AsyncSession,
    *,
    portfolio: Portfolio,
    valuation: PortfolioValuationSnapshot,
    documents: list[ResearchDocument],
    prompt_version: PromptVersion,
    workflow: WorkflowRun,
    trace: LLMCallTrace,
    review_task: ReviewTask,
    report: PublishedReport,
) -> None:
    if portfolio.settings.get("is_demo") is not True:
        raise RuntimeError("Demo portfolio lost its provenance flags")
    if valuation.valuation_status != "complete" or valuation.data_as_of is None:
        raise RuntimeError("Demo valuation is missing or incomplete")
    if not _has_demo_flags(valuation.config_snapshot):
        raise RuntimeError("Demo valuation lost its provenance flags")
    transactions = list(
        (
            await session.scalars(
                select(Transaction).where(
                    Transaction.portfolio_id == portfolio.id,
                    Transaction.source == DEMO_TRANSACTION_SOURCE,
                )
            )
        ).all()
    )
    if len(transactions) != len(_TRANSACTION_FIXTURES) or not all(
        _has_demo_flags(item.raw_payload) for item in transactions
    ):
        raise RuntimeError("Demo transaction lineage is incomplete")
    prices = list(
        (
            await session.scalars(
                select(PriceBar).where(PriceBar.source == DEMO_PRICE_SOURCE)
            )
        ).all()
    )
    fx_rates = list(
        (
            await session.scalars(
                select(FxRate).where(FxRate.source == DEMO_FX_SOURCE)
            )
        ).all()
    )
    if not prices or not all(_has_demo_flags(item.raw_payload) for item in prices):
        raise RuntimeError("Demo price lineage is incomplete")
    if not fx_rates or not all(_has_demo_flags(item.raw_payload) for item in fx_rates):
        raise RuntimeError("Demo FX lineage is incomplete")
    if not all(
        item.status == "published" and _has_demo_flags(item.metadata_json)
        for item in documents
    ):
        raise RuntimeError("Demo document lineage is incomplete")
    if not _has_demo_flags(prompt_version.baseline_metrics):
        raise RuntimeError("Demo prompt lost its provenance flags")
    if not _has_demo_flags(workflow.context_json):
        raise RuntimeError("Demo workflow lost its provenance flags")
    if (
        trace.provider != DEMO_MODEL_SOURCE
        or trace.model != "synthetic-no-network-v1"
        or not _has_demo_flags(trace.response_payload)
    ):
        raise RuntimeError("Demo trace is not explicitly synthetic/mock")
    if trace.fallback_used:
        raise RuntimeError("The intentional demo model must not be labeled as a fallback")
    if not trace.evidence_ids or not trace.retrieved_document_ids:
        raise RuntimeError("Demo trace is missing citation lineage")
    if review_task.status != "COMPLETED":
        raise RuntimeError("Demo review task is incomplete")
    decision = await _required(
        session,
        select(ReviewDecision).where(ReviewDecision.review_task_id == review_task.id),
        "review decision",
    )
    if "human_label_used=false" not in decision.feedback:
        raise RuntimeError("Demo review does not disclose its synthetic status")
    if not _has_demo_flags(report.report_json) or report.report_json.get("human_label_used"):
        raise RuntimeError("Demo report provenance is invalid")
    position_count = await _count(
        session,
        select(func.count(PositionSnapshot.id)).where(
            PositionSnapshot.valuation_snapshot_id == valuation.id
        ),
    )
    if position_count != len(_SECURITY_FIXTURES):
        raise RuntimeError("Demo position snapshot is incomplete")
    forbidden_sources = {"yfinance", "yfinance_research", "tushare", "fmp", "parqet"}
    observed_sources = {
        *(item.source.lower() for item in transactions),
        *(item.source.lower() for item in prices),
        *(item.source.lower() for item in fx_rates),
    }
    if observed_sources & forbidden_sources:
        raise RuntimeError("Demo data was mixed with a live or formal provider source")


def _has_demo_flags(payload: dict[str, Any]) -> bool:
    return all(payload.get(key) is value for key, value in DEMO_FLAGS.items())


def _security_id(key: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, f"security:{key}")


def _business_dates(start: date, end: date) -> list[date]:
    output: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            output.append(current)
        current += timedelta(days=1)
    return output


async def _required(
    session: AsyncSession,
    statement: Any,
    label: str,
) -> Any:
    value = await session.scalar(statement)
    if value is None:
        raise RuntimeError(f"Missing deterministic demo record: {label}")
    return value


async def _count(session: AsyncSession, statement: Any) -> int:
    return int((await session.scalar(statement)) or 0)
