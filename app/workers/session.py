"""Explicit worker Session boundary.

Each concurrent job must enter ``worker_session()`` independently. Only the
sessionmaker and engine pool are shared; an ``AsyncSession`` never is.
"""

from app.db.session import worker_session

__all__ = ["worker_session"]
