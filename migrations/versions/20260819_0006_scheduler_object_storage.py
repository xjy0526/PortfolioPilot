"""Add idempotent daily runs and governed object-key metadata.

Revision ID: 20260819_0006
Revises: 20260813_0005
Create Date: 2026-08-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0006"
down_revision: str | None = "20260813_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sync_runs",
        sa.Column("run_key", sa.String(255), nullable=True),
    )
    op.create_unique_constraint("uq_sync_runs_run_key", "sync_runs", ["run_key"])

    op.add_column(
        "ingestion_jobs",
        sa.Column("object_key", sa.String(1000), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("object_version", sa.String(255), server_default="", nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("object_owner", sa.String(255), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "object_permission_groups",
            postgresql.ARRAY(sa.String(120)),
            server_default=sa.text("ARRAY['public']::varchar[]"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE ingestion_jobs
        SET object_key = regexp_replace(
                storage_uri,
                '^(s3|local)://[^/]+/',
                ''
            )
        WHERE storage_uri ~ '^(s3|local)://[^/]+/.+'
        """
    )
    op.execute("UPDATE ingestion_jobs SET object_owner = user_id")
    op.execute(
        """
        UPDATE ingestion_jobs
        SET object_permission_groups = CASE
            WHEN jsonb_typeof(metadata_json->'permission_groups') = 'array'
                 AND jsonb_array_length(metadata_json->'permission_groups') > 0
            THEN ARRAY(
                SELECT lower(value)
                FROM jsonb_array_elements_text(
                    ingestion_jobs.metadata_json->'permission_groups'
                ) AS value
            )
            ELSE ARRAY['public']::varchar[]
        END
        """
    )
    op.alter_column(
        "ingestion_jobs",
        "object_owner",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_ingestion_jobs_object_key",
        "ingestion_jobs",
        ["object_key"],
    )
    op.create_index(
        "ix_ingestion_jobs_object_owner",
        "ingestion_jobs",
        ["object_owner"],
    )
    op.create_index(
        "ix_ingestion_jobs_object_permission_groups",
        "ingestion_jobs",
        ["object_permission_groups"],
        postgresql_using="gin",
    )
    op.drop_column("ingestion_jobs", "storage_path")
    op.drop_column("ingestion_jobs", "storage_uri")


def downgrade() -> None:
    # The old URI/path columns are intentionally restored as NULL. Reconstructing
    # a provider URI from an object key alone would create false lineage.
    op.add_column(
        "ingestion_jobs",
        sa.Column("storage_uri", sa.String(1200), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("storage_path", sa.String(1000), nullable=True),
    )
    op.execute("UPDATE ingestion_jobs SET storage_path = object_key")
    op.drop_index(
        "ix_ingestion_jobs_object_permission_groups",
        table_name="ingestion_jobs",
    )
    op.drop_index("ix_ingestion_jobs_object_owner", table_name="ingestion_jobs")
    op.drop_constraint(
        "uq_ingestion_jobs_object_key",
        "ingestion_jobs",
        type_="unique",
    )
    for name in (
        "object_permission_groups",
        "object_owner",
        "object_version",
        "object_key",
    ):
        op.drop_column("ingestion_jobs", name)

    op.drop_constraint("uq_sync_runs_run_key", "sync_runs", type_="unique")
    op.drop_column("sync_runs", "run_key")
