"""PostgreSQL and pgvector backed governed research knowledge service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.core.resources import (
    embedding_model_name,
    embedding_model_version,
    get_resources,
)
from app.providers.embeddings import EmbeddingProvider
from app.storage.base import ObjectStorage
from app.db.models import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    ResearchDocument,
)
from app.db.repositories.governance import ResearchRepository
from config import settings
from rag.hybrid_retriever import Reranker, deduplicate_candidates, reciprocal_rank_fusion
from rag.models import DocumentMetadata
from rag.parsers import parse_document, structured_chunks
from rag.query import extract_query_intent
from time_utils import utc_now


class KnowledgeIngestionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedIngestion:
    job_id: uuid.UUID
    checksum: str
    source_checksum: str
    filename: str
    title: str
    parser: str
    content_type: str
    content_length: int
    page_count: int
    metadata: dict[str, Any]
    content_text: str
    chunks: tuple[dict[str, Any], ...]
    vectors: np.ndarray
    model_name: str
    model_version: str


class PostgresKnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedder: EmbeddingProvider | None = None,
        storage: ObjectStorage | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.session = session
        self.repository = ResearchRepository(session)
        self._embedder = embedder
        self._storage = storage
        self.reranker = reranker

    async def queue_upload(
        self,
        *,
        content: bytes,
        filename: str,
        metadata: dict[str, Any],
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[IngestionJob, bool]:
        principal.require_role("knowledge_admin", "platform_admin")
        if not filename or Path(filename).suffix.lower() not in {".txt", ".md", ".csv", ".pdf"}:
            raise KnowledgeIngestionError("Supported document types are txt, md, csv and pdf")
        if not content:
            raise KnowledgeIngestionError("Document content is empty")
        if len(content) > settings.RAG_MAX_UPLOAD_BYTES:
            raise KnowledgeIngestionError(
                f"File size limit exceeded: {len(content)} > {settings.RAG_MAX_UPLOAD_BYTES}"
            )
        metadata = dict(metadata)
        confidentiality = str(metadata.get("confidentiality") or "public").lower()
        requested_groups = {
            str(item).strip().lower()
            for item in metadata.get("permission_groups", [])
            if str(item).strip()
        }
        if confidentiality == "public":
            metadata["permission_groups"] = ["public"]
        else:
            if not requested_groups or "public" in requested_groups:
                raise KnowledgeIngestionError(
                    "Non-public documents require explicit non-public permission_groups"
                )
            unauthorized_groups = requested_groups - principal.permission_groups
            if unauthorized_groups and not principal.is_platform_admin:
                raise KnowledgeIngestionError(
                    "Document permission_groups must be granted to the authenticated principal"
                )
            metadata["permission_groups"] = sorted(requested_groups)
        try:
            DocumentMetadata(
                document_id=str(metadata.get("document_id") or "pending-validation"),
                title=str(metadata.get("title") or Path(filename).stem),
                **{
                    key: value
                    for key, value in metadata.items()
                    if key not in {"document_id", "document_key", "title"}
                },
            )
        except ValueError as exc:
            raise KnowledgeIngestionError(str(exc)) from exc
        key = idempotency_key.strip()
        if not key:
            raise KnowledgeIngestionError("Idempotency-Key header is required")
        checksum = hashlib.sha256(content).hexdigest()
        business_scene = "knowledge_ingestion"
        existing = await self.repository.find_job(
            principal.user_id, key, business_scene=business_scene
        )
        if existing:
            if existing.checksum != checksum:
                raise KnowledgeIngestionError(
                    "Idempotency-Key was already used for different document content"
                )
            return existing, True

        storage_name = f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
        prefix = settings.object_storage_prefix
        object_key = f"{prefix}/{storage_name}" if prefix else storage_name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        storage = await self._get_storage()
        stored = await storage.put_object(object_key, content, content_type=content_type)
        if stored.checksum != checksum:
            raise KnowledgeIngestionError("Object storage checksum does not match upload")
        job_id = uuid.uuid4()
        statement = (
            insert(IngestionJob)
            .values(
                id=job_id,
                user_id=principal.user_id,
                business_scene=business_scene,
                idempotency_key=key,
                filename=Path(filename).name,
                object_key=stored.key,
                object_version=stored.version,
                object_owner=principal.user_id,
                object_permission_groups=list(metadata["permission_groups"]),
                checksum=checksum,
                content_length=len(content),
                content_type=content_type,
                retention_until=None,
                code_version=settings.resolved_code_version,
                status="pending",
                metadata_json=metadata,
                chunks_created=0,
                retry_count=0,
                error_message="",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IngestionJob.user_id,
                    IngestionJob.business_scene,
                    IngestionJob.idempotency_key,
                ]
            )
            .returning(IngestionJob)
        )
        job = (await self.session.execute(statement)).scalar_one_or_none()
        if job is not None:
            return job, False
        await storage.delete_object(stored.key, version=stored.version)
        replay = await self.repository.find_job(
            principal.user_id, key, business_scene=business_scene
        )
        if replay is None:
            raise RuntimeError("Idempotent ingestion insert did not return a job")
        if replay.checksum != checksum:
            raise KnowledgeIngestionError(
                "Idempotency-Key was concurrently used for different document content"
            )
        return replay, True

    async def prepare_job(self, job: IngestionJob) -> PreparedIngestion:
        """Parse and embed outside a database transaction."""
        if job.status not in {"pending", "processing", "failed"}:
            raise KnowledgeIngestionError(f"Job is not processable from status {job.status}")
        if not job.object_key:
            raise KnowledgeIngestionError(
                "Ingestion source object is unavailable; re-upload the legacy document"
            )
        storage = await self._get_storage()
        content = await storage.get_object(
            job.object_key,
            version=job.object_version,
        )
        if hashlib.sha256(content).hexdigest() != job.checksum:
            raise KnowledgeIngestionError("Stored object checksum does not match ingestion job")
        metadata = dict(job.metadata_json or {})
        source_checksum = str(metadata.get("source_checksum") or job.checksum)
        fallback_title = str(metadata.get("title") or Path(job.filename).stem).strip()
        async with asyncio.timeout(settings.RAG_PARSE_TIMEOUT_SECONDS):
            title, blocks, parser = await asyncio.to_thread(
                parse_document,
                content,
                job.filename,
                fallback_title,
                max_pdf_pages=settings.RAG_MAX_PDF_PAGES,
            )
            chunks = await asyncio.to_thread(
                structured_chunks,
                blocks,
                chunk_size=max(200, settings.RAG_CHUNK_SIZE),
            )
        if not chunks:
            raise KnowledgeIngestionError("Document contains no indexable text")
        if len(chunks) > settings.RAG_MAX_CHUNKS:
            raise KnowledgeIngestionError(
                f"Chunk limit exceeded: {len(chunks)} > {settings.RAG_MAX_CHUNKS}"
            )

        metadata.setdefault("document_id", str(metadata.get("document_key") or uuid.uuid4()))
        metadata["title"] = str(metadata.get("title") or title or fallback_title)
        metadata["checksum"] = job.checksum
        # ``prepared`` is an internal worker phase, not a persisted public job
        # state. Keep metadata aligned with the governed status vocabulary.
        metadata["ingestion_status"] = "processing"
        validated = DocumentMetadata(**metadata)
        texts = [str(item["text"]) for item in chunks]
        embedder = await self._get_embedder()
        vectors = np.asarray(await asyncio.to_thread(embedder.encode, texts), dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape != (
            len(chunks),
            settings.RAG_EMBEDDING_DIMENSION,
        ):
            raise KnowledgeIngestionError(
                "Embedding dimension mismatch: "
                f"expected {settings.RAG_EMBEDDING_DIMENSION}, got {tuple(vectors.shape)}"
            )
        return PreparedIngestion(
            job_id=job.id,
            checksum=job.checksum,
            source_checksum=source_checksum,
            filename=job.filename,
            title=metadata["title"],
            parser=parser,
            content_type=job.content_type,
            content_length=len(content),
            page_count=max((block.page_number or 0 for block in blocks), default=0),
            metadata=validated.model_dump(mode="json"),
            content_text="\n\n".join(block.text for block in blocks),
            chunks=tuple(dict(item) for item in chunks),
            vectors=vectors,
            model_name=embedding_model_name(embedder),
            model_version=embedding_model_version(embedder),
        )

    async def persist_prepared(
        self, job: IngestionJob, prepared: PreparedIngestion
    ) -> IngestionJob:
        """Persist one prepared document in a short, atomic DB transaction."""
        if prepared.job_id != job.id or prepared.checksum != job.checksum:
            raise KnowledgeIngestionError("Prepared ingestion does not match the claimed job")
        metadata = dict(prepared.metadata)
        validated = DocumentMetadata(**metadata)
        document = await self.session.scalar(
            select(ResearchDocument).where(
                ResearchDocument.document_key == validated.document_id
            ).with_for_update()
        )
        if document is None:
            document = ResearchDocument(
                document_key=validated.document_id,
                title=validated.title,
                source_type=validated.source_type,
                department=validated.department,
                author=validated.author,
                status="draft",
                current_version=0,
                metadata_json={},
            )
            self.session.add(document)
            await self.session.flush()
        duplicate = await self.repository.find_checksum(
            prepared.source_checksum, document_id=document.id
        )
        if duplicate:
            job.document_id = document.id
            job.version_id = duplicate.id
            job.status = "duplicate"
            job.completed_at = utc_now()
            job.retention_until = _retention_until(
                settings.RAG_SUCCESS_SOURCE_RETENTION_DAYS
            )
            return job

        current = await self.session.scalar(
            select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == document.id)
        )
        version_number = int(current or 0) + 1
        version = DocumentVersion(
            document_id=document.id,
            version=version_number,
            checksum=prepared.source_checksum,
            stored_content_checksum=job.checksum,
            source_filename=job.filename,
            parser=prepared.parser,
            content_type=prepared.content_type,
            metadata_json=validated.model_dump(mode="json"),
            content_text=prepared.content_text,
            content_length=prepared.content_length,
            page_count=prepared.page_count,
            chunk_count=len(prepared.chunks),
            status="completed",
            error_message="",
        )
        self.session.add(version)
        await self.session.flush()

        for item, vector in zip(prepared.chunks, prepared.vectors, strict=True):
            chunk_text = str(item["text"])
            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            chunk = DocumentChunk(
                document_id=document.id,
                version_id=version.id,
                chunk_index=int(item["chunk_index"]),
                title=str(item.get("title") or validated.title),
                section=str(item.get("section") or ""),
                page=item.get("page_number"),
                text_content=chunk_text,
                content_hash=content_hash,
                ticker=validated.tickers[0] if validated.tickers else None,
                fund_code=validated.fund_codes[0] if validated.fund_codes else None,
                tickers=[item.upper() for item in validated.tickers],
                fund_codes=[item.upper() for item in validated.fund_codes],
                document_type=validated.source_type,
                publish_date=validated.publish_date,
                confidentiality=validated.confidentiality,
                permission_groups=[item.lower() for item in validated.permission_groups],
                published_version=False,
                effective_from=validated.effective_from,
                effective_to=validated.effective_to,
            )
            self.session.add(chunk)
            await self.session.flush()
            self.session.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_model=(
                        f"{prepared.model_name}@{prepared.model_version}"
                    )[:255],
                    model_name=prepared.model_name,
                    model_version=prepared.model_version,
                    dimensions=settings.RAG_EMBEDDING_DIMENSION,
                    embedding=vector.tolist(),
                    content_hash=content_hash,
                )
            )

        if document.status != "published":
            document.title = validated.title
            document.source_type = validated.source_type
            document.department = validated.department
            document.author = validated.author
        document.current_version = version_number
        if document.status != "published":
            document.metadata_json = {
                **validated.model_dump(mode="json"),
                "source_checksum": prepared.source_checksum,
                "stored_content_checksum": job.checksum,
            }
        job.document_id = document.id
        job.version_id = version.id
        job.status = "completed"
        job.chunks_created = len(prepared.chunks)
        job.completed_at = utc_now()
        job.retention_until = _retention_until(settings.RAG_SUCCESS_SOURCE_RETENTION_DAYS)
        await self.session.flush()
        return job

    async def process_job(self, job: IngestionJob) -> IngestionJob:
        """Compatibility helper; workers use explicit prepare/persist phases."""
        prepared = await self.prepare_job(job)
        job.status = "processing"
        job.started_at = utc_now()
        job.error_message = ""
        return await self.persist_prepared(job, prepared)

    async def read_source_object(
        self,
        job: IngestionJob,
        *,
        principal: Principal,
    ) -> bytes:
        if not can_access_source_object(job, principal):
            raise PermissionError("Principal cannot access this source object")
        if not job.object_key:
            raise FileNotFoundError("Source object is unavailable")
        content = await (await self._get_storage()).get_object(
            job.object_key,
            version=job.object_version,
        )
        if hashlib.sha256(content).hexdigest() != job.checksum:
            raise KnowledgeIngestionError("Stored source checksum mismatch")
        return content

    async def retrieve_with_status(
        self,
        query: str,
        *,
        principal: Principal,
        top_k: int = 5,
        as_of: date | None = None,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        intent = extract_query_intent(query, as_of=as_of)
        if not intent.normalized_query:
            return _empty_retrieval(intent.normalized_query)
        retrieval_query = intent.retrieval_query
        reference_date = as_of or date.today()
        pool_size = max(top_k, settings.RAG_RETRIEVAL_POOL_SIZE)
        metadata = {
            "tickers": intent.tickers,
            "fund_codes": intent.fund_codes,
            "document_types": intent.source_types,
            "publish_date_from": intent.publish_date_from,
            "publish_date_to": intent.publish_date_to,
        }
        embedder = await self._get_embedder()
        query_vectors = np.asarray(
            await asyncio.to_thread(embedder.encode, [retrieval_query]), dtype=np.float32
        )
        expected_shape = (1, settings.RAG_EMBEDDING_DIMENSION)
        if query_vectors.shape != expected_shape:
            raise KnowledgeIngestionError(
                "Query embedding dimension mismatch: "
                f"expected {expected_shape}, got {tuple(query_vectors.shape)}"
            )
        query_vector = query_vectors[0].tolist()
        fts = await self.repository.full_text_search(
            retrieval_query,
            groups=principal.permission_groups,
            as_of=reference_date,
            limit=pool_size,
            **metadata,
        )
        dense = await self.repository.vector_search(
            query_vector,
            model_name=embedding_model_name(embedder),
            model_version=embedding_model_version(embedder),
            groups=principal.permission_groups,
            as_of=reference_date,
            limit=pool_size,
            **metadata,
        )
        dense = [
            item
            for item in dense
            if float(item.get("score", 0.0)) >= settings.RAG_VECTOR_SCORE_THRESHOLD
        ]
        ranked = reciprocal_rank_fusion([fts, dense], rrf_k=settings.RAG_RRF_K)
        if self.reranker is not None and settings.RAG_RERANKER_ENABLED:
            ranked = await asyncio.to_thread(self.reranker.rerank, retrieval_query, ranked)
        threshold = settings.RAG_SCORE_THRESHOLD if score_threshold is None else score_threshold
        citations = [
            _citation(item)
            for item in deduplicate_candidates(ranked)
            if float(item.get("score", 0.0)) >= float(threshold)
        ][:max(1, top_k)]
        return {
            "normalized_query": intent.normalized_query,
            "intent": {
                "tickers": intent.tickers,
                "fund_codes": intent.fund_codes,
                "source_types": intent.source_types,
                "publish_date_from": intent.publish_date_from.isoformat() if intent.publish_date_from else None,
                "publish_date_to": intent.publish_date_to.isoformat() if intent.publish_date_to else None,
            },
            "citations": citations,
            "evidence_insufficient": not citations,
            "score_threshold": threshold,
            "retrieval_backend": "postgresql_fts+pgvector_rrf",
        }

    async def _get_embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = (await get_resources()).embedder
        if self._embedder is None:
            resources = await get_resources()
            raise KnowledgeIngestionError(
                f"Embedding provider unavailable: {resources.embedding_error or 'unknown'}"
            )
        return self._embedder

    async def _get_storage(self) -> ObjectStorage:
        if self._storage is None:
            self._storage = (await get_resources()).object_storage
        return self._storage


def _citation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "score": round(float(item.get("score", 0.0)), 6),
        "quote": item["text"],
        "permission_level": item["confidentiality"],
        "id": item["chunk_id"],
        "source": item["source_filename"],
        "section_title": item["section"],
        "citation_key": f"{item['document_id']}:{item['chunk_id']}",
    }


def _empty_retrieval(normalized_query: str) -> dict[str, Any]:
    return {
        "normalized_query": normalized_query,
        "intent": {},
        "citations": [],
        "evidence_insufficient": True,
        "retrieval_backend": "postgresql_fts+pgvector_rrf",
    }


def _retention_until(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=max(0, days))


def can_access_source_object(job: IngestionJob, principal: Principal) -> bool:
    if not principal.authenticated:
        return False
    if principal.is_platform_admin or principal.has_role("knowledge_admin"):
        return True
    if principal.user_id == job.object_owner:
        return True
    allowed = {str(item).strip().lower() for item in job.object_permission_groups}
    return bool(allowed & principal.permission_groups)
