"""Application-lifetime reusable model and HTTP resources."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from config import settings


@dataclass(slots=True)
class ApplicationResources:
    http_client: httpx.AsyncClient
    embedder: Any


_resources: ApplicationResources | None = None
_lock = asyncio.Lock()


async def initialize_resources() -> ApplicationResources:
    global _resources
    if _resources is not None:
        return _resources
    async with _lock:
        if _resources is None:
            from rag.retriever import _build_embedder

            embedder = await asyncio.to_thread(_build_embedder)
            client = httpx.AsyncClient(timeout=httpx.Timeout(60.0), limits=httpx.Limits(max_connections=20))
            _resources = ApplicationResources(http_client=client, embedder=embedder)
    return _resources


async def get_resources() -> ApplicationResources:
    return await initialize_resources()


async def close_resources() -> None:
    global _resources
    if _resources is None:
        return
    await _resources.http_client.aclose()
    _resources = None


def embedding_model_name(embedder: Any) -> str:
    """Return the real encoder identity, including deterministic fallback encoders."""
    return str(getattr(embedder, "model_name", settings.RAG_EMBEDDING_MODEL))
