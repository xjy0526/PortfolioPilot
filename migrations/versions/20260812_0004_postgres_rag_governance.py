"""Add governed PostgreSQL research, prompt, trace and workflow storage.

Revision ID: 20260812_0004
Revises: 20260812_0003
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0004"
down_revision: str | None = "20260812_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TICKER_ARRAY = postgresql.ARRAY(sa.String(64))
GROUP_ARRAY = postgresql.ARRAY(sa.String(120))
TEXT_ARRAY = postgresql.ARRAY(sa.String(255))
COST = sa.Numeric(20, 8)


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "research_documents",
        *_identity_columns(),
        sa.Column("document_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("department", sa.String(160), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_research_documents"),
        sa.UniqueConstraint("document_key", name="uq_research_documents_document_key"),
    )
    op.create_table(
        "document_versions",
        *_identity_columns(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("stored_content_checksum", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("parser", sa.String(80), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["research_documents.id"],
            name="fk_document_versions_document_id_research_documents", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        sa.UniqueConstraint("document_id", "checksum", name="uq_document_versions_document_checksum"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_checksum", "document_versions", ["checksum"])

    op.create_table(
        "document_chunks",
        *_identity_columns(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("section", sa.String(500), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "search_vector", postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', coalesce(title, '') || ' ' || "
                "coalesce(section, '') || ' ' || coalesce(text_content, ''))",
                persisted=True,
            ), nullable=False,
        ),
        sa.Column("ticker", sa.String(64), nullable=True),
        sa.Column("fund_code", sa.String(64), nullable=True),
        sa.Column("tickers", TICKER_ARRAY, server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("fund_codes", TICKER_ARRAY, server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("document_type", sa.String(80), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("confidentiality", sa.String(40), nullable=False),
        sa.Column("permission_groups", GROUP_ARRAY, nullable=False),
        sa.Column("published_version", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["research_documents.id"],
            name="fk_document_chunks_document_id_research_documents", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["document_versions.id"],
            name="fk_document_chunks_version_id_document_versions", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint("version_id", "chunk_index", name="uq_document_chunks_version_index"),
    )
    for column in ("document_id", "version_id", "content_hash", "ticker", "fund_code", "document_type", "publish_date"):
        op.create_index(f"ix_document_chunks_{column}", "document_chunks", [column])
    op.create_index("ix_document_chunks_search_vector", "document_chunks", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_document_chunks_permission_groups", "document_chunks", ["permission_groups"], postgresql_using="gin")
    op.create_index("ix_document_chunks_tickers", "document_chunks", ["tickers"], postgresql_using="gin")
    op.create_index("ix_document_chunks_fund_codes", "document_chunks", ["fund_codes"], postgresql_using="gin")

    op.create_table(
        "chunk_embeddings",
        *_identity_columns(),
        sa.Column("chunk_id", UUID, nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunks.id"],
            name="fk_chunk_embeddings_chunk_id_document_chunks", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_embeddings"),
        sa.UniqueConstraint("chunk_id", "embedding_model", name="uq_chunk_embeddings_chunk_model"),
    )
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])
    op.create_index(
        "ix_chunk_embeddings_vector_hnsw", "chunk_embeddings", ["embedding"],
        postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "ingestion_jobs",
        *_identity_columns(),
        sa.Column("document_id", UUID, nullable=True),
        sa.Column("version_id", UUID, nullable=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("code_version", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("metadata_json", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("chunks_created", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["research_documents.id"],
            name="fk_ingestion_jobs_document_id_research_documents", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["document_versions.id"],
            name="fk_ingestion_jobs_version_id_document_versions", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_ingestion_jobs_user_key"),
    )
    for column in ("document_id", "version_id", "user_id", "checksum"):
        op.create_index(f"ix_ingestion_jobs_{column}", "ingestion_jobs", [column])

    op.create_table(
        "prompt_templates",
        *_identity_columns(),
        sa.Column("prompt_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("business_scene", sa.String(255), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_templates"),
        sa.UniqueConstraint("prompt_key", name="uq_prompt_templates_prompt_key"),
        sa.UniqueConstraint("business_scene", "name", name="uq_prompt_templates_scene_name"),
    )
    op.create_index("ix_prompt_templates_business_scene", "prompt_templates", ["business_scene"])
    op.create_table(
        "prompt_versions",
        *_identity_columns(),
        sa.Column("prompt_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("variables", TEXT_ARRAY, server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("input_schema", JSONB, nullable=False),
        sa.Column("output_schema", JSONB, nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("temperature", sa.Numeric(5, 4), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("change_log", sa.Text(), nullable=False),
        sa.Column("baseline_metrics", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompt_templates.id"],
            name="fk_prompt_versions_prompt_id_prompt_templates", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_versions"),
        sa.UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_version"),
    )
    op.create_index("ix_prompt_versions_prompt_id", "prompt_versions", ["prompt_id"])
    op.create_table(
        "prompt_deployments",
        *_identity_columns(),
        sa.Column("prompt_id", UUID, nullable=False),
        sa.Column("prompt_version_id", UUID, nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("environment", sa.String(80), nullable=False),
        sa.Column("deployed_by", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompt_templates.id"],
            name="fk_prompt_deployments_prompt_id_prompt_templates", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["prompt_versions.id"],
            name="fk_prompt_deployments_prompt_version_id_prompt_versions", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_deployments"),
    )
    op.create_index("ix_prompt_deployments_prompt_id", "prompt_deployments", ["prompt_id"])
    op.create_index("ix_prompt_deployments_prompt_version_id", "prompt_deployments", ["prompt_version_id"])

    op.create_table(
        "workflow_runs",
        *_identity_columns(),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("business_scene", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("node_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("cost_budget", COST, nullable=False),
        sa.Column("cost_amount", COST, nullable=False),
        sa.Column("cost_is_estimated", sa.Boolean(), nullable=False),
        sa.Column("current_step", sa.String(120), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("context_json", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(255), nullable=False),
        sa.Column("code_version", sa.String(120), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.UniqueConstraint("user_id", "business_scene", "idempotency_key", name="uq_workflow_runs_user_scene_key"),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])
    op.create_index("ix_workflow_runs_business_scene", "workflow_runs", ["business_scene"])
    op.create_table(
        "workflow_steps",
        *_identity_columns(),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("step_name", sa.String(120), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("input_summary", JSONB, nullable=False),
        sa.Column("output_summary", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"],
            name="fk_workflow_steps_workflow_run_id_workflow_runs", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_steps"),
        sa.UniqueConstraint("workflow_run_id", "step_name", "iteration", name="uq_workflow_steps_run_name_iteration"),
    )
    op.create_index("ix_workflow_steps_workflow_run_id", "workflow_steps", ["workflow_run_id"])
    op.create_table(
        "review_tasks",
        *_identity_columns(),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("assigned_group", sa.String(120), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"],
            name="fk_review_tasks_workflow_run_id_workflow_runs", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_tasks"),
    )
    op.create_index("ix_review_tasks_workflow_run_id", "review_tasks", ["workflow_run_id"])
    op.create_table(
        "review_decisions",
        *_identity_columns(),
        sa.Column("review_task_id", UUID, nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("reviewer_id", sa.String(255), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_task_id"], ["review_tasks.id"],
            name="fk_review_decisions_review_task_id_review_tasks", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"],
            name="fk_review_decisions_workflow_run_id_workflow_runs", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_decisions"),
        sa.UniqueConstraint("review_task_id", "decision", "reviewer_id", name="uq_review_decisions_task_decision_reviewer"),
    )
    op.create_index("ix_review_decisions_review_task_id", "review_decisions", ["review_task_id"])
    op.create_index("ix_review_decisions_workflow_run_id", "review_decisions", ["workflow_run_id"])
    op.create_table(
        "published_reports",
        *_identity_columns(),
        sa.Column("workflow_run_id", UUID, nullable=False),
        sa.Column("report_json", JSONB, nullable=False),
        sa.Column("published_by", sa.String(255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"],
            name="fk_published_reports_workflow_run_id_workflow_runs", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_published_reports"),
        sa.UniqueConstraint("workflow_run_id", name="uq_published_reports_workflow_run"),
    )
    op.create_index("ix_published_reports_workflow_run_id", "published_reports", ["workflow_run_id"])

    op.create_table(
        "llm_call_traces",
        *_identity_columns(),
        sa.Column("trace_key", sa.String(255), nullable=False),
        sa.Column("workflow_run_id", UUID, nullable=True),
        sa.Column("prompt_version_id", UUID, nullable=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("business_scene", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("model_parameters", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_version", sa.String(120), nullable=False),
        sa.Column("provider_usage", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("usage_source", sa.String(20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_amount", COST, nullable=True),
        sa.Column("cost_currency", sa.String(3), nullable=False),
        sa.Column("cost_source", sa.String(40), nullable=False),
        sa.Column("evidence_ids", TEXT_ARRAY, server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("retrieved_document_ids", TEXT_ARRAY, server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("tool_calls", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("output_schema_valid", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("review_decision", sa.String(40), nullable=False),
        sa.Column("review_feedback", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"],
            name="fk_llm_call_traces_workflow_run_id_workflow_runs", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"], ["prompt_versions.id"],
            name="fk_llm_call_traces_prompt_version_id_prompt_versions", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_call_traces"),
        sa.UniqueConstraint("trace_key", name="uq_llm_call_traces_trace_key"),
    )
    for column in ("workflow_run_id", "prompt_version_id", "user_id", "business_scene"):
        op.create_index(f"ix_llm_call_traces_{column}", "llm_call_traces", [column])


def downgrade() -> None:
    for table in (
        "llm_call_traces",
        "published_reports",
        "review_decisions",
        "review_tasks",
        "workflow_steps",
        "workflow_runs",
        "prompt_deployments",
        "prompt_versions",
        "prompt_templates",
        "ingestion_jobs",
        "chunk_embeddings",
        "document_chunks",
        "document_versions",
        "research_documents",
    ):
        op.drop_table(table)
