"""Database readiness repository with non-sensitive schema inspection."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ping(self) -> None:
        await self.session.execute(text("SELECT 1"))

    async def pgvector_version(self) -> str | None:
        return await self.session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )

    async def alembic_revision(self) -> str | None:
        return await self.session.scalar(text("SELECT version_num FROM alembic_version"))

    async def table_names(self) -> frozenset[str]:
        rows = await self.session.scalars(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = current_schema()"
            )
        )
        return frozenset(str(item) for item in rows)
