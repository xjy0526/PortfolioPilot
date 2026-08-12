"""Repositories for users and portfolios."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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
        statement = (
            insert(User)
            .values(
                email=normalized,
                display_name=display_name,
                preferences=preferences or {},
            )
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User)
        )
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self.get_by_email(normalized)
        if existing is None:
            raise RuntimeError("Atomic user upsert did not return a row")
        return existing


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
        statement = (
            insert(Portfolio)
            .values(
                user_id=user_id,
                name=name,
                base_currency=base_currency.upper(),
                description=description,
                settings=portfolio_settings or {},
            )
            .on_conflict_do_nothing(index_elements=[Portfolio.user_id, Portfolio.name])
            .returning(Portfolio)
        )
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self.get_by_user_and_name(user_id, name)
        if existing is None:
            raise RuntimeError("Atomic portfolio upsert did not return a row")
        return existing

    async def list_for_user(self, user_id: uuid.UUID) -> list[Portfolio]:
        statement = select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.name)
        return list((await self.session.scalars(statement)).all())

    async def list_active(self) -> list[Portfolio]:
        statement = select(Portfolio).where(Portfolio.is_active.is_(True)).order_by(Portfolio.name)
        return list((await self.session.scalars(statement)).all())
