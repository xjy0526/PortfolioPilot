"""PortfolioPilot FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app.api.health import router as health_router
from app.api.evaluation import router as evaluation_router
from app.api.market_data import router as market_data_router
from app.api.portfolios import router as db_portfolio_router
from app.db.session import dispose_async_engine, get_db_session
from app.core.resources import close_resources, initialize_resources
from app.services.legacy_portfolio_adapter import PortfolioRebuildRequired
from cache_manager import CacheManager
from config import settings
from logging_config import setup_logging
from middleware.auth import BasicAuthMiddleware, ReadOnlyDemoMiddleware
from routes.analysis import router as analysis_router
from routes.analytics import router as analytics_router
from routes.app_settings import router as app_settings_router
from routes.demo import router as demo_router
from routes.knowledge import router as knowledge_router
from routes.parqet_oauth import router as parqet_oauth_router
from routes.portfolio import router as portfolio_router
from routes.prompts import router as prompts_router
from routes.refresh import router as refresh_router
from routes.research import router as research_router
from routes.shadow_portfolio import router as shadow_portfolio_router
from routes.streaming import router as streaming_router
from routes.telegram import router as telegram_router
from routes.workflows import router as workflows_router

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
    """Initialize only compatibility storage; scheduled work runs in workers."""
    logger.info("PortfolioPilot starting")
    settings.validate_runtime_configuration()
    CacheManager.clear_volatile_caches()
    CacheManager.cleanup_stale_files()
    await initialize_resources()

    # SQLite stays readable during incremental migration, but PostgreSQL schema
    # creation remains exclusively owned by Alembic.
    try:
        from database import init_db, migrate_json_to_sqlite

        await asyncio.to_thread(init_db)
        await asyncio.to_thread(migrate_json_to_sqlite)
    except Exception as exc:
        logger.debug("Legacy SQLite initialization skipped: %s", type(exc).__name__)

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


@app.exception_handler(PortfolioRebuildRequired)
async def portfolio_rebuild_required_handler(
    _request: Request,
    exc: PortfolioRebuildRequired,
) -> JSONResponse:
    """Expose stale legacy valuation lineage as a recoverable conflict."""
    logger.warning(
        "Portfolio valuation rebuild required: portfolio_id=%s generation_id=%s",
        exc.portfolio_id,
        exc.active_generation_id,
    )
    return JSONResponse(status_code=409, content=exc.as_dict())

STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(portfolio_router)
app.include_router(refresh_router)
app.include_router(streaming_router)
app.include_router(analysis_router)
app.include_router(analytics_router)
app.include_router(telegram_router)
app.include_router(parqet_oauth_router)
app.include_router(demo_router)
app.include_router(shadow_portfolio_router)
app.include_router(research_router)
app.include_router(app_settings_router)
app.include_router(knowledge_router)
app.include_router(prompts_router)
app.include_router(workflows_router)
app.include_router(evaluation_router)
app.include_router(health_router)
app.include_router(db_portfolio_router)
app.include_router(market_data_router)


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
