"""Shared repository operations for SQLAlchemy ORM entities."""
from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    @staticmethod
    def insert_values(entity: ModelT) -> dict[str, object]:
        """Return explicitly assigned ORM column values for a Core upsert."""
        values: dict[str, object] = {}
        mapper = inspect(type(entity))
        for attribute in mapper.column_attrs:
            value = getattr(entity, attribute.key)
            if value is not None:
                values[attribute.key] = value
        return values
