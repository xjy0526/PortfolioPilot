"""Object storage contract used by web uploads and asynchronous workers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStorage(Protocol):
    async def put(self, key: str, content: bytes, *, content_type: str) -> str:
        """Store content and return a durable URI."""

    async def get(self, uri: str) -> bytes:
        """Read the complete object."""

    async def delete(self, uri: str) -> None:
        """Delete an object idempotently."""

    async def exists(self, uri: str) -> bool:
        """Return whether the object currently exists."""

    def iter_uris(self, prefix: str = "") -> AsyncIterator[str]:
        """Iterate stored URIs for retention and orphan cleanup."""
