"""Repair dashboard valuations that do not match the active legacy generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionFactory
from app.services.legacy_valuation_repair import LegacyValuationRepairService
from time_utils import utc_now


@dataclass(frozen=True, slots=True)
class RepairFailure:
    portfolio_id: uuid.UUID
    error_type: str
    error: str

    def as_dict(self) -> dict[str, str]:
        return {
            "portfolio_id": str(self.portfolio_id),
            "error_type": self.error_type,
            "error": self.error,
        }


async def run_repair(
    *,
    dry_run: bool,
    portfolio_id: uuid.UUID | None,
    limit: int | None,
    continue_on_error: bool,
    as_of: datetime,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
) -> dict[str, object]:
    """Select at most ``limit`` stale candidates, then repair each separately."""
    cutoff = _as_utc(as_of)
    async with session_factory() as session:
        scan = await LegacyValuationRepairService(session).scan(
            as_of=cutoff,
            portfolio_id=portfolio_id,
            limit=limit,
        )

    outcomes: list[dict[str, object]] = []
    failures: list[RepairFailure] = []
    aborted = False
    if dry_run:
        outcomes = [
            {**candidate.as_dict(), "status": "would_rebuild"}
            for candidate in scan.candidates
        ]
    else:
        for candidate in scan.candidates:
            try:
                async with session_factory.begin() as session:
                    outcome = await LegacyValuationRepairService(
                        session
                    ).repair_portfolio(candidate.portfolio_id, as_of=cutoff)
                outcomes.append(outcome.as_dict())
            except Exception as exc:
                failures.append(
                    RepairFailure(
                        portfolio_id=candidate.portfolio_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                )
                if not continue_on_error:
                    aborted = True
                    break

    return {
        "status": (
            "dry_run"
            if dry_run
            else "completed_with_errors"
            if failures
            else "completed"
        ),
        "dry_run": dry_run,
        "as_of": cutoff.isoformat(),
        "scanned_portfolios": scan.scanned_portfolios,
        "affected_portfolios": scan.affected_portfolios,
        "processed_portfolios": len(outcomes),
        "failed_portfolios": len(failures),
        "aborted": aborted,
        "outcomes": outcomes,
        "failures": [failure.as_dict() for failure in failures],
    }


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return value


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report stale portfolios without writing valuation snapshots.",
    )
    parser.add_argument(
        "--portfolio-id",
        type=uuid.UUID,
        help="Restrict the scan to one portfolio UUID.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        help="Maximum number of stale valuation candidates to report or repair.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue repairing other portfolios after one failure.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    report = asyncio.run(
        run_repair(
            dry_run=args.dry_run,
            portfolio_id=args.portfolio_id,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
            as_of=utc_now(),
        )
    )
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 1 if report["failed_portfolios"] else 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
