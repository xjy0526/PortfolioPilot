"""PortfolioPilot FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app.api.router_registry import (
    register_compat_routes,
    register_core_routes,
    register_experimental_routes,
)
from app.db.session import dispose_async_engine, get_db_session
from app.core.resources import close_resources, initialize_resources
from cache_manager import CacheManager
from config import settings
from logging_config import setup_logging
from middleware.auth import BasicAuthMiddleware, ReadOnlyDemoMiddleware

setup_logging(settings.ENVIRONMENT)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


async def reload_portfolio_and_subscribe() -> None:
    """Deprecated Parqet callback kept so the optional OAuth route remains importable."""
    logger.warning(
        "Parqet refresh no longer mutates the active portfolio; import its transactions "
        "into PostgreSQL instead"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources and explicitly enabled compatibility storage."""
    logger.info("PortfolioPilot starting")
    settings.validate_runtime_configuration()
    CacheManager.clear_volatile_caches()
    CacheManager.cleanup_stale_files()
    await initialize_resources()

    if settings.ENABLE_LEGACY_SQLITE_COMPAT:
        # PostgreSQL schema creation remains exclusively owned by Alembic. The
        # compatibility migration is fail-fast so a broken legacy store cannot
        # be mistaken for a complete startup.
        from database import init_db, migrate_json_to_sqlite

        import asyncio

        logger.warning("Legacy SQLite compatibility is explicitly enabled")
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(migrate_json_to_sqlite)

    yield

    await close_resources()
    await dispose_async_engine()
    logger.info("PortfolioPilot stopped")


app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-market portfolio risk analysis and evidence-driven research platform",
    version="2.0.0",
    lifespan=lifespan,
    dependencies=[Depends(get_db_session)],
)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(BasicAuthMiddleware)
# Starlette executes the most recently added middleware first. Keep the
# deployment-wide read-only gate outside authentication so every mutation is
# consistently rejected with 403, including anonymous requests.
app.add_middleware(ReadOnlyDemoMiddleware)
if settings.auth_configured:
    logger.info("Dashboard password protection enabled")

STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

register_core_routes(app)
register_compat_routes(app)
register_experimental_routes(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1" if settings.ENVIRONMENT == "development" else settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=False,
    )
