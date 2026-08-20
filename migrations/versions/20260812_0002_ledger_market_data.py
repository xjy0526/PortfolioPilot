"""Harden the ledger and add traceable market-data valuation snapshots.

Revision ID: 20260812_0002
Revises: 20260811_0001
Create Date: 2026-08-12
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0002"
down_revision: str | None = "20260811_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
MONEY = sa.Numeric(28, 8)
PRICE = sa.Numeric(28, 10)
QUANTITY = sa.Numeric(38, 12)
RATE = sa.Numeric(28, 12)


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
        sa.Column(
            "cost_basis_method",
            sa.String(length=40),
            server_default="weighted_average",
            nullable=False,
        ),
    )
    op.add_column(
        "portfolios",
        sa.Column(
            "display_timezone",
            sa.String(length=80),
            server_default="Asia/Shanghai",
            nullable=False,
        ),
    )
    op.add_column("portfolios", sa.Column("benchmark_security_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_portfolios_benchmark_security_id_securities",
        "portfolios",
        "securities",
        ["benchmark_security_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_portfolios_benchmark_security_id",
        "portfolios",
        ["benchmark_security_id"],
    )

    op.add_column(
        "sync_runs",
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "import_batches",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("accepted_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("error_summary", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_import_batches_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_batches"),
        sa.UniqueConstraint(
            "portfolio_id",
            "source",
            "file_sha256",
            name="uq_import_batches_portfolio_source_hash",
        ),
    )
    op.create_index("ix_import_batches_portfolio_id", "import_batches", ["portfolio_id"])

    op.add_column("transactions", sa.Column("import_batch_id", UUID, nullable=True))
    op.add_column(
        "transactions", sa.Column("source_record_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "transactions", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_transactions_import_batch_id_import_batches",
        "transactions",
        "import_batches",
        ["import_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_transactions_import_batch_id", "transactions", ["import_batch_id"])
    op.drop_constraint(
        "uq_transactions_portfolio_id_source_external_id",
        "transactions",
        type_="unique",
    )
    op.create_index(
        "uq_transactions_external_id_not_null",
        "transactions",
        ["portfolio_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_transactions_source_record_hash_not_null",
        "transactions",
        ["portfolio_id", "source", "source_record_hash"],
        unique=True,
        postgresql_where=sa.text("source_record_hash IS NOT NULL"),
    )
    op.execute(
        """UPDATE transactions SET
               quantity=abs(quantity), gross_amount=abs(gross_amount),
               fees=abs(fees), taxes=abs(taxes), currency=upper(currency)"""
    )
    op.create_check_constraint(
        "transaction_type_allowed",
        "transactions",
        "transaction_type IN ('opening_balance','buy','sell','deposit','withdrawal',"
        "'dividend','fee','tax','split','transfer_in','transfer_out')",
    )
    op.create_check_constraint(
        "currency_iso3", "transactions", "currency ~ '^[A-Z]{3}$'"
    )
    op.create_check_constraint(
        "quantity_nonnegative", "transactions", "quantity >= 0"
    )
    op.create_check_constraint(
        "price_nonnegative", "transactions", "price IS NULL OR price >= 0"
    )
    op.create_check_constraint(
        "gross_amount_nonnegative", "transactions", "gross_amount >= 0"
    )
    op.create_check_constraint(
        "fees_nonnegative", "transactions", "fees >= 0"
    )
    op.create_check_constraint(
        "taxes_nonnegative", "transactions", "taxes >= 0"
    )
    op.create_check_constraint(
        "fx_rate_positive",
        "transactions",
        "fx_rate_to_base IS NULL OR fx_rate_to_base > 0",
    )
    op.create_check_constraint(
        "security_required",
        "transactions",
        "transaction_type NOT IN ('opening_balance','buy','sell','split') "
        "OR security_id IS NOT NULL",
    )

    op.add_column("price_bars", sa.Column("adjustment_factor", RATE, nullable=True))
    op.add_column(
        "price_bars",
        sa.Column("is_final", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("price_bars", sa.Column("sync_run_id", UUID, nullable=True))
    op.add_column(
        "price_bars",
        sa.Column("quality_status", sa.String(length=40), server_default="valid", nullable=False),
    )
    op.create_foreign_key(
        "fk_price_bars_sync_run_id_sync_runs",
        "price_bars",
        "sync_runs",
        ["sync_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_price_bars_sync_run_id", "price_bars", ["sync_run_id"])

    op.add_column("fx_rates", sa.Column("sync_run_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_fx_rates_sync_run_id_sync_runs",
        "fx_rates",
        "sync_runs",
        ["sync_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_fx_rates_sync_run_id", "fx_rates", ["sync_run_id"])

    op.create_table(
        "portfolio_valuation_snapshots",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("total_market_value", MONEY, nullable=False),
        sa.Column("total_cost_basis", MONEY, nullable=False),
        sa.Column("cash_value", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("sync_run_id", UUID, nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("history_completeness", sa.String(length=40), nullable=False),
        sa.Column("cash_balances", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("warnings", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("config_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_portfolio_valuation_snapshots_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["sync_runs.id"],
            name="fk_portfolio_valuation_snapshots_sync_run_id_sync_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_valuation_snapshots"),
        sa.UniqueConstraint(
            "portfolio_id",
            "as_of",
            "source",
            name="uq_portfolio_valuations_portfolio_as_of_source",
        ),
    )
    op.create_index(
        "ix_portfolio_valuation_snapshots_portfolio_id",
        "portfolio_valuation_snapshots",
        ["portfolio_id"],
    )
    op.create_index(
        "ix_portfolio_valuation_snapshots_as_of", "portfolio_valuation_snapshots", ["as_of"]
    )
    op.create_index(
        "ix_portfolio_valuation_snapshots_valuation_date",
        "portfolio_valuation_snapshots",
        ["valuation_date"],
    )
    op.create_index(
        "ix_portfolio_valuation_snapshots_sync_run_id",
        "portfolio_valuation_snapshots",
        ["sync_run_id"],
    )
    op.create_index(
        "ix_portfolio_valuation_snapshots_input_hash",
        "portfolio_valuation_snapshots",
        ["input_hash"],
    )

    op.execute(
        """INSERT INTO portfolio_valuation_snapshots (
               id, created_at, updated_at, portfolio_id, as_of, valuation_date,
               base_currency, total_market_value, total_cost_basis, cash_value,
               unrealized_pnl, data_as_of, source, sync_run_id, input_hash,
               history_completeness, cash_balances, warnings, config_snapshot
           )
           SELECT
               md5(portfolio_id::text || '|' || as_of::text || '|' || source)::uuid,
               min(created_at), max(updated_at), portfolio_id, as_of, as_of::date,
               max(base_currency),
               COALESCE(
                   max(market_value) FILTER (WHERE security_id IS NULL),
                   sum(market_value) FILTER (WHERE security_id IS NOT NULL), 0
               ),
               COALESCE(
                   max(cost_basis) FILTER (WHERE security_id IS NULL),
                   sum(cost_basis) FILTER (WHERE security_id IS NOT NULL), 0
               ),
               0,
               COALESCE(
                   max(unrealized_pnl) FILTER (WHERE security_id IS NULL),
                   sum(unrealized_pnl) FILTER (WHERE security_id IS NOT NULL), 0
               ),
               as_of, source, NULL,
               md5(portfolio_id::text || as_of::text || source) ||
                   md5(source || as_of::text || portfolio_id::text),
               CASE WHEN bool_or(security_id IS NULL)
                    THEN 'opening_balance_only' ELSE 'unknown' END,
               '{}'::jsonb,
               '["migrated_from_legacy_position_snapshot"]'::jsonb,
               '{"migration":"20260812_0002"}'::jsonb
           FROM position_snapshots
           GROUP BY portfolio_id, as_of, source"""
    )

    op.add_column("position_snapshots", sa.Column("valuation_snapshot_id", UUID, nullable=True))
    op.execute(
        """UPDATE position_snapshots p
           SET valuation_snapshot_id=v.id
           FROM portfolio_valuation_snapshots v
           WHERE v.portfolio_id=p.portfolio_id AND v.as_of=p.as_of AND v.source=p.source"""
    )
    op.execute("DELETE FROM position_snapshots WHERE security_id IS NULL")
    op.drop_constraint(
        "uq_position_snapshots_portfolio_security_as_of_source",
        "position_snapshots",
        type_="unique",
    )
    op.drop_index("ix_position_snapshots_portfolio_id", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_as_of", table_name="position_snapshots")
    op.drop_constraint(
        "fk_position_snapshots_portfolio_id_portfolios",
        "position_snapshots",
        type_="foreignkey",
    )
    op.drop_column("position_snapshots", "portfolio_id")
    op.drop_column("position_snapshots", "as_of")
    op.drop_column("position_snapshots", "source")
    op.alter_column(
        "position_snapshots", "market_price", new_column_name="native_price", existing_type=PRICE
    )
    op.alter_column(
        "position_snapshots", "market_value", new_column_name="market_value_base", existing_type=MONEY
    )
    op.alter_column(
        "position_snapshots", "cost_basis", new_column_name="cost_basis_base", existing_type=MONEY
    )
    op.alter_column(
        "position_snapshots",
        "unrealized_pnl",
        new_column_name="unrealized_pnl_base",
        existing_type=MONEY,
    )
    op.add_column("position_snapshots", sa.Column("price_bar_id", UUID, nullable=True))
    op.add_column("position_snapshots", sa.Column("fx_rate_id", UUID, nullable=True))
    op.add_column(
        "position_snapshots",
        sa.Column("native_currency", sa.String(length=3), server_default="USD", nullable=False),
    )
    op.execute("UPDATE position_snapshots SET native_currency=base_currency")
    op.add_column(
        "position_snapshots",
        sa.Column("valuation_fx_rate", RATE, server_default="1", nullable=False),
    )
    op.alter_column("position_snapshots", "valuation_snapshot_id", nullable=False)
    op.alter_column("position_snapshots", "security_id", nullable=False)
    op.create_foreign_key(
        "fk_position_snapshots_valuation_id_portfolio_valuations",
        "position_snapshots",
        "portfolio_valuation_snapshots",
        ["valuation_snapshot_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_position_snapshots_price_bar_id_price_bars",
        "position_snapshots",
        "price_bars",
        ["price_bar_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_position_snapshots_fx_rate_id_fx_rates",
        "position_snapshots",
        "fx_rates",
        ["fx_rate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_position_snapshots_valuation_security",
        "position_snapshots",
        ["valuation_snapshot_id", "security_id"],
    )
    op.create_index(
        "ix_position_snapshots_valuation_snapshot_id",
        "position_snapshots",
        ["valuation_snapshot_id"],
    )
    op.create_index("ix_position_snapshots_price_bar_id", "position_snapshots", ["price_bar_id"])
    op.create_index("ix_position_snapshots_fx_rate_id", "position_snapshots", ["fx_rate_id"])


def downgrade() -> None:
    op.add_column("position_snapshots", sa.Column("portfolio_id", UUID, nullable=True))
    op.add_column(
        "position_snapshots", sa.Column("as_of", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("position_snapshots", sa.Column("source", sa.String(length=80), nullable=True))
    op.execute(
        """UPDATE position_snapshots p SET
               portfolio_id=v.portfolio_id, as_of=v.as_of, source=v.source
           FROM portfolio_valuation_snapshots v WHERE v.id=p.valuation_snapshot_id"""
    )
    op.alter_column("position_snapshots", "portfolio_id", nullable=False)
    op.alter_column("position_snapshots", "as_of", nullable=False)
    op.alter_column("position_snapshots", "source", nullable=False)
    op.alter_column("position_snapshots", "security_id", nullable=True)
    op.alter_column(
        "position_snapshots", "native_price", new_column_name="market_price", existing_type=PRICE
    )
    op.alter_column(
        "position_snapshots", "market_value_base", new_column_name="market_value", existing_type=MONEY
    )
    op.alter_column(
        "position_snapshots", "cost_basis_base", new_column_name="cost_basis", existing_type=MONEY
    )
    op.alter_column(
        "position_snapshots",
        "unrealized_pnl_base",
        new_column_name="unrealized_pnl",
        existing_type=MONEY,
    )
    op.execute(
        """INSERT INTO position_snapshots (
               id, created_at, updated_at, valuation_snapshot_id, security_id,
               quantity, average_cost, market_price, market_value, cost_basis,
               unrealized_pnl, weight, base_currency, snapshot_data, price_bar_id,
               fx_rate_id, native_currency, valuation_fx_rate,
               portfolio_id, as_of, source
           )
           SELECT
               md5(id::text || '|aggregate')::uuid, created_at, updated_at, id, NULL,
               0, NULL, NULL, total_market_value, total_cost_basis,
               unrealized_pnl, NULL, base_currency, config_snapshot, NULL,
               NULL, base_currency, 1, portfolio_id, as_of, source
           FROM portfolio_valuation_snapshots"""
    )
    op.drop_constraint(
        "uq_position_snapshots_valuation_security",
        "position_snapshots",
        type_="unique",
    )
    op.drop_index("ix_position_snapshots_valuation_snapshot_id", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_price_bar_id", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_fx_rate_id", table_name="position_snapshots")
    op.drop_constraint(
        "fk_position_snapshots_price_bar_id_price_bars",
        "position_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_position_snapshots_fx_rate_id_fx_rates",
        "position_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_position_snapshots_valuation_id_portfolio_valuations",
        "position_snapshots",
        type_="foreignkey",
    )
    op.drop_column("position_snapshots", "price_bar_id")
    op.drop_column("position_snapshots", "fx_rate_id")
    op.drop_column("position_snapshots", "native_currency")
    op.drop_column("position_snapshots", "valuation_fx_rate")
    op.drop_column("position_snapshots", "valuation_snapshot_id")
    op.create_foreign_key(
        "fk_position_snapshots_portfolio_id_portfolios",
        "position_snapshots",
        "portfolios",
        ["portfolio_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_position_snapshots_portfolio_security_as_of_source",
        "position_snapshots",
        ["portfolio_id", "security_id", "as_of", "source"],
    )
    op.create_index("ix_position_snapshots_portfolio_id", "position_snapshots", ["portfolio_id"])
    op.create_index("ix_position_snapshots_as_of", "position_snapshots", ["as_of"])
    op.drop_table("portfolio_valuation_snapshots")

    op.drop_index("ix_fx_rates_sync_run_id", table_name="fx_rates")
    op.drop_constraint("fk_fx_rates_sync_run_id_sync_runs", "fx_rates", type_="foreignkey")
    op.drop_column("fx_rates", "sync_run_id")

    op.drop_index("ix_price_bars_sync_run_id", table_name="price_bars")
    op.drop_constraint(
        "fk_price_bars_sync_run_id_sync_runs", "price_bars", type_="foreignkey"
    )
    op.drop_column("price_bars", "quality_status")
    op.drop_column("price_bars", "sync_run_id")
    op.drop_column("price_bars", "is_final")
    op.drop_column("price_bars", "adjustment_factor")

    for constraint in (
        "security_required",
        "fx_rate_positive",
        "taxes_nonnegative",
        "fees_nonnegative",
        "gross_amount_nonnegative",
        "price_nonnegative",
        "quantity_nonnegative",
        "currency_iso3",
        "transaction_type_allowed",
    ):
        op.drop_constraint(constraint, "transactions", type_="check")
    op.drop_index("uq_transactions_source_record_hash_not_null", table_name="transactions")
    op.drop_index("uq_transactions_external_id_not_null", table_name="transactions")
    op.drop_index("ix_transactions_import_batch_id", table_name="transactions")
    op.drop_constraint(
        "fk_transactions_import_batch_id_import_batches",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "settled_at")
    op.drop_column("transactions", "source_record_hash")
    op.drop_column("transactions", "import_batch_id")
    op.create_unique_constraint(
        "uq_transactions_portfolio_id_source_external_id",
        "transactions",
        ["portfolio_id", "source", "external_id"],
    )
    op.drop_table("import_batches")

    op.drop_column("sync_runs", "retry_count")
    op.drop_index("ix_portfolios_benchmark_security_id", table_name="portfolios")
    op.drop_constraint(
        "fk_portfolios_benchmark_security_id_securities", "portfolios", type_="foreignkey"
    )
    op.drop_column("portfolios", "benchmark_security_id")
    op.drop_column("portfolios", "display_timezone")
    op.drop_column("portfolios", "cost_basis_method")
