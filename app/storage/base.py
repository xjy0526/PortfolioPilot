"""Object storage contract used by web uploads and asynchronous workers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    checksum: str
    content_type: str
    version: str = ""


@runtime_checkable
class ObjectStorage(Protocol):
    async def put_object(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        """Store content and return durable, database-safe object metadata."""

    async def get_object(self, key: str, *, version: str = "") -> bytes:
        """Read an object by key and optional provider version."""

    async def delete_object(self, key: str, *, version: str = "") -> None:
        """Delete an object version idempotently."""

    async def object_exists(self, key: str, *, version: str = "") -> bool:
        """Return whether an object version exists."""

    def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        """Iterate object keys for retention and orphan cleanup."""

    async def check_access(self) -> None:
        """Raise when the configured bucket or root cannot be accessed."""

    # Compatibility methods for jobs created before object-key persistence.
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
