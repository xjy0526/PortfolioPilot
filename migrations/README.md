# Alembic migrations

The migration environment uses the async SQLAlchemy engine and `asyncpg`.
Apply migrations explicitly with:

```bash
alembic upgrade head
```

Application imports and startup never call `Base.metadata.create_all()`.
