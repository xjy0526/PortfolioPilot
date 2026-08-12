"""CLI entry point: python -m app.workers.run_position_rebuild."""
from __future__ import annotations

import asyncio

from app.db.session import dispose_async_engine
from app.workers.cli import base_parser, iso_datetime, optional_uuid, print_result
from app.workers.jobs import run_position_rebuild_job


async def _main() -> None:
    parser = base_parser("Rebuild PostgreSQL position and valuation snapshots")
    parser.add_argument("--portfolio-id", type=optional_uuid)
    parser.add_argument("--as-of", type=iso_datetime)
    args = parser.parse_args()
    try:
        run = await run_position_rebuild_job(
            portfolio_id=args.portfolio_id,
            as_of=args.as_of,
        )
        print_result(run)
    finally:
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())

