"""Add point-in-time backtest persistence.

Revision ID: 20260812_0003
Revises: 20260812_0002
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0003"
down_revision: str | None = "20260812_0002"
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
    op.create_table(
        "backtest_runs",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=True),
        sa.Column("portfolio_snapshot_id", UUID, nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_version", sa.String(length=120), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("price_source", sa.String(length=120), nullable=False),
        sa.Column("benchmark", sa.String(length=255), nullable=True),
        sa.Column("execution_convention", sa.Text(), nullable=False),
        sa.Column("cost_assumptions", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("config_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_metrics", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("mock_price_data_used", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["portfolios.id"],
            name="fk_backtest_runs_portfolio_id_portfolios", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_snapshot_id"], ["portfolio_valuation_snapshots.id"],
            name="fk_backtest_run_valuation_snapshot",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_runs"),
    )
    op.create_index("ix_backtest_runs_portfolio_id", "backtest_runs", ["portfolio_id"])
    op.create_index(
        "ix_backtest_runs_portfolio_snapshot_id", "backtest_runs", ["portfolio_snapshot_id"]
    )
    op.create_index("uq_backtest_runs_input_hash", "backtest_runs", ["input_hash"], unique=True)

    op.create_table(
        "backtest_rebalance_snapshots",
        *_identity_columns(),
        sa.Column("backtest_run_id", UUID, nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("execution_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligible_universe", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("excluded_assets", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("pre_trade_weights", JSONB, nullable=False),
        sa.Column("target_weights", JSONB, nullable=False),
        sa.Column("executed_weights", JSONB, nullable=False),
        sa.Column("post_return_weights", JSONB, nullable=True),
        sa.Column("turnover", WEIGHT, nullable=False),
        sa.Column("executed_turnover", WEIGHT, nullable=False),
        sa.Column("costs", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"], ["backtest_runs.id"],
            name="fk_backtest_rebalance_snapshots_backtest_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_rebalance_snapshots"),
        sa.UniqueConstraint(
            "backtest_run_id", "strategy", "execution_at",
            name="uq_backtest_rebalance_run_strategy_execution",
        ),
    )
    op.create_index(
        "ix_backtest_rebalance_snapshots_backtest_run_id",
        "backtest_rebalance_snapshots", ["backtest_run_id"],
    )

    op.create_table(
        "backtest_strategy_results",
        *_identity_columns(),
        sa.Column("backtest_run_id", UUID, nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("final_weights", JSONB, nullable=False),
        sa.Column("nav_series", JSONB, nullable=False),
        sa.Column("turnover", WEIGHT, nullable=False),
        sa.Column("total_costs", MONEY, nullable=False),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"], ["backtest_runs.id"],
            name="fk_backtest_strategy_results_backtest_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_strategy_results"),
        sa.UniqueConstraint(
            "backtest_run_id", "strategy", name="uq_backtest_strategy_results_run_strategy"
        ),
    )
    op.create_index(
        "ix_backtest_strategy_results_backtest_run_id",
        "backtest_strategy_results", ["backtest_run_id"],
    )


def downgrade() -> None:
    op.drop_table("backtest_strategy_results")
    op.drop_table("backtest_rebalance_snapshots")
    op.drop_index("uq_backtest_runs_input_hash", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_portfolio_snapshot_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_portfolio_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
