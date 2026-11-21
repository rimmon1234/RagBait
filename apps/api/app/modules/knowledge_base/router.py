"""FastAPI router for Knowledge Base subjects, topics, and document lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import require_approved_user
from app.modules.knowledge_base.schemas import (
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    DocumentDetail,
    DocumentListResponse,
    DocumentPagesResponse,
    DownloadUrlResponse,
    SubjectRead,
    TopicRead,
    UploadIntentRequest,
    UploadIntentResponse,
)
from app.modules.knowledge_base.service import KBService
from app.modules.users.schemas import UserProfile

kb_router = APIRouter(prefix="/kb", tags=["knowledge-base"])


@kb_router.get(
    "/subjects",
    response_model=list[SubjectRead],
    summary="List active subjects with topic and document counts",
)
async def list_subjects(
    current_user: UserProfile = Depends(require_approved_user),
) -> list[SubjectRead]:
    """Retrieve all active academic subjects with aggregated counts."""
    return await KBService.get_subjects()


@kb_router.get(
    "/subjects/{subject_id}/topics",
    response_model=list[TopicRead],
    summary="List active topics for a subject",
)
async def list_topics(
    subject_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> list[TopicRead]:
    """Retrieve all active topics belonging to a subject."""
    return await KBService.get_topics(subject_id)


@kb_router.get(
    "/topics/{topic_id}/documents",
    response_model=DocumentListResponse,
    summary="List uploaded documents in a topic",
)
async def list_topic_documents(
    topic_id: UUID,
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Items offset"),
    current_user: UserProfile = Depends(require_approved_user),
) -> DocumentListResponse:
    """Retrieve paginated active uploaded documents within a topic."""
    return await KBService.get_topic_documents(topic_id, limit=limit, offset=offset)


@kb_router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="Get document details and extraction status",
)
async def get_document(
    document_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> DocumentDetail:
    """Retrieve document details and public safe extraction status."""
    return await KBService.get_document(document_id, current_user)


@kb_router.get(
    "/documents/{document_id}/pages",
    response_model=DocumentPagesResponse,
    summary="Get paginated extracted pages for a document",
)
async def get_document_pages(
    document_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Pages per page"),
    offset: int = Query(0, ge=0, description="Pages offset"),
    current_user: UserProfile = Depends(require_approved_user),
) -> DocumentPagesResponse:
    """Retrieve paginated extracted text pages with character and word statistics."""
    return await KBService.get_document_pages(document_id, current_user, limit=limit, offset=offset)


@kb_router.post(
    "/documents/upload-intent",
    response_model=UploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create signed upload reservation for document",
)
async def create_upload_intent(
    request: UploadIntentRequest,
    current_user: UserProfile = Depends(require_approved_user),
) -> UploadIntentResponse:
    """Validate upload parameters and return a time-limited signed upload URL and capability."""
    return await KBService.create_upload_intent(current_user, request)


@kb_router.post(
    "/documents/{document_id}/confirm-upload",
    response_model=ConfirmUploadResponse,
    summary="Confirm and verify uploaded document in storage",
)
async def confirm_upload(
    document_id: UUID,
    request: ConfirmUploadRequest | None = None,
    current_user: UserProfile = Depends(require_approved_user),
) -> ConfirmUploadResponse:
    """Independently verify uploaded file in private storage, validate format, and queue extraction."""
    payload = request or ConfirmUploadRequest()
    return await KBService.confirm_upload(current_user, document_id, payload)


@kb_router.get(
    "/documents/{document_id}/download-url",
    response_model=DownloadUrlResponse,
    summary="Get signed download URL for uploaded document",
)
async def get_download_url(
    document_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> DownloadUrlResponse:
    """Generate a short-lived (5-minute) signed download URL for authorized users."""
    return await KBService.get_download_url(document_id, current_user)


@kb_router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete document and remove storage object",
)
async def delete_document(
    document_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> None:
    """Safely archive document and remove underlying file from Supabase Storage."""
    await KBService.delete_document(document_id, current_user)
