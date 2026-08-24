"""Remove only the deterministic PortfolioPilot demo namespace."""
from __future__ import annotations

import asyncio
import json

from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.services.demo_fixture import reset_demo_fixture


async def _reset() -> dict[str, int]:
    async with AsyncSessionFactory.begin() as session:
        return await reset_demo_fixture(session)


async def _run() -> None:
    try:
        removed = await _reset()
        print("DEMO_RESET=" + json.dumps({"removed": removed}, sort_keys=True))
    finally:
        await dispose_async_engine()


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
