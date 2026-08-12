"""Point-in-time backtest provenance and result models."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, MONEY_NUMERIC, UUIDTimestampMixin, WEIGHT_NUMERIC


class BacktestRun(UUIDTimestampMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("uq_backtest_runs_input_hash", "input_hash", unique=True),)

    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    portfolio_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portfolio_valuation_snapshots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code_version: Mapped[str] = mapped_column(String(120), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price_source: Mapped[str] = mapped_column(String(120), nullable=False)
    benchmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_convention: Mapped[str] = mapped_column(Text, nullable=False)
    cost_assumptions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    output_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mock_price_data_used: Mapped[bool] = mapped_column(nullable=False, default=False)


class BacktestRebalanceSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "backtest_rebalance_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "backtest_run_id",
            "strategy",
            "execution_at",
            name="uq_backtest_rebalance_run_strategy_execution",
        ),
    )

    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    execution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eligible_universe: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    excluded_assets: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    pre_trade_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    executed_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    post_return_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    turnover: Mapped[Decimal] = mapped_column(WEIGHT_NUMERIC, nullable=False)
    executed_turnover: Mapped[Decimal] = mapped_column(WEIGHT_NUMERIC, nullable=False)
    costs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BacktestStrategyResult(UUIDTimestampMixin, Base):
    __tablename__ = "backtest_strategy_results"
    __table_args__ = (
        UniqueConstraint(
            "backtest_run_id", "strategy", name="uq_backtest_strategy_results_run_strategy"
        ),
    )

    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    final_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    nav_series: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    turnover: Mapped[Decimal] = mapped_column(WEIGHT_NUMERIC, nullable=False)
    total_costs: Mapped[Decimal] = mapped_column(MONEY_NUMERIC, nullable=False)
