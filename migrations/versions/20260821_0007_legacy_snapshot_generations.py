"""Add auditable generations for replaceable legacy dashboard snapshots.

Revision ID: 20260821_0007
Revises: 20260819_0006
Create Date: 2026-08-21
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260821_0007"
down_revision: str | None = "20260819_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "legacy_snapshot_generations",
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("import_batch_id", UUID, nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", UUID, nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column(
            "generation_metadata",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "generation_number > 0",
            name="generation_number_positive",
        ),
        sa.CheckConstraint(
            "position_count >= 0",
            name="position_count_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('active','superseded')",
            name="generation_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.id"],
            name="fk_legacy_snapshot_generations_import_batch_id_import_batches",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_legacy_snapshot_generations_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["legacy_snapshot_generations.id"],
            name="fk_legacy_snapshot_generations_superseded_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_legacy_snapshot_generations"),
        sa.UniqueConstraint(
            "portfolio_id",
            "source",
            "generation_number",
            name="uq_legacy_snapshot_generation_number",
        ),
    )
    op.create_index(
        "ix_legacy_snapshot_generations_portfolio_id",
        "legacy_snapshot_generations",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_legacy_snapshot_generations_import_batch_id",
        "legacy_snapshot_generations",
        ["import_batch_id"],
    )
    op.create_index(
        "ix_legacy_snapshot_generations_superseded_by_id",
        "legacy_snapshot_generations",
        ["superseded_by_id"],
    )
    op.create_index(
        "uq_legacy_snapshot_generation_active",
        "legacy_snapshot_generations",
        ["portfolio_id", "source"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # Existing successful dashboard imports were additive. Preserve every batch
    # for audit, but make only the latest one effective after migration.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id AS import_batch_id,
                portfolio_id,
                source,
                accepted_rows,
                created_at,
                updated_at,
                row_number() OVER (
                    PARTITION BY portfolio_id, source
                    ORDER BY created_at, id
                ) AS generation_number,
                count(*) OVER (
                    PARTITION BY portfolio_id, source
                ) AS generation_count
            FROM import_batches
            WHERE source = 'legacy_dashboard_csv'
              AND status IN ('completed', 'completed_with_errors')
        ), prepared AS (
            SELECT
                md5(import_batch_id::text || '-legacy_snapshot_generation')::uuid AS id,
                *,
                created_at AS activated_at
            FROM ranked
        ), linked AS (
            SELECT
                *,
                lead(id) OVER (
                    PARTITION BY portfolio_id, source
                    ORDER BY generation_number
                ) AS next_generation_id,
                lead(activated_at) OVER (
                    PARTITION BY portfolio_id, source
                    ORDER BY generation_number
                ) AS next_activated_at
            FROM prepared
        )
        INSERT INTO legacy_snapshot_generations (
            id,
            portfolio_id,
            import_batch_id,
            source,
            generation_number,
            status,
            activated_at,
            superseded_at,
            superseded_by_id,
            position_count,
            generation_metadata,
            created_at,
            updated_at
        )
        SELECT
            id,
            portfolio_id,
            import_batch_id,
            source,
            generation_number,
            CASE
                WHEN generation_number = generation_count THEN 'active'
                ELSE 'superseded'
            END,
            activated_at,
            next_activated_at,
            next_generation_id,
            accepted_rows,
            jsonb_build_object('backfilled_by', '20260821_0007'),
            created_at,
            COALESCE(next_activated_at, updated_at)
        FROM linked
        """
    )


def downgrade() -> None:
    op.drop_table("legacy_snapshot_generations")
