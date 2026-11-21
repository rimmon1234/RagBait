"""Pydantic schemas for the Knowledge Base and Document Lifecycle."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubjectRead(BaseModel):
    """Subject taxonomy model with child counts."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None = None
    icon_name: str
    display_order: int
    is_active: bool
    topic_count: int = 0
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class TopicRead(BaseModel):
    """Topic taxonomy model with document count."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    name: str
    slug: str
    description: str | None = None
    display_order: int
    is_active: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class DocumentListItem(BaseModel):
    """Document summary item for topic listings."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    topic_id: UUID
    uploader_id: UUID | None = None
    uploader_display_name: str | None = None
    title: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    upload_status: str
    extraction_status: str
    total_pages: int
    processed_pages: int
    pages_with_text: int
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentListItem):
    """Complete document details for status and provenance inspection.

    Crucially excludes worker_id, lease_token, and raw internal stack traces.
    """

    sha256_checksum: str | None = None
    error_code: str | None = None
    upload_expires_at: datetime | None = None


class DocumentListResponse(BaseModel):
    """Paginated document listing response."""

    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int


class DocumentPageItem(BaseModel):
    """Extracted text page record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    page_number: int
    raw_text: str
    character_count: int
    word_count: int
    has_text: bool
    created_at: datetime


class DocumentPagesResponse(BaseModel):
    """Paginated extracted pages response."""

    items: list[DocumentPageItem]
    total: int
    limit: int
    offset: int


class UploadIntentRequest(BaseModel):
    """Request to initiate a document upload reservation."""

    subject_id: UUID
    topic_id: UUID
    title: str = Field(..., min_length=1, max_length=255, description="Document title")
    original_filename: str = Field(
        ..., min_length=1, max_length=255, description="Original filename with extension"
    )
    mime_type: str = Field(..., description="MIME type: application/pdf or text/plain")
    file_size_bytes: int = Field(
        ..., gt=0, le=52_428_800, description="File size in bytes (max 50MB)"
    )
    sha256_checksum: str | None = Field(
        default=None, description="Optional client-computed SHA-256 hex checksum"
    )


class UploadIntentResponse(BaseModel):
    """Secure signed upload reservation response."""

    document_id: UUID
    storage_path: str
    signed_upload_url: str
    token: str
    upload_expires_at: datetime
    max_file_size_bytes: int
    supported_methods: list[str] = ["PUT", "TUS"]


class ConfirmUploadRequest(BaseModel):
    """Request to confirm and verify an uploaded document."""

    sha256_checksum: str | None = Field(
        default=None, description="Optional client SHA-256 hex checksum"
    )
    file_size_bytes: int | None = Field(
        default=None, gt=0, description="Optional client reported file size in bytes"
    )


class ConfirmUploadResponse(BaseModel):
    """Confirmation outcome and queued status."""

    document: DocumentDetail
    message: str = "Document upload verified and queued for extraction."


class DownloadUrlResponse(BaseModel):
    """Time-limited signed download capability."""

    download_url: str
    expires_in: int = 300
    original_filename: str
    mime_type: str
