"""Repositories for users and portfolios."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Portfolio, User
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email.strip().lower())
        return await self.session.scalar(statement)

    async def get_or_create(
        self,
        *,
        email: str,
        display_name: str = "",
        preferences: dict[str, Any] | None = None,
    ) -> User:
        normalized = email.strip().lower()
        existing = await self.get_by_email(normalized)
        if existing is not None:
            return existing
        return await self.add(
            User(
                email=normalized,
                display_name=display_name,
                preferences=preferences or {},
            )
        )


class PortfolioRepository(BaseRepository[Portfolio]):
    model = Portfolio

    async def get_by_user_and_name(self, user_id: uuid.UUID, name: str) -> Portfolio | None:
        statement = select(Portfolio).where(
            Portfolio.user_id == user_id,
            Portfolio.name == name,
        )
        return await self.session.scalar(statement)

    async def get_or_create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        base_currency: str,
        description: str = "",
        portfolio_settings: dict[str, Any] | None = None,
    ) -> Portfolio:
        existing = await self.get_by_user_and_name(user_id, name)
        if existing is not None:
            return existing
        return await self.add(
            Portfolio(
                user_id=user_id,
                name=name,
                base_currency=base_currency.upper(),
                description=description,
                settings=portfolio_settings or {},
            )
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[Portfolio]:
        statement = select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.name)
        return list((await self.session.scalars(statement)).all())
