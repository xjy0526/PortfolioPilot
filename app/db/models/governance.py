"""Governed research, prompt, LLM trace and workflow persistence models."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, UUIDTimestampMixin

RAG_VECTOR_DIMENSIONS = 384


class ResearchDocument(UUIDTimestampMixin, Base):
    __tablename__ = "research_documents"
    __table_args__ = (UniqueConstraint("document_key", name="uq_research_documents_document_key"),)

    document_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, default="uploaded")
    department: Mapped[str] = mapped_column(String(160), nullable=False, default="Research")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class DocumentVersion(UUIDTimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        UniqueConstraint("document_id", "checksum", name="uq_document_versions_document_checksum"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stored_content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    parser: Mapped[str] = mapped_column(String(80), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="processing")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DocumentChunk(UUIDTimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "chunk_index", name="uq_document_chunks_version_index"),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_document_chunks_permission_groups", "permission_groups", postgresql_using="gin"),
        Index("ix_document_chunks_tickers", "tickers", postgresql_using="gin"),
        Index("ix_document_chunks_fund_codes", "fund_codes", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    section: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || "
            "coalesce(section, '') || ' ' || coalesce(text_content, ''))",
            persisted=True,
        ),
        nullable=False,
    )
    ticker: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    fund_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tickers: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    fund_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    document_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    confidentiality: Mapped[str] = mapped_column(String(40), nullable=False, default="public")
    permission_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=lambda: ["public"]
    )
    published_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class ChunkEmbedding(UUIDTimestampMixin, Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "model_name",
            "model_version",
            name="uq_chunk_embeddings_chunk_model_version",
        ),
        Index(
            "ix_chunk_embeddings_vector_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="legacy"
    )
    model_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default="legacy"
    )
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(RAG_VECTOR_DIMENSIONS), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class IngestionJob(UUIDTimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "business_scene",
            "idempotency_key",
            name="uq_ingestion_jobs_user_scene_key",
        ),
        UniqueConstraint("object_key", name="uq_ingestion_jobs_object_key"),
        Index(
            "ix_ingestion_jobs_object_permission_groups",
            "object_permission_groups",
            postgresql_using="gin",
        ),
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    business_scene: Mapped[str] = mapped_column(
        String(255), nullable=False, default="knowledge_ingestion"
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    object_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    object_owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    object_permission_groups: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=lambda: ["public"]
    )
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_type: Mapped[str] = mapped_column(
        String(160), nullable=False, default="application/octet-stream"
    )
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    code_version: Mapped[str] = mapped_column(String(120), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PromptTemplate(UUIDTimestampMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("prompt_key", name="uq_prompt_templates_prompt_key"),
        UniqueConstraint("business_scene", "name", name="uq_prompt_templates_scene_name"),
    )

    prompt_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_scene: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromptVersion(UUIDTimestampMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_version"),
    )

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    change_log: Mapped[str] = mapped_column(Text, nullable=False, default="")
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromptDeployment(UUIDTimestampMixin, Base):
    __tablename__ = "prompt_deployments"

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(80), nullable=False, default="production")
    deployed_by: Mapped[str] = mapped_column(String(255), nullable=False)


class LLMCallTrace(UUIDTimestampMixin, Base):
    __tablename__ = "llm_call_traces"
    __table_args__ = (UniqueConstraint("trace_key", name="uq_llm_call_traces_trace_key"),)

    trace_key: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    business_scene: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    model_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_version: Mapped[str] = mapped_column(String(120), nullable=False, default="unknown")
    provider_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    usage_source: Mapped[str] = mapped_column(String(20), nullable=False, default="estimated")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="UNK")
    cost_source: Mapped[str] = mapped_column(String(40), nullable=False, default="estimated_from_text")
    evidence_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    retrieved_document_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    output_schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_decision: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    review_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class WorkflowRun(UUIDTimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "business_scene", "idempotency_key", name="uq_workflow_runs_user_scene_key"
        ),
    )

    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default="default", index=True
    )
    business_scene: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    node_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    cost_budget: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    cost_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_step: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    code_version: Mapped[str] = mapped_column(String(120), nullable=False, default="unknown")
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class WorkflowStep(UUIDTimestampMixin, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_name", "iteration", name="uq_workflow_steps_run_name_iteration"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class ReviewTask(UUIDTimestampMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "iteration",
            name="uq_review_tasks_run_iteration",
        ),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    assigned_group: Mapped[str] = mapped_column(String(120), nullable=False, default="research_reviewer")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewDecision(UUIDTimestampMixin, Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("review_task_id", "decision", "reviewer_id", name="uq_review_decisions_task_decision_reviewer"),
        UniqueConstraint("review_task_id", name="uq_review_decisions_review_task"),
    )

    review_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PublishedReport(UUIDTimestampMixin, Base):
    __tablename__ = "published_reports"
    __table_args__ = (UniqueConstraint("workflow_run_id", name="uq_published_reports_workflow_run"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
