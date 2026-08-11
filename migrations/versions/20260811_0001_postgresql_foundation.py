"""Create PostgreSQL persistence foundation.

Revision ID: 20260811_0001
Revises:
Create Date: 2026-08-11
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260811_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
MONEY = sa.Numeric(28, 8)
PRICE = sa.Numeric(28, 10)
QUANTITY = sa.Numeric(38, 12)
RATE = sa.Numeric(28, 12)
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        *_identity_columns(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("preferences", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "securities",
        *_identity_columns(),
        sa.Column("canonical_symbol", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=False),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("sector", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column(
            "security_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_securities"),
        sa.UniqueConstraint("isin", name="uq_securities_isin"),
        sa.UniqueConstraint(
            "canonical_symbol",
            "exchange",
            "market",
            name="uq_securities_canonical_symbol_exchange_market",
        ),
    )
    op.create_index("ix_securities_canonical_symbol", "securities", ["canonical_symbol"])

    op.create_table(
        "portfolios",
        *_identity_columns(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("settings", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_portfolios_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolios"),
        sa.UniqueConstraint("user_id", "name", name="uq_portfolios_user_id_name"),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    op.create_table(
        "provider_symbols",
        *_identity_columns(),
        sa.Column("security_id", UUID, nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("symbol", sa.String(length=120), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column(
            "mapping_metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_provider_symbols_security_id_securities",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_symbols"),
        sa.UniqueConstraint(
            "provider", "symbol", name="uq_provider_symbols_provider_symbol"
        ),
        sa.UniqueConstraint(
            "security_id", "provider", name="uq_provider_symbols_security_id_provider"
        ),
    )
    op.create_index("ix_provider_symbols_security_id", "provider_symbols", ["security_id"])

    op.create_table(
        "sync_runs",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_version", sa.String(length=120), nullable=False),
        sa.Column(
            "config_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "result_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_sync_runs_portfolio_id_portfolios",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sync_runs"),
    )
    op.create_index("ix_sync_runs_portfolio_id", "sync_runs", ["portfolio_id"])

    op.create_table(
        "transactions",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("security_id", UUID, nullable=True),
        sa.Column("transaction_type", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("price", PRICE, nullable=True),
        sa.Column("gross_amount", MONEY, nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("taxes", MONEY, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fx_rate_to_base", RATE, nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("raw_payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_transactions_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_transactions_security_id_securities",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint(
            "portfolio_id",
            "source",
            "external_id",
            name="uq_transactions_portfolio_id_source_external_id",
        ),
    )
    op.create_index("ix_transactions_portfolio_id", "transactions", ["portfolio_id"])
    op.create_index("ix_transactions_security_id", "transactions", ["security_id"])
    op.create_index("ix_transactions_occurred_at", "transactions", ["occurred_at"])

    op.create_table(
        "price_bars",
        *_identity_columns(),
        sa.Column("security_id", UUID, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("open", PRICE, nullable=True),
        sa.Column("high", PRICE, nullable=True),
        sa.Column("low", PRICE, nullable=True),
        sa.Column("close", PRICE, nullable=False),
        sa.Column("adjusted_close", PRICE, nullable=True),
        sa.Column("volume", QUANTITY, nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_price_bars_security_id_securities",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_bars"),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "source",
            name="uq_price_bars_security_id_trade_date_source",
        ),
    )
    op.create_index("ix_price_bars_security_id", "price_bars", ["security_id"])
    op.create_index("ix_price_bars_trade_date", "price_bars", ["trade_date"])

    op.create_table(
        "fx_rates",
        *_identity_columns(),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("rate", RATE, nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_fx_rates"),
        sa.UniqueConstraint(
            "base_currency",
            "quote_currency",
            "rate_date",
            "source",
            name="uq_fx_rates_pair_date_source",
        ),
    )
    op.create_index("ix_fx_rates_rate_date", "fx_rates", ["rate_date"])

    op.create_table(
        "position_snapshots",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("security_id", UUID, nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("average_cost", PRICE, nullable=True),
        sa.Column("market_price", PRICE, nullable=True),
        sa.Column("market_value", MONEY, nullable=False),
        sa.Column("cost_basis", MONEY, nullable=True),
        sa.Column("unrealized_pnl", MONEY, nullable=True),
        sa.Column("weight", WEIGHT, nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column(
            "snapshot_data", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_position_snapshots_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_position_snapshots_security_id_securities",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_position_snapshots"),
        sa.UniqueConstraint(
            "portfolio_id",
            "security_id",
            "as_of",
            "source",
            name="uq_position_snapshots_portfolio_security_as_of_source",
        ),
    )
    op.create_index("ix_position_snapshots_portfolio_id", "position_snapshots", ["portfolio_id"])
    op.create_index("ix_position_snapshots_security_id", "position_snapshots", ["security_id"])
    op.create_index("ix_position_snapshots_as_of", "position_snapshots", ["as_of"])

    op.create_table(
        "risk_runs",
        *_identity_columns(),
        sa.Column("portfolio_id", UUID, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position_snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_version", sa.String(length=120), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "config_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "result_snapshot", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("evidence_ids", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolios.id"],
            name="fk_risk_runs_portfolio_id_portfolios",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_runs"),
    )
    op.create_index("ix_risk_runs_portfolio_id", "risk_runs", ["portfolio_id"])
    op.create_index("ix_risk_runs_input_hash", "risk_runs", ["input_hash"])


def downgrade() -> None:
    op.drop_table("risk_runs")
    op.drop_table("position_snapshots")
    op.drop_table("fx_rates")
    op.drop_table("price_bars")
    op.drop_table("transactions")
    op.drop_table("sync_runs")
    op.drop_table("provider_symbols")
    op.drop_table("portfolios")
    op.drop_table("securities")
    op.drop_table("users")
