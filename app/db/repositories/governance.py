"""Async repositories for governed research, prompts, traces and workflows."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.governance import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    LLMCallTrace,
    PromptDeployment,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    WorkflowRun,
    WorkflowStep,
)
from app.db.repositories.base import BaseRepository


def _chunk_acl(groups: frozenset[str], as_of: date) -> Any:
    group_values = sorted(groups or {"public"})
    return and_(
        DocumentChunk.published_version.is_(True),
        or_(DocumentChunk.effective_from.is_(None), DocumentChunk.effective_from <= as_of),
        or_(DocumentChunk.effective_to.is_(None), DocumentChunk.effective_to >= as_of),
        or_(
            DocumentChunk.confidentiality == "public",
            DocumentChunk.permission_groups.overlap(group_values),
        ),
    )


def _metadata_filters(
    *,
    tickers: list[str] | None = None,
    fund_codes: list[str] | None = None,
    document_types: list[str] | None = None,
    publish_date_from: date | None = None,
    publish_date_to: date | None = None,
) -> list[Any]:
    filters: list[Any] = []
    if tickers:
        filters.append(DocumentChunk.tickers.overlap([item.upper() for item in tickers]))
    if fund_codes:
        filters.append(DocumentChunk.fund_codes.overlap([item.upper() for item in fund_codes]))
    if document_types:
        aliases = {
            "announcement": ("announcement", "notice", "public_notice"),
            "research_report": ("research_report", "research", "report"),
            "policy": ("policy", "procedure"),
            "faq": ("faq",),
        }
        type_filters: list[Any] = []
        for document_type in document_types:
            values = aliases.get(document_type.lower(), (document_type.lower(),))
            type_filters.extend(
                func.lower(DocumentChunk.document_type).contains(value) for value in values
            )
        filters.append(or_(*type_filters))
    if publish_date_from:
        filters.append(DocumentChunk.publish_date >= publish_date_from)
    if publish_date_to:
        filters.append(DocumentChunk.publish_date <= publish_date_to)
    return filters


class ResearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_job(self, job_id: uuid.UUID) -> IngestionJob | None:
        return await self.session.get(IngestionJob, job_id)

    async def find_job(
        self,
        user_id: str,
        idempotency_key: str,
        *,
        business_scene: str = "knowledge_ingestion",
    ) -> IngestionJob | None:
        return await self.session.scalar(
            select(IngestionJob).where(
                IngestionJob.user_id == user_id,
                IngestionJob.business_scene == business_scene,
                IngestionJob.idempotency_key == idempotency_key,
            )
        )

    async def next_pending_job(self) -> IngestionJob | None:
        statement = (
            select(IngestionJob)
            .where(IngestionJob.status == "pending")
            .order_by(IngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def list_documents(
        self,
        groups: frozenset[str],
        *,
        is_admin: bool = False,
    ) -> list[ResearchDocument]:
        statement = select(ResearchDocument).order_by(ResearchDocument.updated_at.desc())
        if not is_admin:
            visible_ids = (
                select(DocumentChunk.document_id)
                .where(_chunk_acl(groups, date.today()))
                .distinct()
            )
            statement = statement.where(ResearchDocument.id.in_(visible_ids))
        return list((await self.session.scalars(statement)).all())

    async def get_document(
        self,
        document_id: uuid.UUID,
        groups: frozenset[str],
        *,
        is_admin: bool = False,
    ) -> ResearchDocument | None:
        statement = select(ResearchDocument).where(ResearchDocument.id == document_id)
        if not is_admin:
            visible = select(DocumentChunk.id).where(
                DocumentChunk.document_id == document_id,
                _chunk_acl(groups, date.today()),
            )
            statement = statement.where(visible.exists())
        return await self.session.scalar(statement)

    async def list_versions(self, document_id: uuid.UUID) -> list[DocumentVersion]:
        return list(
            (
                await self.session.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document_id)
                    .order_by(DocumentVersion.version.desc())
                )
            ).all()
        )

    async def find_checksum(
        self,
        checksum: str,
        *,
        document_id: uuid.UUID | None = None,
    ) -> DocumentVersion | None:
        statement = select(DocumentVersion).where(DocumentVersion.checksum == checksum)
        if document_id:
            statement = statement.where(DocumentVersion.document_id == document_id)
        return await self.session.scalar(statement.order_by(DocumentVersion.created_at.desc()).limit(1))

    async def set_published(self, document_id: uuid.UUID) -> ResearchDocument | None:
        document = await self.session.get(ResearchDocument, document_id)
        if document is None or document.current_version <= 0:
            return None
        return await self.set_published_version(document_id, document.current_version)

    async def set_published_version(
        self,
        document_id: uuid.UUID,
        version_number: int,
    ) -> ResearchDocument | None:
        document = await self.session.get(ResearchDocument, document_id)
        if document is None or version_number <= 0:
            return None
        version = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version == version_number,
            )
        )
        if version is None:
            return None
        document.status = "published"
        document.published_version = version_number
        document.published_at = func.now()
        metadata = dict(version.metadata_json or {})
        document.title = str(metadata.get("title") or document.title)
        document.source_type = str(metadata.get("source_type") or document.source_type)
        document.department = str(metadata.get("department") or document.department)
        document.author = str(metadata.get("author") or document.author)
        document.metadata_json = {
            **metadata,
            "source_checksum": version.checksum,
            "stored_content_checksum": version.stored_content_checksum,
        }
        await self.session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .values(published_version=False)
        )
        await self.session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.version_id == version.id)
            .values(published_version=True)
        )
        await self.session.flush()
        return document

    async def deactivate(self, document_id: uuid.UUID) -> ResearchDocument | None:
        document = await self.session.get(ResearchDocument, document_id)
        if document is None:
            return None
        document.status = "inactive"
        await self.session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .values(published_version=False)
        )
        await self.session.flush()
        return document

    async def full_text_search(
        self,
        query: str,
        *,
        groups: frozenset[str],
        as_of: date,
        limit: int,
        **metadata: Any,
    ) -> list[dict[str, Any]]:
        ts_query = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, ts_query).label("rank_score")
        statement = (
            select(DocumentChunk, ResearchDocument, DocumentVersion, rank)
            .join(ResearchDocument, ResearchDocument.id == DocumentChunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
            .where(
                _chunk_acl(groups, as_of),
                DocumentChunk.search_vector.op("@@")(ts_query),
                *_metadata_filters(**metadata),
            )
            .order_by(rank.desc(), DocumentChunk.id)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [_retrieval_row(chunk, document, version, float(score), "fts") for chunk, document, version, score in rows]

    async def vector_search(
        self,
        query_vector: list[float],
        *,
        model_name: str,
        model_version: str,
        groups: frozenset[str],
        as_of: date,
        limit: int,
        **metadata: Any,
    ) -> list[dict[str, Any]]:
        distance = ChunkEmbedding.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(DocumentChunk, ResearchDocument, DocumentVersion, distance)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
            .join(ResearchDocument, ResearchDocument.id == DocumentChunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
            .where(
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.model_version == model_version,
                _chunk_acl(groups, as_of),
                *_metadata_filters(**metadata),
            )
            .order_by(distance, DocumentChunk.id)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            _retrieval_row(chunk, document, version, max(0.0, 1.0 - float(distance_value)), "vector")
            for chunk, document, version, distance_value in rows
        ]

    async def cited_chunks(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        groups: frozenset[str],
        as_of: date,
    ) -> list[dict[str, Any]]:
        """Return cited chunks in trace order after validity and ACL filtering."""
        if not chunk_ids:
            return []
        statement = (
            select(DocumentChunk, ResearchDocument, DocumentVersion)
            .join(ResearchDocument, ResearchDocument.id == DocumentChunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
            .where(
                DocumentChunk.id.in_(chunk_ids),
                _chunk_acl(groups, as_of),
            )
        )
        rows = (await self.session.execute(statement)).all()
        visible = {
            chunk.id: _citation_row(chunk, document, version)
            for chunk, document, version in rows
        }
        return [visible[chunk_id] for chunk_id in chunk_ids if chunk_id in visible]


class PromptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_templates(self) -> list[PromptTemplate]:
        return list((await self.session.scalars(select(PromptTemplate).order_by(PromptTemplate.business_scene, PromptTemplate.name))).all())

    async def get_template(self, prompt_key: str) -> PromptTemplate | None:
        return await self.session.scalar(select(PromptTemplate).where(PromptTemplate.prompt_key == prompt_key))

    async def get_version(self, prompt_id: uuid.UUID, version: int) -> PromptVersion | None:
        return await self.session.scalar(select(PromptVersion).where(PromptVersion.prompt_id == prompt_id, PromptVersion.version == version))

    async def get_version_with_template(
        self, version_id: uuid.UUID
    ) -> tuple[PromptTemplate, PromptVersion] | None:
        row = (
            await self.session.execute(
                select(PromptTemplate, PromptVersion)
                .join(PromptVersion, PromptVersion.prompt_id == PromptTemplate.id)
                .where(PromptVersion.id == version_id)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def published_for_scene(self, business_scene: str) -> tuple[PromptTemplate, PromptVersion] | None:
        row = (
            await self.session.execute(
                select(PromptTemplate, PromptVersion)
                .join(
                    PromptVersion,
                    and_(
                        PromptVersion.prompt_id == PromptTemplate.id,
                        PromptVersion.version == PromptTemplate.published_version,
                    ),
                )
                .where(PromptTemplate.business_scene == business_scene, PromptTemplate.status == "published")
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row else None


class LLMTraceRepository(BaseRepository[LLMCallTrace]):
    model = LLMCallTrace

    async def list_recent(self, limit: int = 100) -> list[LLMCallTrace]:
        return list((await self.session.scalars(select(LLMCallTrace).order_by(LLMCallTrace.created_at.desc()).limit(limit))).all())

    async def latest_for_run(self, run_id: uuid.UUID) -> LLMCallTrace | None:
        return await self.session.scalar(
            select(LLMCallTrace)
            .where(LLMCallTrace.workflow_run_id == run_id)
            .order_by(LLMCallTrace.created_at.desc(), LLMCallTrace.id.desc())
            .limit(1)
        )


class WorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def idempotent_run(self, user_id: str, business_scene: str, key: str) -> WorkflowRun | None:
        return await self.session.scalar(select(WorkflowRun).where(WorkflowRun.user_id == user_id, WorkflowRun.business_scene == business_scene, WorkflowRun.idempotency_key == key))

    async def get_run(
        self, run_id: uuid.UUID, *, for_update: bool = False
    ) -> WorkflowRun | None:
        statement = select(WorkflowRun).where(WorkflowRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def latest_for_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        tenant_id: str,
    ) -> WorkflowRun | None:
        portfolio_key = str(portfolio_id)
        return await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.tenant_id == tenant_id,
                or_(
                    WorkflowRun.context_json["portfolio_id"].as_string()
                    == portfolio_key,
                    WorkflowRun.context_json["request"]["portfolio_id"].as_string()
                    == portfolio_key,
                ),
            )
            .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
            .limit(1)
        )

    async def claim_next_run(
        self,
        *,
        lease_owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> WorkflowRun | None:
        eligible = or_(
            and_(
                WorkflowRun.status.in_(["PENDING", "RETRY"]),
                or_(WorkflowRun.next_retry_at.is_(None), WorkflowRun.next_retry_at <= now),
            ),
            and_(
                WorkflowRun.status == "RUNNING",
                WorkflowRun.lease_expires_at.is_not(None),
                WorkflowRun.lease_expires_at <= now,
            ),
        )
        statement = (
            select(WorkflowRun)
            .where(eligible)
            .order_by(WorkflowRun.next_retry_at.asc().nullsfirst(), WorkflowRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        run = await self.session.scalar(statement)
        if run is None:
            return None
        run.status = "RUNNING"
        run.lease_owner = lease_owner
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
        run.next_retry_at = None
        run.attempt += 1
        if run.started_at is None:
            run.started_at = now
        await self.session.flush()
        return run

    async def get_step(
        self,
        run_id: uuid.UUID,
        step_name: str,
        iteration: int,
        *,
        for_update: bool = False,
    ) -> WorkflowStep | None:
        statement = select(WorkflowStep).where(
            WorkflowStep.workflow_run_id == run_id,
            WorkflowStep.step_name == step_name,
            WorkflowStep.iteration == iteration,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_review(self, review_id: uuid.UUID, *, for_update: bool = False) -> ReviewTask | None:
        statement = select(ReviewTask).where(ReviewTask.id == review_id)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def run_steps(self, run_id: uuid.UUID) -> list[WorkflowStep]:
        return list((await self.session.scalars(select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id).order_by(WorkflowStep.started_at))).all())

    async def run_reviews(self, run_id: uuid.UUID) -> list[ReviewTask]:
        return list((await self.session.scalars(select(ReviewTask).where(ReviewTask.workflow_run_id == run_id).order_by(ReviewTask.created_at))).all())

    async def run_review_decisions(self, run_id: uuid.UUID) -> list[ReviewDecision]:
        return list(
            (
                await self.session.scalars(
                    select(ReviewDecision)
                    .where(ReviewDecision.workflow_run_id == run_id)
                    .order_by(ReviewDecision.created_at, ReviewDecision.id)
                )
            ).all()
        )

    async def report_for_run(self, run_id: uuid.UUID) -> PublishedReport | None:
        return await self.session.scalar(select(PublishedReport).where(PublishedReport.workflow_run_id == run_id))

    async def get_report(self, report_id: uuid.UUID) -> PublishedReport | None:
        return await self.session.get(PublishedReport, report_id)


def _retrieval_row(
    chunk: DocumentChunk,
    document: ResearchDocument,
    version: DocumentVersion,
    score: float,
    channel: str,
) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(document.id),
        "document_key": document.document_key,
        "version_id": str(version.id),
        "version": version.version,
        "source_filename": version.source_filename,
        "source_type": document.source_type,
        "title": chunk.title,
        "section": chunk.section,
        "page_number": chunk.page,
        "text": chunk.text_content,
        "publish_date": chunk.publish_date.isoformat() if chunk.publish_date else None,
        "confidentiality": chunk.confidentiality,
        "permission_groups": chunk.permission_groups,
        "score": score,
        f"{channel}_score": score,
    }


def _citation_row(
    chunk: DocumentChunk,
    document: ResearchDocument,
    version: DocumentVersion,
) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(document.id),
        "document_key": document.document_key,
        "version_id": str(version.id),
        "version": version.version,
        "source_filename": version.source_filename,
        "source_type": document.source_type,
        "title": chunk.title,
        "section": chunk.section,
        "page_number": chunk.page,
        "quote": chunk.text_content,
        "publish_date": chunk.publish_date.isoformat() if chunk.publish_date else None,
        "confidentiality": chunk.confidentiality,
        "retrieval_score": None,
        "score_status": "not_recorded_in_trace",
        "validity_status": "valid",
        "permission_status": "allowed",
    }
