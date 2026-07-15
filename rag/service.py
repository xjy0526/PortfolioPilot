"""Knowledge ingestion, publication and permission-aware retrieval service."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from config import settings
from rag.hybrid_retriever import BM25Retriever, DenseRetriever, HybridRetriever, Reranker
from rag.models import DocumentMetadata, PermissionContext
from rag.parsers import SUPPORTED_SUFFIXES, parse_document, structured_chunks
from rag.query import extract_query_intent
from rag.repository import KnowledgeRepository


class IngestionError(ValueError):
    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


class KnowledgeBaseService:
    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        embedder: Any | None = None,
        reranker: Reranker | None = None,
        reranker_enabled: bool = True,
    ):
        self.repository = repository or KnowledgeRepository()
        if embedder is None:
            from rag.retriever import _build_embedder

            embedder = _build_embedder()
        self.embedder = embedder
        self.hybrid_retriever = HybridRetriever(
            BM25Retriever(),
            DenseRetriever(embedder),
            rrf_k=int(getattr(settings, "RAG_RRF_K", 60)),
            reranker=reranker if reranker_enabled else None,
        )

    def ingest(
        self,
        *,
        content: bytes,
        filename: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        supplied_document_id = str(metadata.get("document_id") or "").strip() or None
        checksum = hashlib.sha256(content).hexdigest()
        duplicate = self.repository.find_checksum(
            checksum,
            document_id=supplied_document_id,
        )
        document_id = supplied_document_id or (duplicate or {}).get("document_id") or str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        self.repository.create_job(job_id, filename, document_id, checksum)

        if duplicate:
            self.repository.update_job(
                job_id,
                status="duplicate",
                document_id=duplicate["document_id"],
                version_id=duplicate["version_id"],
            )
            return {
                "status": "duplicate",
                "job_id": job_id,
                "document_id": duplicate["document_id"],
                "version_id": duplicate["version_id"],
                "version": duplicate["version"],
                "checksum": checksum,
                "chunks_created": 0,
            }

        fallback_title = str(metadata.get("title") or Path(filename).stem).strip()
        try:
            parsed_title, blocks, parser = parse_document(content, filename, fallback_title)
            raw_chunks = structured_chunks(
                blocks,
                chunk_size=max(200, int(getattr(settings, "RAG_CHUNK_SIZE", 900))),
            )
            if not raw_chunks:
                raise ValueError("Document contains no indexable text")

            metadata["document_id"] = document_id
            metadata["title"] = str(metadata.get("title") or parsed_title or fallback_title)
            metadata["checksum"] = checksum
            metadata["ingestion_status"] = "completed"
            validated = DocumentMetadata(**metadata)
            version_id = str(uuid.uuid4())
            chunks = []
            for item in raw_chunks:
                item = dict(item)
                item["chunk_id"] = hashlib.sha256(
                    f"{version_id}:{item['chunk_index']}:{item['text']}".encode("utf-8")
                ).hexdigest()[:32]
                chunks.append(item)

            version, version_id = self.repository.store_version(
                metadata=validated.model_dump(mode="json"),
                version_id=version_id,
                filename=filename,
                parser=parser,
                content_text="\n\n".join(block.text for block in blocks),
                checksum=checksum,
                chunks=chunks,
            )
            self.repository.update_job(
                job_id,
                status="completed",
                document_id=document_id,
                version_id=version_id,
                chunks_created=len(chunks),
            )
            return {
                "status": "completed",
                "job_id": job_id,
                "document_id": document_id,
                "version_id": version_id,
                "version": version,
                "checksum": checksum,
                "chunks_created": len(chunks),
            }
        except Exception as exc:
            self.repository.update_job(
                job_id,
                status="failed",
                document_id=document_id,
                error_message=str(exc),
            )
            raise IngestionError(str(exc), job_id) from exc

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        permission_context: PermissionContext | None = None,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        return self.retrieve_with_status(
            query,
            top_k=top_k,
            permission_context=permission_context,
            as_of=as_of,
        )["citations"]

    def retrieve_with_status(
        self,
        query: str,
        *,
        top_k: int = 5,
        permission_context: PermissionContext | None = None,
        as_of: date | None = None,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        if not query.strip():
            return {
                "normalized_query": "",
                "intent": {},
                "citations": [],
                "evidence_insufficient": True,
            }
        context = permission_context or PermissionContext()
        intent = extract_query_intent(query, as_of=as_of)
        # Metadata, ACL and temporal filters execute in SQLite before sensitive
        # chunk text reaches BM25, dense embedding, fusion or reranking.
        eligible = self.repository.fetch_retrievable_chunks(
            context,
            as_of=as_of,
            **intent.metadata_filters(),
        )
        if not eligible:
            return {
                "normalized_query": intent.normalized_query,
                "intent": _intent_payload(intent),
                "citations": [],
                "evidence_insufficient": True,
            }

        threshold = (
            float(getattr(settings, "RAG_SCORE_THRESHOLD", 0.15))
            if score_threshold is None
            else float(score_threshold)
        )
        ranked = self.hybrid_retriever.retrieve(
            intent.normalized_query,
            eligible,
            top_k=max(1, int(top_k)),
            pool_size=int(getattr(settings, "RAG_RETRIEVAL_POOL_SIZE", 20)),
            score_threshold=threshold,
        )
        citations: list[dict[str, Any]] = []
        for item in ranked:
            citation = {
                "document_id": item["document_id"],
                "version": item["version"],
                "chunk_id": item["chunk_id"],
                "source_type": item["source_type"],
                "title": item["title"],
                "publish_date": item["publish_date"],
                "page_number": item["page_number"],
                "section_title": item["section"],
                "score": round(float(item["score"]), 6),
                "quote": item["text"],
                "permission_level": item["confidentiality"],
                # Compatibility aliases retained for existing prompt and UI clients.
                "id": item["chunk_id"],
                "version_id": item["version_id"],
                "source": item["source_filename"],
                "section": item["section"],
                "text": item["text"],
                "citation_key": f"{item['document_id']}:{item['chunk_id']}",
            }
            citations.append(citation)
        return {
            "normalized_query": intent.normalized_query,
            "intent": _intent_payload(intent),
            "citations": citations,
            "evidence_insufficient": not citations,
            "score_threshold": threshold,
        }

    def bootstrap_public_directory(self, root: Path) -> int:
        """Idempotently ingest bundled public/simulated sample documents."""
        if not root.exists() or not root.is_dir():
            return 0
        ingested = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            document_id = f"sample-{hashlib.sha1(str(path.resolve()).encode('utf-8')).hexdigest()[:20]}"
            try:
                result = self.ingest(
                    content=path.read_bytes(),
                    filename=path.name,
                    metadata={
                        "document_id": document_id,
                        "title": path.stem.replace("_", " ").title(),
                        "source_type": "public_sample",
                        "department": "Research",
                        "confidentiality": "public",
                        "permission_groups": ["public"],
                        "author": "PortfolioPilot sample",
                    },
                )
                self.repository.publish(result["document_id"])
                if result["status"] == "completed":
                    ingested += 1
            except IngestionError:
                continue
        return ingested


def _intent_payload(intent: Any) -> dict[str, Any]:
    return {
        "tickers": intent.tickers,
        "fund_codes": intent.fund_codes,
        "source_types": intent.source_types,
        "publish_date_from": intent.publish_date_from.isoformat() if intent.publish_date_from else None,
        "publish_date_to": intent.publish_date_to.isoformat() if intent.publish_date_to else None,
    }
