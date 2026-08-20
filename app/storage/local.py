"""Filesystem object storage for development and tests."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

from app.storage.base import StoredObject


class LocalObjectStorage:
    def __init__(self, root: str | Path, *, bucket: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.bucket = _safe_part(bucket)
        (self.root / self.bucket).mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, content: bytes, *, content_type: str) -> str:
        stored = await self.put_object(key, content, content_type=content_type)
        return self._uri(stored.key)

    async def put_object(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str,
    ) -> StoredObject:
        path = self._path_for_key(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)

        await asyncio.to_thread(write)
        checksum = hashlib.sha256(content).hexdigest()
        return StoredObject(
            key=_safe_key(key),
            checksum=checksum,
            content_type=content_type,
            version=checksum,
        )

    async def get(self, uri: str) -> bytes:
        return await self.get_object(self._key_from_reference(uri))

    async def get_object(self, key: str, *, version: str = "") -> bytes:
        content = await asyncio.to_thread(self._path_for_key(key).read_bytes)
        if version and hashlib.sha256(content).hexdigest() != version:
            raise ValueError("Local object version does not match stored content")
        return content

    async def delete(self, uri: str) -> None:
        await self.delete_object(self._key_from_reference(uri))

    async def delete_object(self, key: str, *, version: str = "") -> None:
        del version
        await asyncio.to_thread(self._path_for_key(key).unlink, missing_ok=True)

    async def exists(self, uri: str) -> bool:
        return await self.object_exists(self._key_from_reference(uri))

    async def object_exists(self, key: str, *, version: str = "") -> bool:
        path = self._path_for_key(key)
        exists = await asyncio.to_thread(path.is_file)
        if not exists or not version:
            return exists
        content = await asyncio.to_thread(path.read_bytes)
        return hashlib.sha256(content).hexdigest() == version

    async def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        base = self._path_for_key(prefix) if prefix else self.root / self.bucket
        paths = await asyncio.to_thread(
            lambda: sorted(path for path in base.rglob("*") if path.is_file())
            if base.exists()
            else []
        )
        bucket_root = self.root / self.bucket
        for path in paths:
            yield path.relative_to(bucket_root).as_posix()

    async def iter_uris(self, prefix: str = "") -> AsyncIterator[str]:
        async for key in self.iter_keys(prefix):
            yield self._uri(key)

    async def check_access(self) -> None:
        bucket_root = self.root / self.bucket

        def probe() -> None:
            bucket_root.mkdir(parents=True, exist_ok=True)
            probe_path = bucket_root / f".preflight-{uuid.uuid4().hex}"
            probe_path.write_bytes(b"")
            probe_path.unlink()

        await asyncio.to_thread(probe)

    def _key_from_reference(self, value: str) -> str:
        if value.startswith("local://"):
            parsed = urlparse(value)
            if parsed.netloc != self.bucket:
                raise ValueError("Object URI does not belong to this local storage bucket")
            return _safe_key(unquote(parsed.path.lstrip("/")))
        return _safe_key(value)

    def _path_for_key(self, key: str) -> Path:
        normalized = _safe_key(key)
        candidate = (self.root / self.bucket / normalized).resolve()
        bucket_root = (self.root / self.bucket).resolve()
        if not candidate.is_relative_to(bucket_root):
            raise ValueError("Object key escapes the configured storage root")
        return candidate

    def _uri(self, key: str) -> str:
        return f"local://{self.bucket}/{quote(_safe_key(key), safe='/')}"


def _safe_key(value: str) -> str:
    path = PurePosixPath(str(value).strip().lstrip("/"))
    if not str(path) or str(path) == "." or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Object key is empty or unsafe")
    return path.as_posix()


def _safe_part(value: str) -> str:
    normalized = str(value).strip()
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError("Invalid object storage bucket")
    return normalized
