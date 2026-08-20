"""Async SQLAlchemy engine and per-unit-of-work session factories."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings


def create_engine_and_session_factory(
    database_url: str,
    *,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build an async engine and factory without connecting or creating tables."""
    engine_kwargs: dict[str, object] = {
        "echo": echo,
        "pool_pre_ping": True,
        "connect_args": {
            "server_settings": {
                "timezone": "UTC",
                "application_name": "portfoliopilot",
            }
        },
    }
    if database_url.startswith("postgresql+asyncpg://"):
        engine_kwargs.update(
            {
                "pool_size": settings.DATABASE_POOL_SIZE,
                "max_overflow": settings.DATABASE_MAX_OVERFLOW,
                "pool_timeout": settings.DATABASE_POOL_TIMEOUT_SECONDS,
            }
        )
    engine = create_async_engine(database_url, **engine_kwargs)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, factory


async_engine, AsyncSessionFactory = create_engine_and_session_factory(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield one transaction-scoped Session for each FastAPI request."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            if session.in_transaction():
                await session.commit()


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """Create a new transaction-bound Session for one worker task invocation."""
    async with AsyncSessionFactory.begin() as session:
        yield session


async def dispose_async_engine() -> None:
    """Release the shared connection pool during application shutdown."""
    await async_engine.dispose()
