"""Seed and verify the business dataset used by PostgreSQL restart E2E."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    LLMCallTrace,
    Portfolio,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    PriceBar,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    Transaction,
    User,
    WorkflowRun,
)
from app.db.repositories import (
    PortfolioRepository,
    PriceBarRepository,
    SecurityRepository,
    UserRepository,
)
from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.providers.embeddings import HashingEmbeddingProvider
from app.services.portfolio_valuation import PortfolioValuationService
from app.services.research_knowledge import PostgresKnowledgeService
from app.services.transaction_ledger import TransactionLedgerService
from app.storage import build_object_storage

AS_OF = datetime(2026, 8, 19, 12, tzinfo=UTC)
SOURCE = "postgres_restart_e2e"


def _scenario_checksum(namespace: str) -> str:
    payload = {
        "namespace": namespace,
        "as_of": AS_OF.isoformat(),
        "deposit": "2000.00 USD",
        "buy": "10 @ 100.00 USD",
        "close": "120.00 USD",
        "document": "Deterministic restart persistence evidence.",
        "workflow_decision": "approved",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _embedding_checksum(values: list[float]) -> str:
    normalized = [round(float(value), 8) for value in values]
    encoded = json.dumps(normalized, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _transaction(
    portfolio_id: uuid.UUID,
    security_id: uuid.UUID | None,
    *,
    transaction_type: str,
    quantity: str,
    price: str | None,
    gross_amount: str,
    external_id: str,
) -> Transaction:
    return Transaction(
        portfolio_id=portfolio_id,
        security_id=security_id,
        transaction_type=transaction_type,
        occurred_at=AS_OF - timedelta(days=2),
        quantity=Decimal(quantity),
        price=Decimal(price) if price is not None else None,
        gross_amount=Decimal(gross_amount),
        fees=Decimal("0"),
        taxes=Decimal("0"),
        currency="USD",
        source=SOURCE,
        external_id=external_id,
        note="isolated PostgreSQL restart E2E fixture",
        raw_payload={"namespace": external_id.rsplit(":", maxsplit=1)[0]},
    )


async def _ensure_portfolio_data(
    session: AsyncSession,
    namespace: str,
) -> None:
    user = await UserRepository(session).get_or_create(
        email=f"restart-{namespace}@example.invalid"
    )
    portfolio = await PortfolioRepository(session).get_or_create(
        user_id=user.id,
        name=f"Restart E2E {namespace}",
        base_currency="USD",
    )
    security = await SecurityRepository(session).get_or_create(
        canonical_symbol=f"E2E-{namespace}"[:80],
        exchange="TEST",
        market="US",
        currency="USD",
        name="Restart Persistence Security",
        sector="Technology",
        country="US",
        security_metadata={"namespace": namespace},
    )
    ledger = TransactionLedgerService(session)
    await ledger.add_transaction(
        _transaction(
            portfolio.id,
            None,
            transaction_type="deposit",
            quantity="0",
            price=None,
            gross_amount="2000",
            external_id=f"{namespace}:deposit",
        )
    )
    await ledger.add_transaction(
        _transaction(
            portfolio.id,
            security.id,
            transaction_type="buy",
            quantity="10",
            price="100",
            gross_amount="1000",
            external_id=f"{namespace}:buy",
        )
    )
    await PriceBarRepository(session).upsert(
        PriceBar(
            security_id=security.id,
            trade_date=(AS_OF - timedelta(days=1)).date(),
            source="yfinance_research",
            currency="USD",
            open=Decimal("118"),
            high=Decimal("121"),
            low=Decimal("117"),
            close=Decimal("120"),
            adjusted_close=Decimal("120"),
            adjustment_factor=Decimal("1"),
            volume=Decimal("1000"),
            data_as_of=AS_OF - timedelta(minutes=1),
            is_final=True,
            quality_status="valid",
            raw_payload={"namespace": namespace, "research_only": True},
        )
    )
    await PortfolioValuationService(session).value(
        portfolio_id=portfolio.id,
        as_of=AS_OF,
        source=SOURCE,
    )


async def _ensure_research_data(session: AsyncSession, namespace: str) -> None:
    principal = Principal(
        user_id=f"restart-admin-{namespace}",
        permission_groups=frozenset({"public"}),
        roles=frozenset({"knowledge_admin"}),
    )
    content = (
        "# Restart persistence evidence\n\n"
        f"Namespace {namespace}. Deterministic restart persistence evidence."
    ).encode()
    service = PostgresKnowledgeService(
        session,
        embedder=HashingEmbeddingProvider(384),
        storage=build_object_storage(),
    )
    job, _ = await service.queue_upload(
        content=content,
        filename=f"restart-{namespace}.md",
        metadata={
            "document_id": f"restart-e2e-{namespace}",
            "title": "PostgreSQL Restart Persistence Evidence",
            "source_type": "research_report",
            "tickers": [f"E2E-{namespace}"[:80]],
            "publish_date": AS_OF.date().isoformat(),
            "confidentiality": "public",
            "permission_groups": ["public"],
        },
        principal=principal,
        idempotency_key=f"restart-e2e-{namespace}",
    )
    if job.status in {"pending", "processing", "failed"}:
        await service.process_job(job)
    if job.document_id is None:
        raise AssertionError("research ingestion did not create a document")
    document = await service.repository.set_published(job.document_id)
    if document is None:
        raise AssertionError("research document could not be published")


async def _ensure_governance_data(
    session: AsyncSession,
    namespace: str,
) -> None:
    checksum = _scenario_checksum(namespace)
    prompt_key = f"restart-e2e-{namespace}"
    prompt = await session.scalar(
        select(PromptTemplate).where(PromptTemplate.prompt_key == prompt_key)
    )
    if prompt is None:
        prompt = PromptTemplate(
            prompt_key=prompt_key,
            name=f"Restart E2E {namespace}",
            business_scene=SOURCE,
            owner=f"restart-owner-{namespace}",
            status="published",
            current_version=1,
            published_version=1,
            published_at=AS_OF,
        )
        session.add(prompt)
        await session.flush()

    prompt_version = await session.scalar(
        select(PromptVersion).where(
            PromptVersion.prompt_id == prompt.id,
            PromptVersion.version == 1,
        )
    )
    if prompt_version is None:
        prompt_version = PromptVersion(
            prompt_id=prompt.id,
            version=1,
            template="Explain persisted evidence for {namespace}.",
            variables=["namespace"],
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            model="deterministic-e2e",
            temperature=Decimal("0"),
            owner=f"restart-owner-{namespace}",
            status="published",
            change_log="PostgreSQL restart E2E fixture",
            baseline_metrics={"evaluation_mode": "synthetic_smoke"},
            published_at=AS_OF,
        )
        session.add(prompt_version)
        await session.flush()

    workflow = await session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.user_id == f"restart-user-{namespace}",
            WorkflowRun.business_scene == SOURCE,
            WorkflowRun.idempotency_key == namespace,
        )
    )
    if workflow is None:
        workflow = WorkflowRun(
            user_id=f"restart-user-{namespace}",
            tenant_id=f"restart-tenant-{namespace}",
            business_scene=SOURCE,
            idempotency_key=namespace,
            status="PUBLISHED",
            max_steps=10,
            timeout_seconds=60,
            node_timeout_seconds=10,
            cost_budget=Decimal("1"),
            cost_amount=Decimal("0"),
            cost_is_estimated=True,
            current_step="publish_report",
            iteration=1,
            context_json={"namespace": namespace, "scenario_checksum": checksum},
            started_at=AS_OF,
            completed_at=AS_OF,
            code_version="postgres-restart-e2e",
        )
        session.add(workflow)
        await session.flush()

    trace = await session.scalar(
        select(LLMCallTrace).where(
            LLMCallTrace.trace_key == f"restart-e2e:{namespace}:trace"
        )
    )
    if trace is None:
        trace = LLMCallTrace(
            trace_key=f"restart-e2e:{namespace}:trace",
            workflow_run_id=workflow.id,
            prompt_version_id=prompt_version.id,
            user_id=workflow.user_id,
            business_scene=SOURCE,
            provider="deterministic_e2e",
            model="no-network-model",
            model_parameters={"temperature": 0},
            request_hash=checksum,
            response_hash=hashlib.sha256(f"response:{namespace}".encode()).hexdigest(),
            response_payload={"namespace": namespace, "persisted": True},
            status="success",
            duration_ms=1,
            data_as_of=AS_OF,
            code_version="postgres-restart-e2e",
            provider_usage={},
            usage_source="estimated",
            cost_currency="USD",
            cost_source="estimated_from_text",
            evidence_ids=[f"restart-e2e-{namespace}"],
            retrieved_document_ids=[f"restart-e2e-{namespace}"],
            tool_calls=[],
            output_schema_valid=True,
            fallback_used=False,
            review_decision="approved",
            review_feedback="deterministic E2E review",
        )
        session.add(trace)

    review_task = await session.scalar(
        select(ReviewTask).where(
            ReviewTask.workflow_run_id == workflow.id,
            ReviewTask.iteration == 1,
        )
    )
    if review_task is None:
        review_task = ReviewTask(
            workflow_run_id=workflow.id,
            iteration=1,
            status="COMPLETED",
            assigned_group="research_reviewer",
            completed_at=AS_OF,
        )
        session.add(review_task)
        await session.flush()

    decision = await session.scalar(
        select(ReviewDecision).where(ReviewDecision.review_task_id == review_task.id)
    )
    if decision is None:
        session.add(
            ReviewDecision(
                review_task_id=review_task.id,
                workflow_run_id=workflow.id,
                decision="approved",
                reviewer_id=f"restart-reviewer-{namespace}",
                feedback="Persistence E2E approval fixture",
            )
        )

    report = await session.scalar(
        select(PublishedReport).where(PublishedReport.workflow_run_id == workflow.id)
    )
    if report is None:
        session.add(
            PublishedReport(
                workflow_run_id=workflow.id,
                report_json={
                    "namespace": namespace,
                    "scenario_checksum": checksum,
                    "disclaimer": "Research system E2E fixture; not investment advice.",
                },
                published_by=f"restart-reviewer-{namespace}",
                published_at=AS_OF,
            )
        )
    await session.flush()


async def _ensure_scenario(session: AsyncSession, namespace: str) -> dict[str, Any]:
    await _ensure_portfolio_data(session, namespace)
    await _ensure_research_data(session, namespace)
    await _ensure_governance_data(session, namespace)
    return await _load_and_validate(session, namespace)


async def _required(session: AsyncSession, statement: Any, label: str) -> Any:
    value = await session.scalar(statement)
    if value is None:
        raise AssertionError(f"missing persisted E2E record: {label}")
    return value


async def _count(session: AsyncSession, statement: Any) -> int:
    return int((await session.scalar(statement)) or 0)


async def _load_and_validate(session: AsyncSession, namespace: str) -> dict[str, Any]:
    checksum = _scenario_checksum(namespace)
    user = await _required(
        session,
        select(User).where(User.email == f"restart-{namespace}@example.invalid"),
        "user",
    )
    portfolio = await _required(
        session,
        select(Portfolio).where(
            Portfolio.user_id == user.id,
            Portfolio.name == f"Restart E2E {namespace}",
        ),
        "portfolio",
    )
    transaction = await _required(
        session,
        select(Transaction).where(
            Transaction.portfolio_id == portfolio.id,
            Transaction.source == SOURCE,
            Transaction.external_id == f"{namespace}:buy",
        ),
        "portfolio transaction",
    )
    valuation = await _required(
        session,
        select(PortfolioValuationSnapshot).where(
            PortfolioValuationSnapshot.portfolio_id == portfolio.id,
            PortfolioValuationSnapshot.as_of == AS_OF,
            PortfolioValuationSnapshot.source == SOURCE,
        ),
        "valuation snapshot",
    )
    position = await _required(
        session,
        select(PositionSnapshot).where(
            PositionSnapshot.valuation_snapshot_id == valuation.id
        ),
        "rebuilt position",
    )
    rebuilt = await TransactionLedgerService(session).rebuild(
        portfolio.id,
        as_of=AS_OF,
    )

    document = await _required(
        session,
        select(ResearchDocument).where(
            ResearchDocument.document_key == f"restart-e2e-{namespace}"
        ),
        "research document",
    )
    version = await _required(
        session,
        select(DocumentVersion).where(DocumentVersion.document_id == document.id),
        "document version",
    )
    chunk = await _required(
        session,
        select(DocumentChunk).where(DocumentChunk.document_id == document.id),
        "document chunk",
    )
    embedding = await _required(
        session,
        select(ChunkEmbedding).where(ChunkEmbedding.chunk_id == chunk.id),
        "chunk embedding",
    )
    ingestion_job = await _required(
        session,
        select(IngestionJob).where(
            IngestionJob.user_id == f"restart-admin-{namespace}",
            IngestionJob.business_scene == "knowledge_ingestion",
            IngestionJob.idempotency_key == f"restart-e2e-{namespace}",
        ),
        "ingestion job",
    )

    prompt = await _required(
        session,
        select(PromptTemplate).where(
            PromptTemplate.prompt_key == f"restart-e2e-{namespace}"
        ),
        "prompt template",
    )
    prompt_version = await _required(
        session,
        select(PromptVersion).where(
            PromptVersion.prompt_id == prompt.id,
            PromptVersion.version == 1,
        ),
        "prompt version",
    )
    workflow = await _required(
        session,
        select(WorkflowRun).where(
            WorkflowRun.user_id == f"restart-user-{namespace}",
            WorkflowRun.business_scene == SOURCE,
            WorkflowRun.idempotency_key == namespace,
        ),
        "workflow run",
    )
    trace = await _required(
        session,
        select(LLMCallTrace).where(
            LLMCallTrace.trace_key == f"restart-e2e:{namespace}:trace"
        ),
        "LLM trace",
    )
    review_task = await _required(
        session,
        select(ReviewTask).where(ReviewTask.workflow_run_id == workflow.id),
        "review task",
    )
    review_decision = await _required(
        session,
        select(ReviewDecision).where(
            ReviewDecision.review_task_id == review_task.id
        ),
        "review decision",
    )
    report = await _required(
        session,
        select(PublishedReport).where(PublishedReport.workflow_run_id == workflow.id),
        "published report",
    )

    if transaction.quantity != Decimal("10") or transaction.gross_amount != Decimal("1000"):
        raise AssertionError("portfolio transaction values changed")
    if len(rebuilt.positions) != 1 or rebuilt.positions[0].quantity != Decimal("10"):
        raise AssertionError("ledger rebuild does not reproduce the expected position")
    if position.quantity != Decimal("10") or position.market_value_base != Decimal("1200"):
        raise AssertionError("persisted position values changed")
    if valuation.total_market_value != Decimal("2200") or valuation.input_hash == "":
        raise AssertionError("valuation snapshot values or lineage changed")
    if document.status != "published" or chunk.published_version is not True:
        raise AssertionError("research publication state changed")
    if (
        embedding.dimensions != 384
        or len(embedding.embedding) != 384
        or embedding.content_hash != chunk.content_hash
    ):
        raise AssertionError("embedding lineage changed")
    if prompt_version.status != "published":
        raise AssertionError("prompt version is no longer published")
    if trace.workflow_run_id != workflow.id or trace.prompt_version_id != prompt_version.id:
        raise AssertionError("LLM trace lineage changed")
    if review_decision.decision != "approved":
        raise AssertionError("human review decision changed")
    if report.report_json.get("scenario_checksum") != checksum:
        raise AssertionError("published report checksum changed")
    if not ingestion_job.object_key:
        raise AssertionError("ingestion object key is missing")
    source_content = await build_object_storage().get_object(
        ingestion_job.object_key,
        version=ingestion_job.object_version,
    )
    if hashlib.sha256(source_content).hexdigest() != ingestion_job.checksum:
        raise AssertionError("stored research source checksum changed")

    counts = {
        "portfolio_transactions": await _count(
            session,
            select(func.count(Transaction.id)).where(
                Transaction.portfolio_id == portfolio.id,
                Transaction.source == SOURCE,
            ),
        ),
        "rebuilt_positions": await _count(
            session,
            select(func.count(PositionSnapshot.id)).where(
                PositionSnapshot.valuation_snapshot_id == valuation.id
            ),
        ),
        "valuation_snapshots": await _count(
            session,
            select(func.count(PortfolioValuationSnapshot.id)).where(
                PortfolioValuationSnapshot.portfolio_id == portfolio.id,
                PortfolioValuationSnapshot.source == SOURCE,
            ),
        ),
        "document_versions": await _count(
            session,
            select(func.count(DocumentVersion.id)).where(
                DocumentVersion.document_id == document.id
            ),
        ),
        "document_chunks": await _count(
            session,
            select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id),
        ),
        "embeddings": await _count(
            session,
            select(func.count(ChunkEmbedding.id)).where(
                ChunkEmbedding.chunk_id == chunk.id
            ),
        ),
        "prompt_versions": await _count(
            session,
            select(func.count(PromptVersion.id)).where(PromptVersion.prompt_id == prompt.id),
        ),
        "llm_traces": await _count(
            session,
            select(func.count(LLMCallTrace.id)).where(
                LLMCallTrace.trace_key == trace.trace_key
            ),
        ),
        "workflow_runs": await _count(
            session,
            select(func.count(WorkflowRun.id)).where(
                WorkflowRun.user_id == workflow.user_id,
                WorkflowRun.business_scene == SOURCE,
                WorkflowRun.idempotency_key == namespace,
            ),
        ),
        "review_tasks": await _count(
            session,
            select(func.count(ReviewTask.id)).where(
                ReviewTask.workflow_run_id == workflow.id
            ),
        ),
        "review_decisions": await _count(
            session,
            select(func.count(ReviewDecision.id)).where(
                ReviewDecision.workflow_run_id == workflow.id
            ),
        ),
        "published_reports": await _count(
            session,
            select(func.count(PublishedReport.id)).where(
                PublishedReport.workflow_run_id == workflow.id
            ),
        ),
    }
    expected_counts = {
        "portfolio_transactions": 2,
        "rebuilt_positions": 1,
        "valuation_snapshots": 1,
        "document_versions": 1,
        "document_chunks": 1,
        "embeddings": 1,
        "prompt_versions": 1,
        "llm_traces": 1,
        "workflow_runs": 1,
        "review_tasks": 1,
        "review_decisions": 1,
        "published_reports": 1,
    }
    if counts != expected_counts:
        raise AssertionError(f"restart E2E duplicate or missing rows: {counts}")

    return {
        "namespace": namespace,
        "scenario_checksum": checksum,
        "document_checksum": ingestion_job.checksum,
        "embedding_checksum": _embedding_checksum(embedding.embedding),
        "valuation_input_hash": valuation.input_hash,
        "ids": {
            "user": str(user.id),
            "portfolio": str(portfolio.id),
            "transaction": str(transaction.id),
            "valuation_snapshot": str(valuation.id),
            "position_snapshot": str(position.id),
            "research_document": str(document.id),
            "document_chunk": str(chunk.id),
            "embedding": str(embedding.id),
            "prompt_version": str(prompt_version.id),
            "llm_trace": str(trace.id),
            "workflow_run": str(workflow.id),
            "review_decision": str(review_decision.id),
            "published_report": str(report.id),
        },
        "counts": counts,
    }


async def _run(action: str, namespace: str) -> dict[str, Any]:
    try:
        if action == "seed":
            async with AsyncSessionFactory.begin() as session:
                manifest = await _ensure_scenario(session, namespace)
            return {"phase": "seed", "manifest": manifest}

        async with AsyncSessionFactory() as session:
            before = await _load_and_validate(session, namespace)
        async with AsyncSessionFactory.begin() as session:
            after = await _ensure_scenario(session, namespace)
        if before != after:
            raise AssertionError("idempotent replay changed IDs, checksums, hashes, or counts")
        return {
            "phase": "verify",
            "manifest": before,
            "idempotent_replay_verified": True,
        }
    finally:
        await dispose_async_engine()


def _namespace(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", normalized):
        raise argparse.ArgumentTypeError(
            "namespace must be 1-48 lowercase letters, digits, or hyphens"
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("--namespace", required=True, type=_namespace)
    args = parser.parse_args()
    result = asyncio.run(_run(args.action, args.namespace))
    print("E2E_MANIFEST=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
