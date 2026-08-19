"""Governed PostgreSQL research-document APIs."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from app.core.principal import require_writable
from app.db.repositories.governance import ResearchRepository
from app.services.research_knowledge import KnowledgeIngestionError, PostgresKnowledgeService
from config import settings

router = APIRouter()


@router.post("/api/knowledge/documents")
async def create_knowledge_document(
    file: UploadFile = File(...),
    metadata: str = Form(default="{}"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        require_writable()
        parsed_metadata = json.loads(metadata)
        if not isinstance(parsed_metadata, dict):
            raise ValueError("metadata must be a JSON object")
        content = await _read_limited_upload(file)
        job, replay = await PostgresKnowledgeService(session).queue_upload(
            content=content,
            filename=file.filename or "",
            metadata=parsed_metadata,
            principal=principal,
            idempotency_key=idempotency_key,
        )
        return JSONResponse(
            {
                "job_id": str(job.id),
                "status": job.status,
                "filename": job.filename,
                "checksum": job.checksum,
                "idempotent_replay": replay,
            },
            status_code=200 if replay else 202,
        )
    except (KnowledgeIngestionError, ValueError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    finally:
        await file.close()


@router.get("/api/knowledge/documents")
async def list_knowledge_documents(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    documents = await ResearchRepository(session).list_documents(
        principal.permission_groups,
        is_admin=principal.has_role("knowledge_admin") or principal.is_platform_admin,
    )
    include_drafts = principal.has_role("knowledge_admin") or principal.is_platform_admin
    payload = [_document_payload(item, include_drafts=include_drafts) for item in documents]
    return {"count": len(payload), "documents": payload}


@router.get("/api/knowledge/documents/{document_id}")
async def get_knowledge_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    repository = ResearchRepository(session)
    document = await repository.get_document(
        document_id,
        principal.permission_groups,
        is_admin=principal.has_role("knowledge_admin") or principal.is_platform_admin,
    )
    if document is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    include_drafts = principal.has_role("knowledge_admin") or principal.is_platform_admin
    versions = await repository.list_versions(document_id)
    if not include_drafts:
        versions = [item for item in versions if item.version == document.published_version]
    return {
        "document": _document_payload(document, include_drafts=include_drafts),
        "versions": [
            {
                "version_id": str(item.id),
                "version": item.version,
                "checksum": item.checksum,
                "stored_content_checksum": item.stored_content_checksum,
                "source_filename": item.source_filename,
                "parser": item.parser,
                "page_count": item.page_count,
                "chunk_count": item.chunk_count,
                "status": item.status,
                "created_at": item.created_at.isoformat(),
            }
            for item in versions
        ],
    }


@router.post("/api/knowledge/documents/{document_id}/publish")
async def publish_knowledge_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    document = await ResearchRepository(session).set_published(document_id)
    if document is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "published", "document": _document_payload(document)}


@router.post("/api/knowledge/documents/{document_id}/deactivate")
async def deactivate_knowledge_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    require_writable()
    principal.require_role("knowledge_admin", "platform_admin")
    document = await ResearchRepository(session).deactivate(document_id)
    if document is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "inactive", "document": _document_payload(document)}


@router.get("/api/knowledge/ingestion-jobs/{job_id}")
async def get_ingestion_job(
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    job = await ResearchRepository(session).get_job(job_id)
    is_admin = principal.has_role("knowledge_admin") or principal.is_platform_admin
    if job is None or (job.user_id != principal.user_id and not is_admin):
        return JSONResponse({"error": "Ingestion job not found"}, status_code=404)
    return {
        "job_id": str(job.id),
        "document_id": str(job.document_id) if job.document_id else None,
        "version_id": str(job.version_id) if job.version_id else None,
        "status": job.status,
        "filename": job.filename,
        "checksum": job.checksum,
        "content_length": job.content_length,
        "content_type": job.content_type,
        "retention_until": job.retention_until.isoformat() if job.retention_until else None,
        "code_version": job.code_version,
        "chunks_created": job.chunks_created,
        "retry_count": job.retry_count,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/api/knowledge/ingestion-jobs/{job_id}/source")
async def download_ingestion_source(
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
):
    job = await ResearchRepository(session).get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Ingestion source not found"}, status_code=404)
    try:
        content = await PostgresKnowledgeService(session).read_source_object(
            job,
            principal=principal,
        )
    except PermissionError:
        return JSONResponse({"error": "Source access denied"}, status_code=403)
    except FileNotFoundError:
        return JSONResponse({"error": "Ingestion source not found"}, status_code=404)
    filename = Path(job.filename).name
    return Response(
        content,
        media_type=job.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _document_payload(document: Any, *, include_drafts: bool = True) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "document_key": document.document_key,
        "title": document.title,
        "source_type": document.source_type,
        "department": document.department,
        "author": document.author,
        "status": document.status,
        "current_version": (
            document.current_version if include_drafts else document.published_version
        ),
        "published_version": document.published_version,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }


async def _read_limited_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while block := await file.read(1024 * 1024):
        total += len(block)
        if total > settings.RAG_MAX_UPLOAD_BYTES:
            raise KnowledgeIngestionError(
                f"File size limit exceeded: {total} > "
                f"{settings.RAG_MAX_UPLOAD_BYTES}"
            )
        chunks.append(block)
    return b"".join(chunks)
