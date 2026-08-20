"""Enterprise-lite knowledge base data models."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


Confidentiality = Literal["public", "internal", "confidential", "restricted"]
IngestionStatus = Literal["pending", "processing", "completed", "duplicate", "failed"]


class DocumentMetadata(BaseModel):
    document_id: str
    title: str
    source_type: str = "uploaded"
    department: str = "Research"
    tickers: list[str] = Field(default_factory=list)
    fund_codes: list[str] = Field(default_factory=list)
    publish_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    version: int = 1
    confidentiality: Confidentiality = "public"
    permission_groups: list[str] = Field(default_factory=lambda: ["public"])
    author: str = ""
    checksum: str = ""
    ingestion_status: IngestionStatus = "pending"
    status: Literal["draft", "published", "inactive"] = "draft"
    published_version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_access_and_validity(self) -> "DocumentMetadata":
        groups = {group.strip().lower() for group in self.permission_groups if group.strip()}
        if self.confidentiality != "public" and (not groups or "public" in groups):
            raise ValueError("Non-public documents require explicit non-public permission_groups")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("effective_from must be on or before effective_to")
        return self


class DocumentVersion(BaseModel):
    version_id: str
    document_id: str
    version: int
    checksum: str
    source_filename: str
    parser: str
    content_length: int
    chunk_count: int = 0
    status: Literal["processing", "completed", "failed"] = "processing"
    error_message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    version_id: str
    version: int
    chunk_index: int
    title: str
    section: str = ""
    page_number: int | None = None
    text: str
    char_count: int


class IngestionJob(BaseModel):
    job_id: str
    document_id: str | None = None
    version_id: str | None = None
    status: IngestionStatus = "pending"
    filename: str
    checksum: str = ""
    chunks_created: int = 0
    error_message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PermissionContext(BaseModel):
    user_id: str = "anonymous"
    permission_groups: list[str] = Field(default_factory=lambda: ["public"])

    @property
    def is_admin(self) -> bool:
        return "knowledge_admin" in {group.lower() for group in self.permission_groups}
