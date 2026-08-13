"""Harden identity, ingestion, workflow recovery, and valuation integrity.

Revision ID: 20260813_0005
Revises: 20260812_0004
Create Date: 2026-08-13
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
MONEY = sa.Numeric(28, 8)
WEIGHT = sa.Numeric(20, 12)


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("tenant_id", sa.String(120), server_default="default", nullable=False),
    )
    op.create_index("ix_portfolios_tenant_id", "portfolios", ["tenant_id"])
    op.create_table(
        "portfolio_memberships",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(40), server_default="viewer", nullable=False),
        sa.Column("can_read", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("can_write", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("can_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint(
            "role IN ('viewer','analyst','operator','admin')",
            name="ck_portfolio_memberships_portfolio_membership_role_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_portfolio_memberships_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_memberships"),
        sa.UniqueConstraint(
            "portfolio_id",
            "user_id",
            name="uq_portfolio_memberships_portfolio_user",
        ),
    )
    op.create_index(
        "ix_portfolio_memberships_portfolio_id",
        "portfolio_memberships",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_portfolio_memberships_user_id",
        "portfolio_memberships",
        ["user_id"],
    )
    op.execute(
        """
        INSERT INTO portfolio_memberships
            (id, portfolio_id, user_id, role, can_read, can_write, can_admin,
             created_at, updated_at)
        SELECT md5(p.id::text || ':' || p.user_id::text)::uuid,
               p.id, p.user_id::text, 'admin', TRUE, TRUE, TRUE,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM portfolios p
        ON CONFLICT (portfolio_id, user_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO portfolio_memberships
            (id, portfolio_id, user_id, role, can_read, can_write, can_admin,
             created_at, updated_at)
        SELECT md5(p.id::text || ':' || lower(u.email))::uuid,
               p.id, lower(u.email), 'admin', TRUE, TRUE, TRUE,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM portfolios p
        JOIN users u ON u.id = p.user_id
        WHERE u.email IS NOT NULL AND u.email <> ''
        ON CONFLICT (portfolio_id, user_id) DO NOTHING
        """
    )

    op.add_column(
        "chunk_embeddings",
        sa.Column("model_name", sa.String(255), server_default="legacy", nullable=False),
    )
    op.add_column(
        "chunk_embeddings",
        sa.Column("model_version", sa.String(120), server_default="legacy", nullable=False),
    )
    op.execute("UPDATE chunk_embeddings SET model_name = embedding_model")
    op.drop_constraint(
        "uq_chunk_embeddings_chunk_model", "chunk_embeddings", type_="unique"
    )
    op.create_unique_constraint(
        "uq_chunk_embeddings_chunk_model_version",
        "chunk_embeddings",
        ["chunk_id", "model_name", "model_version"],
    )

    op.drop_constraint("uq_ingestion_jobs_user_key", "ingestion_jobs", type_="unique")
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "business_scene",
            sa.String(255),
            server_default="knowledge_ingestion",
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("storage_uri", sa.String(1200), server_default="", nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("content_length", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "content_type",
            sa.String(160),
            server_default="application/octet-stream",
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE ingestion_jobs SET storage_uri = 'legacy-file://' || storage_path "
        "WHERE storage_uri = ''"
    )
    op.alter_column("ingestion_jobs", "storage_path", existing_type=sa.String(1000), nullable=True)
    op.create_unique_constraint(
        "uq_ingestion_jobs_user_scene_key",
        "ingestion_jobs",
        ["user_id", "business_scene", "idempotency_key"],
    )
    op.create_index(
        "ix_ingestion_jobs_retention_until", "ingestion_jobs", ["retention_until"]
    )

    op.add_column(
        "workflow_runs", sa.Column("lease_owner", sa.String(255), nullable=True)
    )
    op.add_column(
        "workflow_runs",
        sa.Column("tenant_id", sa.String(120), server_default="default", nullable=False),
    )
    op.add_column(
        "workflow_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "workflow_runs",
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "workflow_runs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_workflow_runs_lease_owner", "workflow_runs", ["lease_owner"])
    op.create_index("ix_workflow_runs_tenant_id", "workflow_runs", ["tenant_id"])
    op.create_index(
        "ix_workflow_runs_lease_expires_at", "workflow_runs", ["lease_expires_at"]
    )
    op.create_index("ix_workflow_runs_next_retry_at", "workflow_runs", ["next_retry_at"])
    op.add_column(
        "llm_call_traces",
        sa.Column(
            "response_payload",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "review_tasks",
        sa.Column("iteration", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY workflow_run_id ORDER BY created_at, id
                   ) AS iteration_value
            FROM review_tasks
        )
        UPDATE review_tasks rt
        SET iteration = ranked.iteration_value
        FROM ranked
        WHERE rt.id = ranked.id
        """
    )
    op.alter_column(
        "review_tasks",
        "iteration",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1",
    )
    op.create_unique_constraint(
        "uq_review_tasks_run_iteration",
        "review_tasks",
        ["workflow_run_id", "iteration"],
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM review_decisions
                GROUP BY review_task_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'review_decisions contains multiple decisions for one task; '
                    'resolve the audit conflict before migration';
            END IF;
        END $$
        """
    )
    op.create_unique_constraint(
        "uq_review_decisions_review_task", "review_decisions", ["review_task_id"]
    )

    op.execute(
        """
        UPDATE import_batches
        SET status = CASE
            WHEN accepted_rows = total_rows AND total_rows > 0 THEN 'completed'
            WHEN accepted_rows > 0 THEN 'completed_with_errors'
            ELSE 'failed'
        END
        WHERE status NOT IN (
            'pending','processing','completed','completed_with_errors','failed','duplicate'
        )
        """
    )
    op.create_check_constraint(
        "ck_import_batches_import_batch_status_allowed",
        "import_batches",
        "status IN ('pending','processing','completed','completed_with_errors','failed','duplicate')",
    )
    op.alter_column(
        "portfolio_valuation_snapshots",
        "total_market_value",
        existing_type=MONEY,
        nullable=True,
    )
    for column in ("total_cost_basis", "cash_value", "unrealized_pnl"):
        op.alter_column(
            "portfolio_valuation_snapshots", column, existing_type=MONEY, nullable=True
        )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("priced_market_value", MONEY, server_default="0", nullable=False),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("valuation_status", sa.String(40), server_default="complete", nullable=False),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("priced_asset_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("unpriced_asset_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column(
            "unpriced_assets", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("data_as_of_earliest", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("data_as_of_latest", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("max_staleness_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "portfolio_valuation_snapshots",
        sa.Column("coverage_ratio", WEIGHT, server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_portfolio_valuation_snapshots_valuation_status_allowed",
        "portfolio_valuation_snapshots",
        "valuation_status IN ('complete','partial','unavailable')",
    )
    op.create_check_constraint(
        "ck_portfolio_valuation_snapshots_valuation_coverage_ratio_range",
        "portfolio_valuation_snapshots",
        "coverage_ratio >= 0 AND coverage_ratio <= 1",
    )
    op.create_check_constraint(
        "ck_portfolio_valuation_snapshots_valuation_asset_counts_nonnegative",
        "portfolio_valuation_snapshots",
        "priced_asset_count >= 0 AND unpriced_asset_count >= 0",
    )
    op.execute(
        """
        UPDATE portfolio_valuation_snapshots
        SET priced_market_value = total_market_value,
            data_as_of_earliest = data_as_of,
            data_as_of_latest = data_as_of,
            max_staleness_days = 0
        """
    )
    for name in (
        "cost_basis_native",
        "cost_basis_base_at_trade",
        "local_price_pnl",
        "fx_pnl",
        "total_pnl_base",
    ):
        op.add_column("position_snapshots", sa.Column(name, MONEY, nullable=True))
    op.execute(
        """
        UPDATE position_snapshots
        SET cost_basis_base_at_trade = cost_basis_base,
            total_pnl_base = unrealized_pnl_base,
            local_price_pnl = unrealized_pnl_base,
            fx_pnl = 0
        """
    )


def downgrade() -> None:
    for name in (
        "total_pnl_base",
        "fx_pnl",
        "local_price_pnl",
        "cost_basis_base_at_trade",
        "cost_basis_native",
    ):
        op.drop_column("position_snapshots", name)

    for constraint_name in (
        "ck_portfolio_valuation_snapshots_valuation_asset_counts_nonnegative",
        "ck_portfolio_valuation_snapshots_valuation_coverage_ratio_range",
        "ck_portfolio_valuation_snapshots_valuation_status_allowed",
    ):
        op.drop_constraint(
            constraint_name,
            "portfolio_valuation_snapshots",
            type_="check",
        )

    op.execute(
        """
        UPDATE portfolio_valuation_snapshots
        SET total_market_value = COALESCE(total_market_value, priced_market_value, 0),
            total_cost_basis = COALESCE(total_cost_basis, 0),
            cash_value = COALESCE(cash_value, 0),
            unrealized_pnl = COALESCE(unrealized_pnl, 0)
        """
    )
    for column in ("total_market_value", "total_cost_basis", "cash_value", "unrealized_pnl"):
        op.alter_column(
            "portfolio_valuation_snapshots", column, existing_type=MONEY, nullable=False
        )
    for name in (
        "coverage_ratio",
        "max_staleness_days",
        "data_as_of_latest",
        "data_as_of_earliest",
        "unpriced_assets",
        "unpriced_asset_count",
        "priced_asset_count",
        "valuation_status",
        "priced_market_value",
    ):
        op.drop_column("portfolio_valuation_snapshots", name)
    op.drop_constraint(
        "ck_import_batches_import_batch_status_allowed", "import_batches", type_="check"
    )

    op.drop_constraint("uq_review_decisions_review_task", "review_decisions", type_="unique")
    op.drop_constraint("uq_review_tasks_run_iteration", "review_tasks", type_="unique")
    op.drop_column("review_tasks", "iteration")
    op.drop_column("llm_call_traces", "response_payload")
    op.drop_index("ix_workflow_runs_next_retry_at", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_lease_expires_at", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_lease_owner", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_tenant_id", table_name="workflow_runs")
    for name in (
        "next_retry_at",
        "attempt",
        "lease_expires_at",
        "heartbeat_at",
        "lease_owner",
        "tenant_id",
    ):
        op.drop_column("workflow_runs", name)

    op.drop_index("ix_ingestion_jobs_retention_until", table_name="ingestion_jobs")
    op.drop_constraint("uq_ingestion_jobs_user_scene_key", "ingestion_jobs", type_="unique")
    op.execute(
        "UPDATE ingestion_jobs SET storage_path = COALESCE(storage_path, storage_uri)"
    )
    op.alter_column("ingestion_jobs", "storage_path", existing_type=sa.String(1000), nullable=False)
    for name in (
        "retention_until",
        "content_type",
        "content_length",
        "storage_uri",
        "business_scene",
    ):
        op.drop_column("ingestion_jobs", name)
    op.create_unique_constraint(
        "uq_ingestion_jobs_user_key", "ingestion_jobs", ["user_id", "idempotency_key"]
    )

    op.drop_constraint(
        "uq_chunk_embeddings_chunk_model_version", "chunk_embeddings", type_="unique"
    )
    op.create_unique_constraint(
        "uq_chunk_embeddings_chunk_model",
        "chunk_embeddings",
        ["chunk_id", "embedding_model"],
    )
    op.drop_column("chunk_embeddings", "model_version")
    op.drop_column("chunk_embeddings", "model_name")

    op.drop_table("portfolio_memberships")
    op.drop_index("ix_portfolios_tenant_id", table_name="portfolios")
    op.drop_column("portfolios", "tenant_id")
