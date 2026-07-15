"""Enterprise-lite knowledge base APIs."""
from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag.models import PermissionContext
from rag.service import IngestionError, KnowledgeBaseService

router = APIRouter()


class KnowledgeDocumentUpload(BaseModel):
    filename: str
    content: str | None = None
    content_base64: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_knowledge_service() -> KnowledgeBaseService:
    return KnowledgeBaseService()


def _permission_context(user_id: str | None, groups: str | None) -> PermissionContext:
    parsed = [group.strip() for group in (groups or "public").split(",") if group.strip()]
    return PermissionContext(user_id=user_id or "anonymous", permission_groups=parsed or ["public"])


@router.post("/api/knowledge/documents")
async def create_knowledge_document(payload: KnowledgeDocumentUpload):
    try:
        if payload.content_base64:
            content = base64.b64decode(payload.content_base64, validate=True)
        elif payload.content is not None:
            if payload.filename.lower().endswith(".pdf"):
                return JSONResponse({"error": "PDF content must use content_base64"}, status_code=400)
            content = payload.content.encode("utf-8")
        else:
            return JSONResponse({"error": "content or content_base64 is required"}, status_code=400)
    except (binascii.Error, ValueError) as exc:
        return JSONResponse({"error": f"Invalid base64 content: {exc}"}, status_code=400)

    try:
        result = get_knowledge_service().ingest(
            content=content,
            filename=payload.filename,
            metadata=payload.metadata,
        )
        return JSONResponse(result, status_code=200 if result["status"] == "duplicate" else 201)
    except IngestionError as exc:
        return JSONResponse({"error": str(exc), "job_id": exc.job_id}, status_code=422)


@router.get("/api/knowledge/documents")
async def list_knowledge_documents(
    x_user_id: str | None = Header(default=None),
    x_permission_groups: str | None = Header(default=None),
):
    service = get_knowledge_service()
    context = _permission_context(x_user_id, x_permission_groups)
    documents = service.repository.list_documents(context)
    return {"count": len(documents), "documents": [doc.model_dump(mode="json") for doc in documents]}


@router.get("/api/knowledge/documents/{document_id}")
async def get_knowledge_document(
    document_id: str,
    x_user_id: str | None = Header(default=None),
    x_permission_groups: str | None = Header(default=None),
):
    service = get_knowledge_service()
    context = _permission_context(x_user_id, x_permission_groups)
    document = service.repository.get_document(document_id, context)
    if not document:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {
        "document": document.model_dump(mode="json"),
        "versions": service.repository.list_versions(document_id),
    }


@router.post("/api/knowledge/documents/{document_id}/publish")
async def publish_knowledge_document(document_id: str):
    document = get_knowledge_service().repository.publish(document_id)
    if not document:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "published", "document": document.model_dump(mode="json")}


@router.post("/api/knowledge/documents/{document_id}/deactivate")
async def deactivate_knowledge_document(document_id: str):
    document = get_knowledge_service().repository.deactivate(document_id)
    if not document:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "inactive", "document": document.model_dump(mode="json")}


@router.get("/api/knowledge/ingestion-jobs/{job_id}")
async def get_ingestion_job(job_id: str):
    job = get_knowledge_service().repository.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Ingestion job not found"}, status_code=404)
    return job.model_dump(mode="json")
