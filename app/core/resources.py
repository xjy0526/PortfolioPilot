"""Application-lifetime reusable model and HTTP resources."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.providers.embeddings import EmbeddingProvider, build_embedding_provider
from app.storage.base import ObjectStorage
from app.storage.factory import build_object_storage
from config import settings


@dataclass(slots=True)
class ApplicationResources:
    http_client: httpx.AsyncClient
    embedder: EmbeddingProvider | None
    object_storage: ObjectStorage
    embedding_error: str = ""


_resources: ApplicationResources | None = None
_lock = asyncio.Lock()


async def initialize_resources() -> ApplicationResources:
    global _resources
    if _resources is not None:
        return _resources
    async with _lock:
        if _resources is None:
            embedder: EmbeddingProvider | None = None
            embedding_error = ""
            try:
                embedder = await asyncio.to_thread(build_embedding_provider)
            except Exception as exc:
                embedding_error = f"{type(exc).__name__}: {exc}"
            client = httpx.AsyncClient(timeout=httpx.Timeout(60.0), limits=httpx.Limits(max_connections=20))
            storage = await asyncio.to_thread(build_object_storage)
            _resources = ApplicationResources(
                http_client=client,
                embedder=embedder,
                object_storage=storage,
                embedding_error=embedding_error,
            )
    return _resources


async def get_resources() -> ApplicationResources:
    return await initialize_resources()


async def close_resources() -> None:
    global _resources
    if _resources is None:
        return
    await _resources.http_client.aclose()
    _resources = None


def embedding_model_name(embedder: EmbeddingProvider) -> str:
    """Return the real encoder identity, including deterministic fallback encoders."""
    return str(getattr(embedder, "model_name", settings.RAG_EMBEDDING_MODEL))


def embedding_model_version(embedder: EmbeddingProvider) -> str:
    return str(getattr(embedder, "model_version", "unknown"))
