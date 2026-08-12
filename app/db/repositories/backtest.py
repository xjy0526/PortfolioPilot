"""Repository operations for traceable backtest runs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.backtest import BacktestRun
from app.db.repositories.base import BaseRepository


class BacktestRunRepository(BaseRepository[BacktestRun]):
    model = BacktestRun

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_input_hash(self, input_hash: str) -> BacktestRun | None:
        result = await self.session.execute(
            select(BacktestRun).where(BacktestRun.input_hash == input_hash)
        )
        return result.scalar_one_or_none()
