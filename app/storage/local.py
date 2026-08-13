"""Filesystem object storage for development and tests."""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse


class LocalObjectStorage:
    def __init__(self, root: str | Path, *, bucket: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.bucket = _safe_part(bucket)
        (self.root / self.bucket).mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, content: bytes, *, content_type: str) -> str:
        del content_type
        path = self._path_for_key(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)

        await asyncio.to_thread(write)
        return self._uri(key)

    async def get(self, uri: str) -> bytes:
        return await asyncio.to_thread(self._path_for_uri(uri).read_bytes)

    async def delete(self, uri: str) -> None:
        await asyncio.to_thread(self._path_for_uri(uri).unlink, missing_ok=True)

    async def exists(self, uri: str) -> bool:
        return await asyncio.to_thread(self._path_for_uri(uri).is_file)

    async def iter_uris(self, prefix: str = "") -> AsyncIterator[str]:
        base = self._path_for_key(prefix) if prefix else self.root / self.bucket
        paths = await asyncio.to_thread(
            lambda: sorted(path for path in base.rglob("*") if path.is_file())
            if base.exists()
            else []
        )
        bucket_root = self.root / self.bucket
        for path in paths:
            yield self._uri(path.relative_to(bucket_root).as_posix())

    def _path_for_uri(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "local" or parsed.netloc != self.bucket:
            raise ValueError("Object URI does not belong to this local storage bucket")
        return self._path_for_key(unquote(parsed.path.lstrip("/")))

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
