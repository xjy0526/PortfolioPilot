"""Repositories for sync and risk run traceability."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import RiskRun, SyncRun
from app.db.repositories.base import BaseRepository


class SyncRunRepository(BaseRepository[SyncRun]):
    model = SyncRun

    async def list_for_portfolio(self, portfolio_id: uuid.UUID) -> list[SyncRun]:
        statement = (
            select(SyncRun)
            .where(SyncRun.portfolio_id == portfolio_id)
            .order_by(SyncRun.created_at.desc())
        )
        return list((await self.session.scalars(statement)).all())


class RiskRunRepository(BaseRepository[RiskRun]):
    model = RiskRun

    async def list_for_portfolio(self, portfolio_id: uuid.UUID) -> list[RiskRun]:
        statement = (
            select(RiskRun)
            .where(RiskRun.portfolio_id == portfolio_id)
            .order_by(RiskRun.created_at.desc())
        )
        return list((await self.session.scalars(statement)).all())
