"""Contract tests for the PostgreSQL declarative mapping."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import DateTime, Float, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.models import Base
from app.db.session import AsyncSessionFactory, worker_session
from config import Settings

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "users",
    "portfolios",
    "securities",
    "provider_symbols",
    "transactions",
    "import_batches",
    "price_bars",
    "fx_rates",
    "portfolio_valuation_snapshots",
    "position_snapshots",
    "sync_runs",
    "risk_runs",
    "backtest_runs",
    "backtest_rebalance_snapshots",
    "backtest_strategy_results",
}


def test_initial_postgres_table_set_and_common_columns():
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    for table in Base.metadata.tables.values():
        assert isinstance(table.c.id.type, UUID)
        assert table.c.id.primary_key
        assert isinstance(table.c.created_at.type, DateTime)
        assert table.c.created_at.type.timezone is True
        assert isinstance(table.c.updated_at.type, DateTime)
        assert table.c.updated_at.type.timezone is True


def test_financial_columns_are_numeric_and_never_float():
    numeric_columns = {
        "transactions": {
            "quantity", "price", "gross_amount", "fees", "taxes", "fx_rate_to_base"
        },
        "price_bars": {
            "open", "high", "low", "close", "adjusted_close", "adjustment_factor", "volume"
        },
        "fx_rates": {"rate"},
        "position_snapshots": {
            "quantity", "average_cost", "native_price", "valuation_fx_rate",
            "market_value_base", "cost_basis_base", "unrealized_pnl_base", "weight",
        },
        "portfolio_valuation_snapshots": {
            "total_market_value", "total_cost_basis", "cash_value", "unrealized_pnl",
        },
        "backtest_rebalance_snapshots": {"turnover", "executed_turnover"},
        "backtest_strategy_results": {"turnover", "total_costs"},
    }
    for table_name, column_names in numeric_columns.items():
        table = Base.metadata.tables[table_name]
        for column_name in column_names:
            assert isinstance(table.c[column_name].type, Numeric)
            assert not isinstance(table.c[column_name].type, Float)


def test_jsonb_runtime_and_configuration_snapshots():
    json_columns = {
        ("users", "preferences"),
        ("portfolios", "settings"),
        ("securities", "security_metadata"),
        ("provider_symbols", "mapping_metadata"),
        ("transactions", "raw_payload"),
        ("import_batches", "error_summary"),
        ("price_bars", "raw_payload"),
        ("fx_rates", "raw_payload"),
        ("position_snapshots", "snapshot_data"),
        ("portfolio_valuation_snapshots", "cash_balances"),
        ("portfolio_valuation_snapshots", "warnings"),
        ("portfolio_valuation_snapshots", "config_snapshot"),
        ("sync_runs", "config_snapshot"),
        ("sync_runs", "result_snapshot"),
        ("risk_runs", "config_snapshot"),
        ("risk_runs", "result_snapshot"),
        ("risk_runs", "evidence_ids"),
        ("backtest_runs", "cost_assumptions"),
        ("backtest_runs", "config_snapshot"),
        ("backtest_runs", "output_metrics"),
        ("backtest_rebalance_snapshots", "eligible_universe"),
        ("backtest_rebalance_snapshots", "excluded_assets"),
        ("backtest_rebalance_snapshots", "pre_trade_weights"),
        ("backtest_rebalance_snapshots", "target_weights"),
        ("backtest_rebalance_snapshots", "executed_weights"),
        ("backtest_rebalance_snapshots", "post_return_weights"),
        ("backtest_rebalance_snapshots", "costs"),
        ("backtest_strategy_results", "metrics"),
        ("backtest_strategy_results", "final_weights"),
        ("backtest_strategy_results", "nav_series"),
    }
    for table_name, column_name in json_columns:
        assert isinstance(Base.metadata.tables[table_name].c[column_name].type, JSONB)


def test_required_idempotency_constraints_are_present():
    price_constraints = {constraint.name for constraint in Base.metadata.tables["price_bars"].constraints}
    transaction_indexes = {
        index.name for index in Base.metadata.tables["transactions"].indexes
    }

    assert "uq_price_bars_security_id_trade_date_source" in price_constraints
    assert "uq_transactions_external_id_not_null" in transaction_indexes
    assert "uq_transactions_source_record_hash_not_null" in transaction_indexes


def test_legacy_database_module_has_no_import_time_init_call():
    tree = ast.parse((ROOT / "database.py").read_text(encoding="utf-8"))
    top_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "init_db" not in top_level_calls


def test_database_url_normalizes_managed_postgres_urls_to_asyncpg():
    current = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://user:password@database.example/portfolio",
    )
    assert current.DATABASE_URL.startswith("postgresql+asyncpg://")


@pytest.mark.asyncio
async def test_session_factory_returns_independent_sessions():
    first = AsyncSessionFactory()
    second = AsyncSessionFactory()
    try:
        assert first is not second
        assert first.sync_session is not second.sync_session
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_concurrent_worker_tasks_never_share_a_session():
    async def capture_session_identity() -> int:
        async with worker_session() as session:
            await asyncio.sleep(0)
            return id(session)

    identities = await asyncio.gather(
        capture_session_identity(),
        capture_session_identity(),
        capture_session_identity(),
    )
    assert len(set(identities)) == len(identities)
