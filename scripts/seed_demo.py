"""Seed the deterministic PortfolioPilot demo namespace."""
from __future__ import annotations

import asyncio
import json

from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.services.demo_fixture import seed_demo_fixture


async def _seed() -> dict[str, object]:
    async with AsyncSessionFactory.begin() as session:
        manifest = await seed_demo_fixture(session)
        return manifest.as_dict()


async def _run() -> None:
    try:
        manifest = await _seed()
        print("DEMO_MANIFEST=" + json.dumps(manifest, sort_keys=True))
    finally:
        await dispose_async_engine()


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
