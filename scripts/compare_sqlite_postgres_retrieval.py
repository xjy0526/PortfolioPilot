"""Compare public-document recall between legacy SQLite and PostgreSQL RAG."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.principal import Principal
from app.core.resources import close_resources, get_resources
from app.services.research_knowledge import PostgresKnowledgeService
from config import settings
from rag.models import PermissionContext
from rag.repository import KnowledgeRepository
from rag.service import KnowledgeBaseService


async def compare(
    *,
    sqlite_path: Path,
    database_url: str,
    queries: list[str],
    top_k: int = 5,
) -> dict:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {sqlite_path}")
    connection = sqlite3.connect(sqlite_path)
    resources = await get_resources()
    legacy = KnowledgeBaseService(
        KnowledgeRepository(connection, ensure_schema=False),
        resources.embedder,
    )
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    principal = Principal("retrieval-validation", frozenset({"public"}))
    cases = []
    try:
        async with factory() as session:
            current = PostgresKnowledgeService(session, embedder=resources.embedder)
            for query in queries:
                sqlite_hits = legacy.retrieve(
                    query,
                    top_k=top_k,
                    permission_context=PermissionContext(permission_groups=["public"]),
                )
                pg_hits = (
                    await current.retrieve_with_status(query, top_k=top_k, principal=principal)
                )["citations"]
                sqlite_keys = {str(item["document_id"]) for item in sqlite_hits}
                pg_keys = {str(item.get("document_key") or item["document_id"]) for item in pg_hits}
                missing = sorted(sqlite_keys - pg_keys)
                cases.append(
                    {
                        "query": query,
                        "sqlite_public_documents": sorted(sqlite_keys),
                        "postgres_public_documents": sorted(pg_keys),
                        "missing_from_postgres": missing,
                        "no_unexpected_public_recall_regression": not missing,
                    }
                )
        return {
            "top_k": top_k,
            "case_count": len(cases),
            "no_unexpected_public_recall_regression": all(
                item["no_unexpected_public_recall_regression"] for item in cases
            ),
            "cases": cases,
        }
    finally:
        connection.close()
        await close_resources()
        await engine.dispose()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Compare SQLite and PostgreSQL public RAG recall")
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    report = await compare(
        sqlite_path=args.sqlite,
        database_url=args.database_url,
        queries=args.query,
        top_k=args.top_k,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["no_unexpected_public_recall_regression"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(_main())
