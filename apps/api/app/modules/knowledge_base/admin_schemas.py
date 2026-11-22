"""Pydantic schemas for Knowledge Base administrative management and moderation."""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_ICON_NAMES = {
    "BookOpen",
    "Code",
    "Cpu",
    "Database",
    "Globe",
    "Layers",
    "Server",
    "Terminal",
    "Brain",
    "Compass",
    "FileText",
    "Bookmark",
}

SLUG_REGEX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ------------------------------------------------------------------------------
# Taxonomy Schemas
# ------------------------------------------------------------------------------


class SubjectCreateRequest(BaseModel):
    """Admin payload to create a new academic subject."""

    name: str = Field(..., min_length=1, max_length=100, description="Subject name")
    slug: str = Field(..., min_length=1, max_length=100, description="URL-friendly slug")
    description: str | None = Field(default=None, max_length=1000)
    icon_name: str = Field(default="BookOpen", description="Approved icon name")
    display_order: int = Field(default=0, ge=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        clean = v.strip().lower()
        if not SLUG_REGEX.match(clean):
            raise ValueError(
                "Slug must consist of lowercase alphanumeric characters separated by single hyphens."
            )
        return clean

    @field_validator("icon_name")
    @classmethod
    def validate_icon(cls, v: str) -> str:
        if v not in ALLOWED_ICON_NAMES:
            raise ValueError(
                f"Invalid icon_name '{v}'. Allowed icons: {', '.join(sorted(ALLOWED_ICON_NAMES))}"
            )
        return v


class SubjectUpdateRequest(BaseModel):
    """Admin payload to update an existing academic subject."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    icon_name: str | None = Field(default=None)
    display_order: int | None = Field(default=None, ge=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().lower()
        if not SLUG_REGEX.match(clean):
            raise ValueError(
                "Slug must consist of lowercase alphanumeric characters separated by single hyphens."
            )
        return clean

    @field_validator("icon_name")
    @classmethod
    def validate_icon(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ALLOWED_ICON_NAMES:
            raise ValueError(
                f"Invalid icon_name '{v}'. Allowed icons: {', '.join(sorted(ALLOWED_ICON_NAMES))}"
            )
        return v


class AdminSubjectItem(BaseModel):
    """Full subject record for administrators."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None = None
    icon_name: str = "BookOpen"
    display_order: int
    is_active: bool
    topic_count: int = 0
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class TopicCreateRequest(BaseModel):
    """Admin payload to create a topic within a subject."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    display_order: int = Field(default=0, ge=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        clean = v.strip().lower()
        if not SLUG_REGEX.match(clean):
            raise ValueError(
                "Slug must consist of lowercase alphanumeric characters separated by single hyphens."
            )
        return clean


class TopicUpdateRequest(BaseModel):
    """Admin payload to update an existing topic."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    display_order: int | None = Field(default=None, ge=0)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().lower()
        if not SLUG_REGEX.match(clean):
            raise ValueError(
                "Slug must consist of lowercase alphanumeric characters separated by single hyphens."
            )
        return clean


class TopicReorderRequest(BaseModel):
    """Admin payload to adjust topic display order."""

    display_order: int = Field(..., ge=0, description="Target non-negative display order")


class AdminTopicItem(BaseModel):
    """Full topic record for administrators."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    subject_name: str | None = None
    subject_slug: str | None = None
    name: str
    slug: str
    description: str | None = None
    display_order: int
    is_active: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
# Document Moderation Schemas
# ------------------------------------------------------------------------------


class DocumentArchiveRequest(BaseModel):
    """Admin payload to archive a document."""

    reason: str = Field(
        ..., min_length=3, max_length=500, description="Mandatory moderation reason"
    )


class DocumentRestoreRequest(BaseModel):
    """Admin payload to restore an archived document."""

    reason: str | None = Field(default=None, max_length=500)


class DocumentDeleteRequest(BaseModel):
    """Admin payload to permanently delete a document."""

    reason: str = Field(
        ..., min_length=3, max_length=500, description="Mandatory permanent deletion reason"
    )


class AdminDocumentListItem(BaseModel):
    """Document summary item for moderation lists."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    subject_name: str
    topic_id: UUID
    topic_name: str
    uploader_id: UUID | None = None
    uploader_display_name: str | None = None
    uploader_email: str | None = None
    title: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    upload_status: str
    extraction_status: str
    total_pages: int
    processed_pages: int
    pages_with_text: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class AdminDocumentDetail(AdminDocumentListItem):
    """Detailed document moderation item with storage path and error info."""

    storage_path: str
    sha256_checksum: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    upload_expires_at: datetime | None = None


class PaginatedAdminDocumentsResponse(BaseModel):
    """Paginated moderation document response."""

    items: list[AdminDocumentListItem]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------------------
# Extraction Job Monitoring & Retry Schemas
# ------------------------------------------------------------------------------


class AdminJobListItem(BaseModel):
    """Sanitized extraction job item for admin monitor."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    document_title: str
    subject_id: UUID
    subject_name: str
    topic_id: UUID
    topic_name: str
    uploader_display_name: str | None = None
    status: str
    attempts: int
    max_attempts: int
    scheduled_at: datetime
    locked_until: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    retry_of_job_id: UUID | None = None
    is_retryable: bool = False
    created_at: datetime
    updated_at: datetime


class AdminJobDetail(AdminJobListItem):
    """Job detail view with sanitization guarantees (no lease_token or stack traces)."""

    file_size_bytes: int
    mime_type: str
    document_upload_status: str
    document_is_archived: bool
    retry_eligibility_reason: str | None = None


class PaginatedAdminJobsResponse(BaseModel):
    """Paginated job monitor response."""

    items: list[AdminJobListItem]
    total: int
    limit: int
    offset: int


class JobRetryRequest(BaseModel):
    """Admin payload to trigger manual retry of a failed extraction job."""

    reason: str | None = Field(default=None, max_length=500, description="Optional retry reason")


class JobRetryResponse(BaseModel):
    """Outcome of a successful job retry operation."""

    job: AdminJobListItem
    message: str = "Extraction job queued for retry."


# ------------------------------------------------------------------------------
# Audit Activity Schemas
# ------------------------------------------------------------------------------


class AdminAuditItem(BaseModel):
    """Audit log entry representation for Knowledge Base operations."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None = None
    actor_display_name: str | None = None
    actor_email: str | None = None
    action: str
    target_type: str
    target_id: UUID | None = None
    payload: dict[str, Any] = {}
    created_at: datetime


class PaginatedAdminAuditResponse(BaseModel):
    """Paginated audit log response."""

    items: list[AdminAuditItem]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------------------
# Admin Overview Dashboard
# ------------------------------------------------------------------------------


class AdminOverviewMetrics(BaseModel):
    """Aggregate dashboard metrics for the Knowledge Base admin console."""

    active_subjects_count: int
    total_subjects_count: int
    active_topics_count: int
    total_topics_count: int
    total_documents_count: int
    archived_documents_count: int
    queued_jobs_count: int
    processing_jobs_count: int
    failed_jobs_count: int
    completed_jobs_count: int
    recent_documents: list[AdminDocumentListItem] = []
    recent_actions: list[AdminAuditItem] = []
