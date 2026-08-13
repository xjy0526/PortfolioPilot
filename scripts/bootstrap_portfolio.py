"""Create an idempotent local user and portfolio for ledger imports."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.repositories import (
    PortfolioMembershipRepository,
    PortfolioRepository,
    UserRepository,
)
from app.db.session import AsyncSessionFactory, dispose_async_engine
from config import settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="local-research@local.invalid")
    parser.add_argument("--name", default="Local Research Portfolio")
    parser.add_argument("--base-currency", default="CNY")
    return parser


async def bootstrap(email: str, name: str, base_currency: str) -> dict[str, str]:
    async with AsyncSessionFactory() as session:
        async with session.begin():
            user = await UserRepository(session).get_or_create(
                email=email,
                display_name="Local Research User",
                preferences={"synthetic_identity": True},
            )
            portfolio = await PortfolioRepository(session).get_or_create(
                user_id=user.id,
                name=name,
                base_currency=base_currency,
                description="Local research and system demonstration portfolio.",
                portfolio_settings={"contains_personal_data": False},
            )
            await PortfolioMembershipRepository(session).grant(
                portfolio_id=portfolio.id,
                user_id=settings.LOCAL_PRINCIPAL_USER,
                role="admin",
                can_read=True,
                can_write=True,
                can_admin=True,
            )
        return {
            "user_id": str(user.id),
            "portfolio_id": str(portfolio.id),
            "base_currency": portfolio.base_currency,
        }


async def _main() -> None:
    args = _parser().parse_args()
    try:
        print(json.dumps(await bootstrap(args.email, args.name, args.base_currency)))
    finally:
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())
