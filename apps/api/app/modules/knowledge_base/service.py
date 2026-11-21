"""Knowledge Base business service coordinating storage, repository, and validation."""

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.database import get_admin_db_session
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.core.redis import check_rate_limit
from app.modules.knowledge_base import repository, storage
from app.modules.knowledge_base.schemas import (
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    DocumentDetail,
    DocumentListItem,
    DocumentListResponse,
    DocumentPageItem,
    DocumentPagesResponse,
    DownloadUrlResponse,
    SubjectRead,
    TopicRead,
    UploadIntentRequest,
    UploadIntentResponse,
)
from app.modules.knowledge_base.validators import (
    sanitize_title,
    validate_filename,
    validate_storage_stream,
)
from app.modules.users.schemas import UserProfile

logger = logging.getLogger("ragbait.kb.service")


class KBService:
    """Service layer managing Knowledge Base subjects, topics, and documents."""

    @staticmethod
    async def get_subjects() -> list[SubjectRead]:
        """Fetch all active subjects with child topic and document counts."""
        async with get_admin_db_session() as conn:
            subjects = await repository.get_active_subjects(conn)
        return [SubjectRead.model_validate(s) for s in subjects]

    @staticmethod
    async def get_topics(subject_id: UUID) -> list[TopicRead]:
        """Fetch active topics for a given subject."""
        async with get_admin_db_session() as conn:
            subject = await repository.get_subject_by_id(conn, subject_id)
            if not subject or not subject["is_active"]:
                raise NotFoundError(
                    f"Subject '{subject_id}' not found or inactive.",
                    code="SUBJECT_NOT_FOUND",
                )
            topics = await repository.get_active_topics_for_subject(conn, subject_id)
        return [TopicRead.model_validate(t) for t in topics]

    @staticmethod
    async def get_topic_documents(
        topic_id: UUID, limit: int = 20, offset: int = 0
    ) -> DocumentListResponse:
        """Fetch uploaded documents in a topic with pagination."""
        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)

        async with get_admin_db_session() as conn:
            topic = await repository.get_topic_by_id(conn, topic_id)
            if not topic or not topic["is_active"]:
                raise NotFoundError(
                    f"Topic '{topic_id}' not found or inactive.",
                    code="TOPIC_NOT_FOUND",
                )
            subject = await repository.get_subject_by_id(conn, topic["subject_id"])
            if not subject or not subject["is_active"]:
                raise NotFoundError(
                    f"Subject '{topic['subject_id']}' not found or inactive.",
                    code="SUBJECT_NOT_FOUND",
                )
            docs, total = await repository.list_documents_in_topic(
                conn, topic_id, limit=bounded_limit, offset=bounded_offset
            )

        items = [DocumentListItem.model_validate(d) for d in docs]
        return DocumentListResponse(
            items=items,
            total=total,
            limit=bounded_limit,
            offset=bounded_offset,
        )

    @staticmethod
    async def get_document(document_id: UUID, user: UserProfile) -> DocumentDetail:
        """Fetch document details enforcing visibility and authorization rules."""
        is_admin = user.role in ("admin", "super_admin")

        async with get_admin_db_session() as conn:
            doc = await repository.get_raw_document_by_id(conn, document_id)

        if not doc:
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        # Visibility evaluation matching hardened M3.1 RLS:
        # Admins can view any non-deleted document.
        # Approved students can view uploaded active documents.
        # Uploaders can view their own pending_upload or upload_failed documents.
        if is_admin:
            return DocumentDetail.model_validate(doc)

        if doc["is_archived"] or doc["upload_status"] in ("deleting", "deleted"):
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        # Non-admin check: parent subject and topic must both be active
        async with get_admin_db_session() as conn:
            tax = await conn.fetchrow(
                """
                SELECT s.is_active AS subject_active, t.is_active AS topic_active
                FROM public.kb_topics t
                JOIN public.kb_subjects s ON s.id = t.subject_id
                WHERE t.id = $1 AND s.id = $2
                """,
                doc["topic_id"],
                doc["subject_id"],
            )
        if not tax or not tax["subject_active"] or not tax["topic_active"]:
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        if doc["upload_status"] == "uploaded":
            return DocumentDetail.model_validate(doc)

        if doc["uploader_id"] == user.id and doc["upload_status"] in (
            "pending_upload",
            "upload_failed",
        ):
            return DocumentDetail.model_validate(doc)

        raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

    @staticmethod
    async def get_document_pages(
        document_id: UUID, user: UserProfile, limit: int = 50, offset: int = 0
    ) -> DocumentPagesResponse:
        """Fetch paginated extracted text pages for an authorized document."""
        # Check document visibility first
        await KBService.get_document(document_id, user)

        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)

        async with get_admin_db_session() as conn:
            pages, total = await repository.get_document_pages(
                conn, document_id, limit=bounded_limit, offset=bounded_offset
            )

        items = [DocumentPageItem.model_validate(p) for p in pages]
        return DocumentPagesResponse(
            items=items,
            total=total,
            limit=bounded_limit,
            offset=bounded_offset,
        )

    @staticmethod
    async def create_upload_intent(
        user: UserProfile, req: UploadIntentRequest
    ) -> UploadIntentResponse:
        """Initiate document upload reservation with signed storage capability."""
        # 1. Enforce per-user daily upload quota via Redis (20 uploads/day)
        await check_rate_limit(
            key_prefix="kb_upload_daily",
            identifier=str(user.id),
            limit=settings.KB_USER_DAILY_UPLOAD_LIMIT,
            window_seconds=86400,
        )

        # 2. Input sanitization & validation
        clean_title = sanitize_title(req.title)
        clean_filename = validate_filename(req.original_filename, req.mime_type)

        if req.file_size_bytes <= 0 or req.file_size_bytes > settings.KB_MAX_FILE_SIZE_BYTES:
            raise BadRequestError(
                f"File size must be between 1 and {settings.KB_MAX_FILE_SIZE_BYTES} bytes (50MB).",
                code="INVALID_FILE_SIZE",
            )

        # 3. Taxonomy hierarchy integrity check
        async with get_admin_db_session() as conn:
            topic = await repository.get_topic_by_id(conn, req.topic_id)
            if not topic or not topic["is_active"]:
                raise NotFoundError(
                    f"Topic '{req.topic_id}' not found or inactive.",
                    code="TOPIC_NOT_FOUND",
                )
            if topic["subject_id"] != req.subject_id:
                raise BadRequestError(
                    f"Topic '{req.topic_id}' does not belong to subject '{req.subject_id}'.",
                    code="TOPIC_SUBJECT_MISMATCH",
                )

        # 4. Reservation parameters
        doc_id = uuid4()
        ext = "pdf" if req.mime_type == "application/pdf" else "txt"
        storage_path = f"documents/{doc_id}/source.{ext}"
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=settings.KB_SIGNED_UPLOAD_EXPIRY_SECONDS)

        # 5. Persist pending document record
        async with get_admin_db_session() as conn:
            await repository.create_pending_document(
                conn=conn,
                document_id=doc_id,
                subject_id=req.subject_id,
                topic_id=req.topic_id,
                uploader_id=user.id,
                uploader_email_snapshot=user.email,
                title=clean_title,
                original_filename=clean_filename,
                storage_path=storage_path,
                mime_type=req.mime_type,
                file_size_bytes=req.file_size_bytes,
                sha256_checksum=req.sha256_checksum.lower() if req.sha256_checksum else None,
                upload_expires_at=expires_at,
            )

        # 6. Generate signed upload URL from Supabase Storage
        signed_upload = await storage.create_signed_upload(storage_path)

        return UploadIntentResponse(
            document_id=doc_id,
            storage_path=storage_path,
            signed_upload_url=signed_upload["signed_url"],
            token=signed_upload["token"],
            upload_expires_at=expires_at,
            max_file_size_bytes=settings.KB_MAX_FILE_SIZE_BYTES,
            supported_methods=["PUT", "TUS"],
        )

    @staticmethod
    async def confirm_upload(
        user: UserProfile, document_id: UUID, req: ConfirmUploadRequest
    ) -> ConfirmUploadResponse:
        """Independently verify uploaded file in storage, validate structure, and enqueue extraction."""
        is_admin = user.role in ("admin", "super_admin")

        # 1. Fetch document and verify authorization & state
        async with get_admin_db_session() as conn:
            doc = await repository.get_raw_document_by_id(conn, document_id)

        if not doc or doc["is_archived"]:
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        if not is_admin and doc["uploader_id"] != user.id:
            raise BadRequestError(
                "You are not authorized to confirm this document.",
                code="UNAUTHORIZED_CONFIRMATION",
            )

        # Idempotent confirmation if already uploaded
        if doc["upload_status"] == "uploaded":
            return ConfirmUploadResponse(
                document=DocumentDetail.model_validate(doc),
                message="Document upload was already verified and queued.",
            )

        if doc["upload_status"] != "pending_upload":
            raise BadRequestError(
                f"Cannot confirm document in status '{doc['upload_status']}'.",
                code="INVALID_DOCUMENT_STATUS",
            )

        # Check reservation expiration
        now = datetime.now(UTC)
        if doc.get("upload_expires_at") and doc["upload_expires_at"] < now:
            async with get_admin_db_session() as conn:
                await repository.mark_upload_failed(
                    conn,
                    document_id,
                    "RESERVATION_EXPIRED",
                    "Upload reservation expired before confirmation.",
                )
            await storage.delete_storage_object(doc["storage_path"])
            raise BadRequestError(
                "Upload reservation has expired. Please initiate a new upload intent.",
                code="RESERVATION_EXPIRED",
            )

        # 2 & 3. Stream file from storage with strictly bounded memory & validate
        expected_size = req.file_size_bytes or doc["file_size_bytes"]
        expected_checksum = req.sha256_checksum or doc["sha256_checksum"]

        try:
            async with storage.open_storage_stream(doc["storage_path"]) as stream:
                actual_size, actual_sha256 = await validate_storage_stream(
                    stream=stream,
                    mime_type=doc["mime_type"],
                    expected_size=expected_size,
                    expected_checksum=expected_checksum,
                )
        except NotFoundError as e:
            raise BadRequestError(
                "Uploaded file was not found in storage. Ensure file was uploaded to the signed URL before confirmation.",
                code="FILE_NOT_FOUND_IN_STORAGE",
            ) from e
        except BadRequestError as exc:
            # Verification failed: clean up invalid storage object and mark document failed
            logger.warning(
                f"Document confirmation failed for {document_id} ({exc.code}): {exc.message}"
            )
            await storage.delete_storage_object(doc["storage_path"])
            async with get_admin_db_session() as conn:
                await repository.mark_upload_failed(conn, document_id, exc.code, exc.message)
            raise exc

        # 4. Atomic status transition & durable extraction job queuing
        async with get_admin_db_session() as conn:
            updated_doc = await repository.confirm_uploaded_document(
                conn=conn,
                document_id=document_id,
                actual_size=actual_size,
                actual_sha256=actual_sha256,
            )

        if not updated_doc:
            # Check if another concurrent request successfully confirmed it
            async with get_admin_db_session() as conn:
                doc_concurrent = await repository.get_raw_document_by_id(conn, document_id)
            if doc_concurrent and doc_concurrent["upload_status"] == "uploaded":
                return ConfirmUploadResponse(
                    document=DocumentDetail.model_validate(doc_concurrent),
                    message="Document upload was already verified and queued.",
                )
            raise ConflictError(
                "Failed to confirm document. It may have been updated concurrently.",
                code="CONCURRENT_UPDATE",
            )

        # 5. Audit log
        await _log_kb_audit(
            actor_id=user.id,
            action="kb_document_upload_confirmed",
            document_id=document_id,
            details={
                "title": doc["title"],
                "file_size_bytes": actual_size,
                "mime_type": doc["mime_type"],
                "sha256_checksum": actual_sha256,
            },
        )

        return ConfirmUploadResponse(
            document=DocumentDetail.model_validate(updated_doc),
            message="Document upload verified and queued for extraction.",
        )

    @staticmethod
    async def get_download_url(document_id: UUID, user: UserProfile) -> DownloadUrlResponse:
        """Generate a time-limited (300s) signed download URL for visible document."""
        doc = await KBService.get_document(document_id, user)

        if doc.upload_status != "uploaded":
            raise BadRequestError(
                f"Document file is not ready for download (status: '{doc.upload_status}').",
                code="DOCUMENT_NOT_READY",
            )

        # Fetch raw doc storage path
        async with get_admin_db_session() as conn:
            raw = await repository.get_raw_document_by_id(conn, document_id)
        if not raw:
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        signed_url = await storage.create_signed_download(
            raw["storage_path"], expires_in=settings.KB_SIGNED_DOWNLOAD_EXPIRY_SECONDS
        )

        return DownloadUrlResponse(
            download_url=signed_url,
            expires_in=settings.KB_SIGNED_DOWNLOAD_EXPIRY_SECONDS,
            original_filename=raw["original_filename"],
            mime_type=raw["mime_type"],
        )

    @staticmethod
    async def delete_document(document_id: UUID, user: UserProfile) -> None:
        """Safely soft-delete document, remove storage object, and mark archived."""
        is_admin = user.role in ("admin", "super_admin")

        # 1. Fetch document and verify existence
        async with get_admin_db_session() as conn:
            doc = await repository.get_raw_document_by_id(conn, document_id)

        if not doc or doc["is_archived"] or doc["upload_status"] == "deleted":
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        if not is_admin and doc["uploader_id"] != user.id:
            raise BadRequestError(
                "You are not authorized to delete this document.",
                code="UNAUTHORIZED_DELETION",
            )

        # 2. Transition state to 'deleting'
        async with get_admin_db_session() as conn:
            transitioned = await repository.transition_document_to_deleting(
                conn=conn,
                document_id=document_id,
                user_id=user.id,
                is_admin=is_admin,
            )

        if not transitioned:
            raise NotFoundError(
                "Document could not be transitioned to deleting.",
                code="DOCUMENT_NOT_FOUND",
            )

        # 3. Delete object from private Supabase Storage
        await storage.delete_storage_object(transitioned["storage_path"])

        # 4. Finalize deletion: set upload_status = 'deleted', is_archived = true
        async with get_admin_db_session() as conn:
            await repository.finalize_document_deletion(conn, document_id)

        # 5. Audit log
        await _log_kb_audit(
            actor_id=user.id,
            action="kb_document_deleted",
            document_id=document_id,
            details={
                "title": transitioned["title"],
                "storage_path": transitioned["storage_path"],
            },
        )


async def _log_kb_audit(
    actor_id: UUID,
    action: str,
    document_id: UUID,
    details: dict | None = None,
) -> None:
    """Record immutable audit log entry for Knowledge Base operations."""
    try:
        async with get_admin_db_session() as conn:
            await conn.execute(
                """
                INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
                VALUES ($1, $2, 'kb_document', $3, $4::jsonb)
                """,
                actor_id,
                action,
                document_id,
                json.dumps(details or {}),
            )
    except Exception as exc:
        logger.error(f"Failed to record audit log for KB action '{action}': {exc}", exc_info=True)
