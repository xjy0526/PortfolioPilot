"""Migrate supported legacy SQLite records into PostgreSQL.

Run Alembic first. This script never creates schema and can be rerun safely for
the supported aggregate snapshots and optional Shadow Agent transactions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import PortfolioValuationSnapshot, Transaction
from app.db.repositories import (
    PortfolioRepository,
    PortfolioMembershipRepository,
    PortfolioValuationRepository,
    SecurityRepository,
    TransactionRepository,
    UserRepository,
)
from app.db.session import create_engine_and_session_factory
from config import settings

LEGACY_USER_EMAIL = "legacy-migration@local.invalid"
LEGACY_PORTFOLIO_NAME = "Legacy SQLite Import"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=settings.CACHE_DIR / "portfoliopilot.db",
        help="Path to the legacy SQLite database",
    )
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
        help="Target postgresql+asyncpg URL; defaults to DATABASE_URL",
    )
    parser.add_argument("--base-currency", default="CNY")
    return parser


def _read_rows(path: Path, table: str) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
    finally:
        connection.close()


def _utc_datetime(value: Any, *, fallback_date: Any = None) -> datetime:
    candidate = value or fallback_date
    if isinstance(candidate, datetime):
        parsed = candidate
    elif isinstance(candidate, date):
        parsed = datetime.combine(candidate, datetime.min.time())
    else:
        text = str(candidate or "").strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


async def migrate(
    *,
    sqlite_path: Path,
    database_url: str,
    base_currency: str,
) -> dict[str, int]:
    snapshots = _read_rows(sqlite_path, "portfolio_snapshots")
    shadow_transactions = _read_rows(sqlite_path, "shadow_transactions")
    engine, factory = create_engine_and_session_factory(database_url)
    counts = {
        "snapshots_created": 0,
        "snapshots_existing": 0,
        "transactions_created": 0,
        "transactions_existing": 0,
    }
    try:
        async with factory.begin() as session:
            user = await UserRepository(session).get_or_create(
                email=LEGACY_USER_EMAIL,
                display_name="Legacy SQLite Migration",
                preferences={"source": "sqlite", "synthetic_identity": True},
            )
            portfolio = await PortfolioRepository(session).get_or_create(
                user_id=user.id,
                name=LEGACY_PORTFOLIO_NAME,
                base_currency=base_currency,
                description="Compatibility import; not a live source of truth.",
                portfolio_settings={"source": "sqlite", "migration_version": 1},
            )
            await PortfolioMembershipRepository(session).grant(
                portfolio_id=portfolio.id,
                user_id=settings.LOCAL_PRINCIPAL_USER,
                role="admin",
                can_read=True,
                can_write=True,
                can_admin=True,
            )

            snapshot_repository = PortfolioValuationRepository(session)
            for row in snapshots:
                as_of = _utc_datetime(row.get("timestamp"), fallback_date=row.get("date"))
                source = "legacy_sqlite_portfolio_snapshot"
                existing = await snapshot_repository.find_unique(
                    portfolio.id,
                    as_of,
                    source,
                )
                payload_hash = hashlib.sha256(
                    json.dumps(row, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                snapshot = PortfolioValuationSnapshot(
                    portfolio_id=portfolio.id,
                    as_of=as_of,
                    valuation_date=as_of.date(),
                    base_currency=base_currency.upper(),
                    total_market_value=_decimal(row.get("total_value")),
                    total_cost_basis=_decimal(row.get("total_cost")),
                    cash_value=Decimal("0"),
                    unrealized_pnl=_decimal(row.get("total_pnl")),
                    data_as_of=as_of,
                    source=source,
                    input_hash=payload_hash,
                    history_completeness="unknown",
                    cash_balances={},
                    warnings=["legacy_aggregate_has_no_position_lineage"],
                    config_snapshot={
                        "legacy_aggregate": True,
                        "num_positions": row.get("num_positions", 0),
                        "eur_usd_rate": row.get("eur_usd_rate"),
                        "source_table": "portfolio_snapshots",
                    },
                )
                await snapshot_repository.upsert(snapshot)
                created = existing is None
                key = "snapshots_created" if created else "snapshots_existing"
                counts[key] += 1

            security_repository = SecurityRepository(session)
            transaction_repository = TransactionRepository(session)
            for row in shadow_transactions:
                ticker = str(row.get("ticker") or "CASH").strip().upper()
                security = await security_repository.get_or_create(
                    canonical_symbol=ticker,
                    exchange="LEGACY",
                    market="shadow_simulation",
                    currency="EUR",
                    name=str(row.get("name") or ticker),
                    asset_type="cash" if ticker == "CASH" else "equity",
                    security_metadata={"source": "legacy_sqlite_shadow"},
                )
                legacy_id = str(row.get("id"))
                transaction = Transaction(
                    portfolio_id=portfolio.id,
                    security_id=security.id,
                    transaction_type=str(row.get("action") or "unknown").lower(),
                    occurred_at=_utc_datetime(row.get("timestamp")),
                    quantity=_decimal(row.get("shares")),
                    price=_decimal(row.get("price_eur")),
                    gross_amount=abs(_decimal(row.get("total_eur"))),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="EUR",
                    fx_rate_to_base=Decimal("1") if base_currency.upper() == "EUR" else None,
                    source="legacy_sqlite_shadow",
                    external_id=f"shadow_transactions:{legacy_id}",
                    note=str(row.get("reason") or ""),
                    raw_payload={
                        "source_table": "shadow_transactions",
                        "score": row.get("score"),
                        "confidence": row.get("confidence"),
                    },
                )
                _, created = await transaction_repository.add_idempotent(transaction)
                key = "transactions_created" if created else "transactions_existing"
                counts[key] += 1
    finally:
        await engine.dispose()
    return counts


async def _main() -> None:
    args = _parser().parse_args()
    counts = await migrate(
        sqlite_path=args.sqlite_path,
        database_url=args.database_url,
        base_currency=args.base_currency,
    )
    print("SQLite to PostgreSQL migration complete")
    for key, value in counts.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(_main())
