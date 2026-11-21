"""FastAPI administrative router for Knowledge Base management, moderation, and monitoring."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import (
    rate_limit_admin_mutate,
    rate_limit_admin_read,
    require_admin,
)
from app.modules.knowledge_base.admin_schemas import (
    AdminDocumentDetail,
    AdminJobDetail,
    AdminOverviewMetrics,
    AdminSubjectItem,
    AdminTopicItem,
    DocumentArchiveRequest,
    DocumentDeleteRequest,
    DocumentRestoreRequest,
    JobRetryRequest,
    JobRetryResponse,
    PaginatedAdminAuditResponse,
    PaginatedAdminDocumentsResponse,
    PaginatedAdminJobsResponse,
    SubjectCreateRequest,
    SubjectUpdateRequest,
    TopicCreateRequest,
    TopicReorderRequest,
    TopicUpdateRequest,
)
from app.modules.knowledge_base.admin_service import KBAdminService
from app.modules.users.schemas import UserProfile

kb_admin_router = APIRouter(prefix="/admin", tags=["Knowledge Base Administration"])


# ------------------------------------------------------------------------------
# Overview & Metrics
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/overview",
    response_model=AdminOverviewMetrics,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="Get Knowledge Base administrative dashboard metrics",
)
async def get_overview(
    _: UserProfile = Depends(require_admin),
) -> AdminOverviewMetrics:
    """Retrieve aggregate counts, recent document uploads, and recent admin activity."""
    return await KBAdminService.get_overview()


# ------------------------------------------------------------------------------
# Subject Management
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/subjects",
    response_model=list[AdminSubjectItem],
    dependencies=[Depends(rate_limit_admin_read)],
    summary="List all subjects including inactive for administration",
)
async def list_subjects(
    include_inactive: bool = Query(True, description="Include archived/inactive subjects"),
    search: str | None = Query(None, description="Filter by name or slug"),
    _: UserProfile = Depends(require_admin),
) -> list[AdminSubjectItem]:
    """Retrieve subjects with topic and document count statistics."""
    return await KBAdminService.list_subjects(include_inactive=include_inactive, search=search)


@kb_admin_router.post(
    "/subjects",
    response_model=AdminSubjectItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Create a new academic subject",
)
async def create_subject(
    body: SubjectCreateRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminSubjectItem:
    """Create a new subject in the academic catalogue."""
    return await KBAdminService.create_subject(current_user, body)


@kb_admin_router.patch(
    "/subjects/{subject_id}",
    response_model=AdminSubjectItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Update subject metadata",
)
async def update_subject(
    subject_id: UUID,
    body: SubjectUpdateRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminSubjectItem:
    """Update an existing subject's name, description, icon, display order, or slug."""
    return await KBAdminService.update_subject(current_user, subject_id, body)


@kb_admin_router.post(
    "/subjects/{subject_id}/archive",
    response_model=AdminSubjectItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Archive a subject",
)
async def archive_subject(
    subject_id: UUID,
    current_user: UserProfile = Depends(require_admin),
) -> AdminSubjectItem:
    """Mark a subject inactive without deleting its topics or documents."""
    return await KBAdminService.archive_subject(current_user, subject_id)


@kb_admin_router.post(
    "/subjects/{subject_id}/restore",
    response_model=AdminSubjectItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Restore an archived subject",
)
async def restore_subject(
    subject_id: UUID,
    current_user: UserProfile = Depends(require_admin),
) -> AdminSubjectItem:
    """Reactivate an archived subject."""
    return await KBAdminService.restore_subject(current_user, subject_id)


# ------------------------------------------------------------------------------
# Topic Management
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/subjects/{subject_id}/topics",
    response_model=list[AdminTopicItem],
    dependencies=[Depends(rate_limit_admin_read)],
    summary="List all topics for a subject including inactive",
)
async def list_topics(
    subject_id: UUID,
    include_inactive: bool = Query(True, description="Include archived/inactive topics"),
    _: UserProfile = Depends(require_admin),
) -> list[AdminTopicItem]:
    """Retrieve topics belonging to a subject with document counts."""
    return await KBAdminService.list_topics(subject_id, include_inactive=include_inactive)


@kb_admin_router.post(
    "/subjects/{subject_id}/topics",
    response_model=AdminTopicItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Create a new topic in a subject",
)
async def create_topic(
    subject_id: UUID,
    body: TopicCreateRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminTopicItem:
    """Create a topic under an active subject."""
    return await KBAdminService.create_topic(current_user, subject_id, body)


@kb_admin_router.patch(
    "/topics/{topic_id}",
    response_model=AdminTopicItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Update topic metadata",
)
async def update_topic(
    topic_id: UUID,
    body: TopicUpdateRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminTopicItem:
    """Update topic name, slug, description, or display order."""
    return await KBAdminService.update_topic(current_user, topic_id, body)


@kb_admin_router.patch(
    "/topics/{topic_id}/order",
    response_model=AdminTopicItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Reorder a topic",
)
async def reorder_topic(
    topic_id: UUID,
    body: TopicReorderRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminTopicItem:
    """Update a topic's display order."""
    return await KBAdminService.reorder_topic(current_user, topic_id, body)


@kb_admin_router.post(
    "/topics/{topic_id}/archive",
    response_model=AdminTopicItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Archive a topic",
)
async def archive_topic(
    topic_id: UUID,
    current_user: UserProfile = Depends(require_admin),
) -> AdminTopicItem:
    """Mark a topic inactive without deleting documents."""
    return await KBAdminService.archive_topic(current_user, topic_id)


@kb_admin_router.post(
    "/topics/{topic_id}/restore",
    response_model=AdminTopicItem,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Restore an archived topic",
)
async def restore_topic(
    topic_id: UUID,
    current_user: UserProfile = Depends(require_admin),
) -> AdminTopicItem:
    """Reactivate an archived topic under an active parent subject."""
    return await KBAdminService.restore_topic(current_user, topic_id)


# ------------------------------------------------------------------------------
# Document Moderation
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/documents",
    response_model=PaginatedAdminDocumentsResponse,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="Global document moderation list",
)
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    subject_id: UUID | None = Query(None),
    topic_id: UUID | None = Query(None),
    uploader_id: UUID | None = Query(None),
    upload_status: str | None = Query(None),
    extraction_status: str | None = Query(None),
    is_archived: bool | None = Query(None),
    search: str | None = Query(None),
    _: UserProfile = Depends(require_admin),
) -> PaginatedAdminDocumentsResponse:
    """List documents across subjects with comprehensive filters."""
    return await KBAdminService.list_documents(
        limit=limit,
        offset=offset,
        subject_id=subject_id,
        topic_id=topic_id,
        uploader_id=uploader_id,
        upload_status=upload_status,
        extraction_status=extraction_status,
        is_archived=is_archived,
        search=search,
    )


@kb_admin_router.get(
    "/documents/{document_id}",
    response_model=AdminDocumentDetail,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="Get document moderation details",
)
async def get_document(
    document_id: UUID,
    _: UserProfile = Depends(require_admin),
) -> AdminDocumentDetail:
    """Fetch complete document detail including storage path and processing info."""
    return await KBAdminService.get_document_detail(document_id)


@kb_admin_router.post(
    "/documents/{document_id}/archive",
    response_model=AdminDocumentDetail,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Archive a document",
)
async def archive_document(
    document_id: UUID,
    body: DocumentArchiveRequest,
    current_user: UserProfile = Depends(require_admin),
) -> AdminDocumentDetail:
    """Archive a document with mandatory reason, blocking student access and fencing workers."""
    return await KBAdminService.archive_document(current_user, document_id, body)


@kb_admin_router.post(
    "/documents/{document_id}/restore",
    response_model=AdminDocumentDetail,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Restore an archived document",
)
async def restore_document(
    document_id: UUID,
    body: DocumentRestoreRequest | None = None,
    current_user: UserProfile = Depends(require_admin),
) -> AdminDocumentDetail:
    """Restore an archived document under an active taxonomy."""
    req = body or DocumentRestoreRequest()
    return await KBAdminService.restore_document(current_user, document_id, req)


@kb_admin_router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Permanently delete a document",
)
async def delete_document(
    document_id: UUID,
    body: DocumentDeleteRequest,
    current_user: UserProfile = Depends(require_admin),
) -> None:
    """Permanently remove a document and delete its underlying storage object."""
    await KBAdminService.delete_document(current_user, document_id, body)


# ------------------------------------------------------------------------------
# Extraction Job Monitoring & Retry
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/jobs",
    response_model=PaginatedAdminJobsResponse,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="List extraction jobs for operational monitoring",
)
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    subject_id: UUID | None = Query(None),
    topic_id: UUID | None = Query(None),
    search: str | None = Query(None),
    _: UserProfile = Depends(require_admin),
) -> PaginatedAdminJobsResponse:
    """List extraction queue jobs with retry eligibility indicators and sanitized errors."""
    return await KBAdminService.list_jobs(
        limit=limit,
        offset=offset,
        status=status,
        subject_id=subject_id,
        topic_id=topic_id,
        search=search,
    )


@kb_admin_router.get(
    "/jobs/{job_id}",
    response_model=AdminJobDetail,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="Get sanitized extraction job details",
)
async def get_job(
    job_id: UUID,
    _: UserProfile = Depends(require_admin),
) -> AdminJobDetail:
    """Fetch sanitized extraction job details (excludes lease tokens and worker internals)."""
    return await KBAdminService.get_job_detail(job_id)


@kb_admin_router.post(
    "/jobs/{job_id}/retry",
    response_model=JobRetryResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
    summary="Retry an eligible failed extraction job",
)
async def retry_job(
    job_id: UUID,
    body: JobRetryRequest | None = None,
    current_user: UserProfile = Depends(require_admin),
) -> JobRetryResponse:
    """Requeue an eligible failed extraction job safely."""
    req = body or JobRetryRequest()
    return await KBAdminService.retry_job(current_user, job_id, req)


# ------------------------------------------------------------------------------
# Audit Activity
# ------------------------------------------------------------------------------


@kb_admin_router.get(
    "/audit",
    response_model=PaginatedAdminAuditResponse,
    dependencies=[Depends(rate_limit_admin_read)],
    summary="List Knowledge Base administrative audit history",
)
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    actor_id: UUID | None = Query(None),
    _: UserProfile = Depends(require_admin),
) -> PaginatedAdminAuditResponse:
    """Retrieve immutable audit records for Knowledge Base mutations."""
    return await KBAdminService.list_audit_logs(
        limit=limit,
        offset=offset,
        action=action,
        target_type=target_type,
        actor_id=actor_id,
    )
