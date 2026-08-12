"""CLI entry point: python -m app.workers.run_daily_pipeline."""
from __future__ import annotations

import asyncio

from app.db.session import dispose_async_engine
from app.workers.cli import base_parser, iso_datetime, optional_uuid
from app.workers.jobs import run_daily_pipeline_job


async def _main() -> None:
    parser = base_parser("Run market sync followed by portfolio valuation")
    parser.add_argument("--portfolio-id", type=optional_uuid)
    parser.add_argument("--as-of", type=iso_datetime)
    args = parser.parse_args()
    try:
        print(
            await run_daily_pipeline_job(
                portfolio_id=args.portfolio_id,
                as_of=args.as_of,
            )
        )
    finally:
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())

