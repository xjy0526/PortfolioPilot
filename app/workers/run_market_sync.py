"""CLI entry point: python -m app.workers.run_market_sync."""
from __future__ import annotations

import asyncio

from app.db.session import dispose_async_engine
from app.workers.cli import base_parser, iso_date, optional_uuid, print_result
from app.workers.jobs import run_market_sync_job


async def _main() -> None:
    parser = base_parser("Synchronize market data into PostgreSQL")
    parser.add_argument("--start", type=iso_date)
    parser.add_argument("--end", type=iso_date)
    parser.add_argument("--portfolio-id", type=optional_uuid)
    parser.add_argument("--provider", action="append", dest="providers")
    args = parser.parse_args()
    try:
        run = await run_market_sync_job(
            start=args.start,
            end=args.end,
            provider_names=tuple(args.providers) if args.providers else None,
            portfolio_id=args.portfolio_id,
        )
        print_result(run)
    finally:
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())

