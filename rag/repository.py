"""Explicit-connection SQLite reader kept only for migration validation."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from rag.models import DocumentMetadata, IngestionJob, PermissionContext


class KnowledgeRepository:
    def __init__(self, connection: sqlite3.Connection, *, ensure_schema: bool = True):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if ensure_schema:
            ensure_knowledge_schema(self.conn)

    def create_job(self, job_id: str, filename: str, document_id: str | None, checksum: str) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO ingestion_jobs
               (job_id, document_id, status, filename, checksum, created_at, started_at)
               VALUES (?, ?, 'processing', ?, ?, ?, ?)""",
            (job_id, document_id, filename, checksum, now, now),
        )
        self.conn.commit()

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        document_id: str | None = None,
        version_id: str | None = None,
        chunks_created: int = 0,
        error_message: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE ingestion_jobs SET
               status=?, document_id=COALESCE(?, document_id), version_id=COALESCE(?, version_id),
               chunks_created=?, error_message=?, completed_at=? WHERE job_id=?""",
            (status, document_id, version_id, chunks_created, error_message, _now(), job_id),
        )
        self.conn.commit()

    def get_job(self, job_id: str) -> IngestionJob | None:
        row = self.conn.execute("SELECT * FROM ingestion_jobs WHERE job_id=?", (job_id,)).fetchone()
        return IngestionJob(**dict(row)) if row else None

    def find_checksum(self, checksum: str, document_id: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT document_id, version_id, version, checksum FROM knowledge_document_versions WHERE checksum=?"
        params: list[Any] = [checksum]
        if document_id:
            sql += " AND document_id=?"
            params.append(document_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def store_version(
        self,
        *,
        metadata: dict[str, Any],
        version_id: str,
        filename: str,
        parser: str,
        content_text: str,
        checksum: str,
        chunks: list[dict[str, Any]],
    ) -> tuple[int, str]:
        document_id = metadata["document_id"]
        existing = self.conn.execute(
            "SELECT * FROM knowledge_documents WHERE document_id=?", (document_id,)
        ).fetchone()
        version = int(existing["current_version"]) + 1 if existing else 1
        now = _now()
        permission_groups = _json_list(metadata.get("permission_groups") or ["public"])
        tickers = _json_list(metadata.get("tickers") or [])
        fund_codes = _json_list(metadata.get("fund_codes") or [])

        try:
            self.conn.execute("BEGIN")
            if existing:
                self.conn.execute(
                    """UPDATE knowledge_documents SET
                       title=?, source_type=?, department=?, tickers=?, fund_codes=?,
                       publish_date=?, effective_from=?, effective_to=?, current_version=?, confidentiality=?,
                       permission_groups=?, author=?, checksum=?, ingestion_status='completed', updated_at=?
                       WHERE document_id=?""",
                    (
                        metadata["title"], metadata.get("source_type", "uploaded"),
                        metadata.get("department", "Research"), tickers, fund_codes,
                        _date_value(metadata.get("publish_date")),
                        _date_value(metadata.get("effective_from")), _date_value(metadata.get("effective_to")),
                        version, metadata.get("confidentiality", "public"), permission_groups,
                        metadata.get("author", ""), checksum, now, document_id,
                    ),
                )
            else:
                self.conn.execute(
                    """INSERT INTO knowledge_documents
                       (document_id, title, source_type, department, tickers, fund_codes,
                        publish_date, effective_from, effective_to, current_version, published_version,
                        confidentiality, permission_groups, author, checksum, ingestion_status,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'completed', 'draft', ?, ?)""",
                    (
                        document_id, metadata["title"], metadata.get("source_type", "uploaded"),
                        metadata.get("department", "Research"), tickers, fund_codes,
                        _date_value(metadata.get("publish_date")), _date_value(metadata.get("effective_from")),
                        _date_value(metadata.get("effective_to")), version,
                        metadata.get("confidentiality", "public"), permission_groups,
                        metadata.get("author", ""), checksum, now, now,
                    ),
                )

            self.conn.execute(
                """INSERT INTO knowledge_document_versions
                   (version_id, document_id, version, checksum, source_filename, parser,
                    content_text, content_length, chunk_count, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
                (
                    version_id, document_id, version, checksum, filename, parser,
                    content_text, len(content_text), len(chunks), now,
                ),
            )
            self.conn.executemany(
                """INSERT INTO knowledge_chunks
                   (chunk_id, document_id, version_id, version, chunk_index, title,
                    section, page_number, text, char_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chunk["chunk_id"], document_id, version_id, version, chunk["chunk_index"],
                        chunk["title"], chunk.get("section", ""), chunk.get("page_number"),
                        chunk["text"], len(chunk["text"]), now,
                    )
                    for chunk in chunks
                ],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return version, version_id

    def publish(self, document_id: str) -> DocumentMetadata | None:
        row = self.conn.execute(
            "SELECT current_version FROM knowledge_documents WHERE document_id=?", (document_id,)
        ).fetchone()
        if not row:
            return None
        now = _now()
        today = date.today().isoformat()
        self.conn.execute(
            """UPDATE knowledge_documents SET status='published', published_version=current_version,
               publish_date=COALESCE(publish_date, ?), updated_at=? WHERE document_id=?""",
            (today, now, document_id),
        )
        self.conn.commit()
        return self.get_document_unchecked(document_id)

    def deactivate(self, document_id: str) -> DocumentMetadata | None:
        cursor = self.conn.execute(
            "UPDATE knowledge_documents SET status='inactive', updated_at=? WHERE document_id=?",
            (_now(), document_id),
        )
        self.conn.commit()
        return self.get_document_unchecked(document_id) if cursor.rowcount else None

    def list_documents(self, context: PermissionContext) -> list[DocumentMetadata]:
        clause, params = _permission_clause(context, "d")
        rows = self.conn.execute(
            f"SELECT d.* FROM knowledge_documents d WHERE {clause} ORDER BY d.updated_at DESC",
            params,
        ).fetchall()
        return [_metadata_from_row(row) for row in rows]

    def get_document(self, document_id: str, context: PermissionContext) -> DocumentMetadata | None:
        clause, params = _permission_clause(context, "d")
        row = self.conn.execute(
            f"SELECT d.* FROM knowledge_documents d WHERE d.document_id=? AND {clause}",
            [document_id, *params],
        ).fetchone()
        return _metadata_from_row(row) if row else None

    def get_document_unchecked(self, document_id: str) -> DocumentMetadata | None:
        row = self.conn.execute(
            "SELECT * FROM knowledge_documents WHERE document_id=?", (document_id,)
        ).fetchone()
        return _metadata_from_row(row) if row else None

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT version_id, document_id, version, checksum, source_filename, parser,
                      content_length, chunk_count, status, error_message, created_at
               FROM knowledge_document_versions WHERE document_id=? ORDER BY version DESC""",
            (document_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def fetch_retrievable_chunks(
        self,
        context: PermissionContext,
        *,
        as_of: date | None = None,
        tickers: list[str] | None = None,
        fund_codes: list[str] | None = None,
        source_types: list[str] | None = None,
        publish_date_from: date | None = None,
        publish_date_to: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch only authorized, active chunks before any embedding or scoring."""
        effective_date = (as_of or date.today()).isoformat()
        permission_clause, permission_params = _permission_clause(context, "d")
        metadata_clauses: list[str] = []
        metadata_params: list[Any] = []
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            metadata_clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(d.tickers) t WHERE upper(t.value) IN ({placeholders}))"
            )
            metadata_params.extend(ticker.upper() for ticker in tickers)
        if fund_codes:
            placeholders = ",".join("?" for _ in fund_codes)
            metadata_clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(d.fund_codes) f WHERE upper(f.value) IN ({placeholders}))"
            )
            metadata_params.extend(code.upper() for code in fund_codes)
        if source_types:
            type_clauses = []
            for source_type in source_types:
                aliases = {
                    "announcement": ("announcement", "notice"),
                    "research_report": ("research", "report"),
                    "policy": ("policy", "procedure"),
                    "faq": ("faq",),
                }.get(source_type.lower(), (source_type.lower(),))
                alias_clauses = ["lower(d.source_type) LIKE ?" for _ in aliases]
                type_clauses.append(f"({' OR '.join(alias_clauses)})")
                metadata_params.extend(f"%{alias}%" for alias in aliases)
            metadata_clauses.append(f"({' OR '.join(type_clauses)})")
        if publish_date_from:
            metadata_clauses.append("d.publish_date IS NOT NULL AND d.publish_date >= ?")
            metadata_params.append(publish_date_from.isoformat())
        if publish_date_to:
            metadata_clauses.append("d.publish_date IS NOT NULL AND d.publish_date <= ?")
            metadata_params.append(publish_date_to.isoformat())
        metadata_sql = " AND ".join(metadata_clauses) if metadata_clauses else "1=1"
        rows = self.conn.execute(
            f"""SELECT c.*, v.source_filename, d.source_type, d.department, d.tickers, d.fund_codes,
                       d.publish_date, d.effective_from, d.effective_to,
                       d.confidentiality, d.permission_groups, d.author
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.document_id=c.document_id
                JOIN knowledge_document_versions v ON v.version_id=c.version_id
                WHERE {metadata_sql}
                  AND {permission_clause}
                  AND d.status='published'
                  AND d.published_version IS NOT NULL
                  AND c.version=d.published_version
                  AND (d.effective_from IS NULL OR d.effective_from <= ?)
                  AND (d.effective_to IS NULL OR d.effective_to >= ?)
                ORDER BY c.document_id, c.chunk_index""",
            [*metadata_params, *permission_params, effective_date, effective_date],
        ).fetchall()
        return [_decode_row(dict(row)) for row in rows]

    def count_documents(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM knowledge_documents").fetchone()
        return int(row["count"])


def _permission_clause(context: PermissionContext, alias: str) -> tuple[str, list[Any]]:
    if context.is_admin:
        return "1=1", []
    groups = sorted({group.strip() for group in context.permission_groups if group.strip()})
    public_clause = f"{alias}.confidentiality='public'"
    if not groups:
        return public_clause, []
    placeholders = ",".join("?" for _ in groups)
    return (
        f"({public_clause} OR EXISTS (SELECT 1 FROM json_each({alias}.permission_groups) pg "
        f"WHERE pg.value IN ({placeholders})))",
        groups,
    )


def _metadata_from_row(row: sqlite3.Row) -> DocumentMetadata:
    payload = _decode_row(dict(row))
    payload["version"] = payload.pop("current_version")
    return DocumentMetadata(**payload)


def _decode_row(payload: dict[str, Any]) -> dict[str, Any]:
    for field in ("tickers", "fund_codes", "permission_groups"):
        if field in payload and isinstance(payload[field], str):
            try:
                payload[field] = json.loads(payload[field])
            except json.JSONDecodeError:
                payload[field] = []
    return payload


def _json_list(value: list[Any]) -> str:
    return json.dumps(sorted({str(item).strip() for item in value if str(item).strip()}), ensure_ascii=False)


def _date_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_knowledge_schema(conn: sqlite3.Connection) -> None:
    """Idempotent standalone migration used by tests and alternate connections."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            document_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'uploaded',
            department TEXT NOT NULL DEFAULT 'Research', tickers TEXT NOT NULL DEFAULT '[]',
            fund_codes TEXT NOT NULL DEFAULT '[]', publish_date TEXT, effective_from TEXT, effective_to TEXT,
            current_version INTEGER NOT NULL DEFAULT 0, published_version INTEGER,
            confidentiality TEXT NOT NULL DEFAULT 'public', permission_groups TEXT NOT NULL DEFAULT '["public"]',
            author TEXT NOT NULL DEFAULT '', checksum TEXT NOT NULL DEFAULT '',
            ingestion_status TEXT NOT NULL DEFAULT 'pending', status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_document_versions (
            version_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
            checksum TEXT NOT NULL, source_filename TEXT NOT NULL, parser TEXT NOT NULL,
            content_text TEXT NOT NULL, content_length INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'processing',
            error_message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES knowledge_documents(document_id),
            UNIQUE(document_id, version), UNIQUE(document_id, checksum)
        );
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version_id TEXT NOT NULL,
            version INTEGER NOT NULL, chunk_index INTEGER NOT NULL, title TEXT NOT NULL,
            section TEXT NOT NULL DEFAULT '', page_number INTEGER, text TEXT NOT NULL,
            char_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES knowledge_documents(document_id),
            FOREIGN KEY(version_id) REFERENCES knowledge_document_versions(version_id),
            UNIQUE(version_id, chunk_index)
        );
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            job_id TEXT PRIMARY KEY, document_id TEXT, version_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending', filename TEXT NOT NULL, checksum TEXT NOT NULL DEFAULT '',
            chunks_created INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_documents_status ON knowledge_documents(status, effective_from, effective_to);
        CREATE INDEX IF NOT EXISTS idx_knowledge_versions_document ON knowledge_document_versions(document_id, version);
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document_version ON knowledge_chunks(document_id, version);
        CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document ON ingestion_jobs(document_id, created_at);
        """
    )
    conn.commit()
