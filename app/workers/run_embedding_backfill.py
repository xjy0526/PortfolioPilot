"""Backfill pgvector rows for the currently configured real embedding model."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json

import numpy as np
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.core.resources import (
    close_resources,
    embedding_model_name,
    embedding_model_version,
    get_resources,
)
from app.db.models import ChunkEmbedding, DocumentChunk
from app.db.session import AsyncSessionFactory, dispose_async_engine
from config import settings


async def backfill_embeddings(*, batch_size: int = 64) -> dict[str, object]:
    resources = await get_resources()
    embedder = resources.embedder
    if embedder is None:
        raise RuntimeError(f"Embedding provider unavailable: {resources.embedding_error}")
    if not embedder.semantic:
        raise RuntimeError("Embedding backfill requires a semantic embedding provider")
    model_name = embedding_model_name(embedder)
    model_version = embedding_model_version(embedder)
    updated = 0

    while True:
        async with AsyncSessionFactory() as session:
            current = exists(
                select(ChunkEmbedding.id).where(
                    ChunkEmbedding.chunk_id == DocumentChunk.id,
                    ChunkEmbedding.model_name == model_name,
                    ChunkEmbedding.model_version == model_version,
                    ChunkEmbedding.content_hash == DocumentChunk.content_hash,
                )
            )
            rows = list(
                (
                    await session.execute(
                        select(
                            DocumentChunk.id,
                            DocumentChunk.text_content,
                            DocumentChunk.content_hash,
                        )
                        .where(~current)
                        .order_by(DocumentChunk.id)
                        .limit(max(1, batch_size))
                    )
                ).all()
            )
        if not rows:
            break

        vectors = np.asarray(
            await asyncio.to_thread(embedder.encode, [row.text_content for row in rows]),
            dtype=np.float32,
        )
        expected = (len(rows), settings.RAG_EMBEDDING_DIMENSION)
        if vectors.shape != expected:
            raise RuntimeError(f"Embedding shape mismatch: expected {expected}, got {vectors.shape}")

        async with AsyncSessionFactory.begin() as session:
            for row, vector in zip(rows, vectors, strict=True):
                compatibility_model = f"{model_name}@{model_version}"[:255]
                statement = (
                    insert(ChunkEmbedding)
                    .values(
                        chunk_id=row.id,
                        embedding_model=compatibility_model,
                        model_name=model_name,
                        model_version=model_version,
                        dimensions=settings.RAG_EMBEDDING_DIMENSION,
                        embedding=vector.tolist(),
                        content_hash=row.content_hash
                        or hashlib.sha256(row.text_content.encode()).hexdigest(),
                    )
                    .on_conflict_do_update(
                        constraint="uq_chunk_embeddings_chunk_model_version",
                        set_={
                            "embedding_model": compatibility_model,
                            "dimensions": settings.RAG_EMBEDDING_DIMENSION,
                            "embedding": vector.tolist(),
                            "content_hash": row.content_hash,
                        },
                    )
                )
                await session.execute(statement)
                updated += 1
    return {
        "status": "completed",
        "model_name": model_name,
        "model_version": model_version,
        "dimensions": settings.RAG_EMBEDDING_DIMENSION,
        "updated": updated,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Backfill current pgvector embeddings")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                await backfill_embeddings(batch_size=args.batch_size),
                ensure_ascii=False,
            )
        )
    finally:
        await close_resources()
        await dispose_async_engine()


if __name__ == "__main__":
    asyncio.run(_main())
