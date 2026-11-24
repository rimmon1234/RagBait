"""Service layer for Knowledge Base administrative management, moderation, and retry."""

import logging
from uuid import UUID, uuid4

from app.core.database import get_admin_db_session
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.modules.knowledge_base import admin_repository, repository, storage
from app.modules.knowledge_base.admin_schemas import (
    AdminAuditItem,
    AdminDocumentDetail,
    AdminDocumentListItem,
    AdminJobDetail,
    AdminJobListItem,
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
from app.modules.knowledge_base.validators import sanitize_title
from app.modules.users.schemas import UserProfile

logger = logging.getLogger("ragbait.kb.admin_service")

# Explicit classification of extraction failure categories
RETRYABLE_ERROR_CODES = {
    "PARSER_TIMEOUT",
    "EXTRACTION_TIMEOUT",
    "STORAGE_STREAM_FAILED",
    "STORAGE_DOWNLOAD_FAILED",
    "DATABASE_ERROR",
    "WORKER_CRASH",
    "INFRASTRUCTURE_ERROR",
    "EXTRACTION_FAILED",
    "ARCHIVED_BY_ADMIN",
}

PERMANENT_ERROR_CODES = {
    "CORRUPTED_FILE",
    "ENCRYPTED_PDF",
    "PAGE_LIMIT_EXCEEDED",
    "PAYLOAD_TOO_LARGE",
    "MALFORMED_TEXT",
    "INVALID_MIME_TYPE",
    "CHECKSUM_MISMATCH",
    "FILE_SIZE_MISMATCH",
    "STORAGE_OBJECT_MISSING",
}

MAX_MANUAL_RETRIES = 2


class KBAdminService:
    """Administrative business logic and moderation controller."""

    # --------------------------------------------------------------------------
    # Overview
    # --------------------------------------------------------------------------

    @staticmethod
    async def get_overview() -> AdminOverviewMetrics:
        """Fetch dashboard metrics and recent activity."""
        async with get_admin_db_session() as conn:
            metrics = await admin_repository.get_admin_overview_metrics(conn)
            docs, _ = await admin_repository.list_admin_documents(conn, limit=5, offset=0)
            audits, _ = await admin_repository.list_kb_audit_logs(conn, limit=5, offset=0)

        return AdminOverviewMetrics(
            active_subjects_count=metrics.get("active_subjects", 0),
            total_subjects_count=metrics.get("total_subjects", 0),
            active_topics_count=metrics.get("active_topics", 0),
            total_topics_count=metrics.get("total_topics", 0),
            total_documents_count=metrics.get("total_documents", 0),
            archived_documents_count=metrics.get("archived_documents", 0),
            queued_jobs_count=metrics.get("queued_jobs", 0),
            processing_jobs_count=metrics.get("processing_jobs", 0),
            failed_jobs_count=metrics.get("failed_jobs", 0),
            completed_jobs_count=metrics.get("completed_jobs", 0),
            recent_documents=[AdminDocumentListItem.model_validate(d) for d in docs],
            recent_actions=[AdminAuditItem.model_validate(a) for a in audits],
        )

    # --------------------------------------------------------------------------
    # Subjects
    # --------------------------------------------------------------------------

    @staticmethod
    async def list_subjects(
        include_inactive: bool = True, search: str | None = None
    ) -> list[AdminSubjectItem]:
        """List subjects for administrator management."""
        async with get_admin_db_session() as conn:
            rows = await admin_repository.list_admin_subjects(
                conn, include_inactive=include_inactive, search=search
            )
        return [AdminSubjectItem.model_validate(r) for r in rows]

    @staticmethod
    async def create_subject(user: UserProfile, req: SubjectCreateRequest) -> AdminSubjectItem:
        """Create a new academic subject with uniqueness checks and audit logging."""
        clean_name = sanitize_title(req.name)
        async with get_admin_db_session() as conn:
            # Check name uniqueness
            if await admin_repository.get_subject_by_name(conn, clean_name):
                raise ConflictError(
                    f"Subject with name '{clean_name}' already exists.",
                    code="SUBJECT_NAME_EXISTS",
                )
            # Check slug uniqueness
            if await admin_repository.get_subject_by_slug(conn, req.slug):
                raise ConflictError(
                    f"Subject with slug '{req.slug}' already exists.",
                    code="SUBJECT_SLUG_EXISTS",
                )

            subject_id = uuid4()
            async with conn.transaction():
                created = await admin_repository.create_subject(
                    conn=conn,
                    subject_id=subject_id,
                    name=clean_name,
                    slug=req.slug,
                    description=req.description,
                    icon_name=req.icon_name,
                    display_order=req.display_order,
                    created_by=user.id,
                )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.subject_create",
                    target_type="kb_subject",
                    target_id=subject_id,
                    payload={
                        "name": clean_name,
                        "slug": req.slug,
                        "icon_name": req.icon_name,
                        "display_order": req.display_order,
                    },
                )

        return AdminSubjectItem.model_validate(created)

    @staticmethod
    async def update_subject(
        user: UserProfile, subject_id: UUID, req: SubjectUpdateRequest
    ) -> AdminSubjectItem:
        """Update an existing subject with conflict validation and audit logging."""
        clean_name = sanitize_title(req.name) if req.name else None

        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_subject_by_id(conn, subject_id)
            if not existing:
                raise NotFoundError(f"Subject '{subject_id}' not found.", code="SUBJECT_NOT_FOUND")

            if clean_name and clean_name.lower() != existing["name"].lower():
                dup_name = await admin_repository.get_subject_by_name(conn, clean_name)
                if dup_name and dup_name["id"] != subject_id:
                    raise ConflictError(
                        f"Subject with name '{clean_name}' already exists.",
                        code="SUBJECT_NAME_EXISTS",
                    )

            if req.slug and req.slug != existing["slug"]:
                dup_slug = await admin_repository.get_subject_by_slug(conn, req.slug)
                if dup_slug and dup_slug["id"] != subject_id:
                    raise ConflictError(
                        f"Subject with slug '{req.slug}' already exists.",
                        code="SUBJECT_SLUG_EXISTS",
                    )

            async with conn.transaction():
                updated = await admin_repository.update_subject(
                    conn=conn,
                    subject_id=subject_id,
                    name=clean_name,
                    slug=req.slug,
                    description=req.description,
                    icon_name=req.icon_name,
                    display_order=req.display_order,
                )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.subject_update",
                    target_type="kb_subject",
                    target_id=subject_id,
                    payload={
                        "changes": {
                            k: v
                            for k, v in {
                                "name": clean_name,
                                "slug": req.slug,
                                "icon_name": req.icon_name,
                                "display_order": req.display_order,
                            }.items()
                            if v is not None
                        }
                    },
                )

        return AdminSubjectItem.model_validate(updated)

    @staticmethod
    async def archive_subject(user: UserProfile, subject_id: UUID) -> AdminSubjectItem:
        """Archive a subject (is_active = false) without cascading deletion."""
        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_subject_by_id(conn, subject_id)
            if not existing:
                raise NotFoundError(f"Subject '{subject_id}' not found.", code="SUBJECT_NOT_FOUND")

            async with conn.transaction():
                updated = await admin_repository.set_subject_active_status(
                    conn, subject_id, is_active=False
                )
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.subject_archive",
                    target_type="kb_subject",
                    target_id=subject_id,
                    payload={"name": existing["name"], "slug": existing["slug"]},
                )

        return AdminSubjectItem.model_validate(updated)

    @staticmethod
    async def restore_subject(user: UserProfile, subject_id: UUID) -> AdminSubjectItem:
        """Restore an archived subject (is_active = true)."""
        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_subject_by_id(conn, subject_id)
            if not existing:
                raise NotFoundError(f"Subject '{subject_id}' not found.", code="SUBJECT_NOT_FOUND")

            async with conn.transaction():
                updated = await admin_repository.set_subject_active_status(
                    conn, subject_id, is_active=True
                )
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.subject_restore",
                    target_type="kb_subject",
                    target_id=subject_id,
                    payload={"name": existing["name"], "slug": existing["slug"]},
                )

        return AdminSubjectItem.model_validate(updated)

    # --------------------------------------------------------------------------
    # Topics
    # --------------------------------------------------------------------------

    @staticmethod
    async def list_topics(subject_id: UUID, include_inactive: bool = True) -> list[AdminTopicItem]:
        """List topics belonging to a subject."""
        async with get_admin_db_session() as conn:
            subject = await admin_repository.get_admin_subject_by_id(conn, subject_id)
            if not subject:
                raise NotFoundError(f"Subject '{subject_id}' not found.", code="SUBJECT_NOT_FOUND")
            rows = await admin_repository.list_admin_topics(
                conn, subject_id, include_inactive=include_inactive
            )
        return [AdminTopicItem.model_validate(r) for r in rows]

    @staticmethod
    async def create_topic(
        user: UserProfile, subject_id: UUID, req: TopicCreateRequest
    ) -> AdminTopicItem:
        """Create a new topic ensuring active parent subject and unique slug."""
        clean_name = sanitize_title(req.name)

        async with get_admin_db_session() as conn:
            subject = await admin_repository.get_admin_subject_by_id(conn, subject_id)
            if not subject:
                raise NotFoundError(f"Subject '{subject_id}' not found.", code="SUBJECT_NOT_FOUND")
            if not subject["is_active"]:
                raise BadRequestError(
                    f"Cannot create a topic under inactive subject '{subject['name']}'.",
                    code="SUBJECT_INACTIVE",
                )

            dup_slug = await admin_repository.get_topic_by_subject_and_slug(
                conn, subject_id, req.slug
            )
            if dup_slug:
                raise ConflictError(
                    f"Topic with slug '{req.slug}' already exists in this subject.",
                    code="TOPIC_SLUG_EXISTS",
                )

            topic_id = uuid4()
            async with conn.transaction():
                await admin_repository.create_topic(
                    conn=conn,
                    topic_id=topic_id,
                    subject_id=subject_id,
                    name=clean_name,
                    slug=req.slug,
                    description=req.description,
                    display_order=req.display_order,
                    created_by=user.id,
                )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.topic_create",
                    target_type="kb_topic",
                    target_id=topic_id,
                    payload={
                        "subject_id": str(subject_id),
                        "name": clean_name,
                        "slug": req.slug,
                        "display_order": req.display_order,
                    },
                )

        # Re-fetch full item with subject metadata
        return await KBAdminService.get_topic_detail(topic_id)

    @staticmethod
    async def get_topic_detail(topic_id: UUID) -> AdminTopicItem:
        """Fetch full topic details."""
        async with get_admin_db_session() as conn:
            row = await admin_repository.get_admin_topic_by_id(conn, topic_id)
        if not row:
            raise NotFoundError(f"Topic '{topic_id}' not found.", code="TOPIC_NOT_FOUND")
        return AdminTopicItem.model_validate(row)

    @staticmethod
    async def update_topic(
        user: UserProfile, topic_id: UUID, req: TopicUpdateRequest
    ) -> AdminTopicItem:
        """Update topic metadata."""
        clean_name = sanitize_title(req.name) if req.name else None

        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_topic_by_id(conn, topic_id)
            if not existing:
                raise NotFoundError(f"Topic '{topic_id}' not found.", code="TOPIC_NOT_FOUND")

            if req.slug and req.slug != existing["slug"]:
                dup = await admin_repository.get_topic_by_subject_and_slug(
                    conn, existing["subject_id"], req.slug
                )
                if dup and dup["id"] != topic_id:
                    raise ConflictError(
                        f"Topic with slug '{req.slug}' already exists in this subject.",
                        code="TOPIC_SLUG_EXISTS",
                    )

            async with conn.transaction():
                await admin_repository.update_topic(
                    conn=conn,
                    topic_id=topic_id,
                    name=clean_name,
                    slug=req.slug,
                    description=req.description,
                    display_order=req.display_order,
                )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.topic_update",
                    target_type="kb_topic",
                    target_id=topic_id,
                    payload={
                        "changes": {
                            k: v
                            for k, v in {
                                "name": clean_name,
                                "slug": req.slug,
                                "display_order": req.display_order,
                            }.items()
                            if v is not None
                        }
                    },
                )

        return await KBAdminService.get_topic_detail(topic_id)

    @staticmethod
    async def reorder_topic(
        user: UserProfile, topic_id: UUID, req: TopicReorderRequest
    ) -> AdminTopicItem:
        """Update topic display order atomically."""
        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_topic_by_id(conn, topic_id)
            if not existing:
                raise NotFoundError(f"Topic '{topic_id}' not found.", code="TOPIC_NOT_FOUND")

            async with conn.transaction():
                await admin_repository.update_topic(
                    conn=conn,
                    topic_id=topic_id,
                    name=None,
                    slug=None,
                    description=None,
                    display_order=req.display_order,
                )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.topic_reorder",
                    target_type="kb_topic",
                    target_id=topic_id,
                    payload={
                        "old_display_order": existing["display_order"],
                        "new_display_order": req.display_order,
                    },
                )

        return await KBAdminService.get_topic_detail(topic_id)

    @staticmethod
    async def archive_topic(user: UserProfile, topic_id: UUID) -> AdminTopicItem:
        """Archive a topic (is_active = false)."""
        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_topic_by_id(conn, topic_id)
            if not existing:
                raise NotFoundError(f"Topic '{topic_id}' not found.", code="TOPIC_NOT_FOUND")

            async with conn.transaction():
                await admin_repository.set_topic_active_status(conn, topic_id, is_active=False)
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.topic_archive",
                    target_type="kb_topic",
                    target_id=topic_id,
                    payload={"name": existing["name"], "slug": existing["slug"]},
                )

        return await KBAdminService.get_topic_detail(topic_id)

    @staticmethod
    async def restore_topic(user: UserProfile, topic_id: UUID) -> AdminTopicItem:
        """Restore an archived topic, ensuring its parent subject is active."""
        async with get_admin_db_session() as conn:
            existing = await admin_repository.get_admin_topic_by_id(conn, topic_id)
            if not existing:
                raise NotFoundError(f"Topic '{topic_id}' not found.", code="TOPIC_NOT_FOUND")

            # Parent subject must be active
            subject = await admin_repository.get_admin_subject_by_id(conn, existing["subject_id"])
            if not subject or not subject["is_active"]:
                raise BadRequestError(
                    f"Cannot restore topic '{existing['name']}' because parent subject '{existing['subject_name']}' is archived.",
                    code="PARENT_SUBJECT_ARCHIVED",
                )

            async with conn.transaction():
                await admin_repository.set_topic_active_status(conn, topic_id, is_active=True)
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.topic_restore",
                    target_type="kb_topic",
                    target_id=topic_id,
                    payload={"name": existing["name"], "slug": existing["slug"]},
                )

        return await KBAdminService.get_topic_detail(topic_id)

    # --------------------------------------------------------------------------
    # Document Moderation
    # --------------------------------------------------------------------------

    @staticmethod
    async def list_documents(
        limit: int = 20,
        offset: int = 0,
        subject_id: UUID | None = None,
        topic_id: UUID | None = None,
        uploader_id: UUID | None = None,
        upload_status: str | None = None,
        extraction_status: str | None = None,
        is_archived: bool | None = None,
        search: str | None = None,
    ) -> PaginatedAdminDocumentsResponse:
        """List documents across subjects with administrative filtering."""
        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)

        async with get_admin_db_session() as conn:
            docs, total = await admin_repository.list_admin_documents(
                conn=conn,
                limit=bounded_limit,
                offset=bounded_offset,
                subject_id=subject_id,
                topic_id=topic_id,
                uploader_id=uploader_id,
                upload_status=upload_status,
                extraction_status=extraction_status,
                is_archived=is_archived,
                search=search,
            )

        items = [AdminDocumentListItem.model_validate(d) for d in docs]
        return PaginatedAdminDocumentsResponse(
            items=items,
            total=total,
            limit=bounded_limit,
            offset=bounded_offset,
        )

    @staticmethod
    async def get_document_detail(document_id: UUID) -> AdminDocumentDetail:
        """Fetch detailed document moderation view."""
        async with get_admin_db_session() as conn:
            doc = await admin_repository.get_admin_document_detail(conn, document_id)
        if not doc:
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")
        return AdminDocumentDetail.model_validate(doc)

    @staticmethod
    async def archive_document(
        user: UserProfile, document_id: UUID, req: DocumentArchiveRequest
    ) -> AdminDocumentDetail:
        """Archive a document with mandatory moderation reason and worker lease fencing."""
        clean_reason = sanitize_title(req.reason)

        async with get_admin_db_session() as conn:
            async with conn.transaction():
                # Document-first lock
                doc = await conn.fetchrow(
                    """
                    SELECT id, title, storage_path, is_archived, upload_status, extraction_status
                    FROM public.kb_documents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    document_id,
                )
                if not doc or doc["upload_status"] == "deleted":
                    raise NotFoundError(
                        f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND"
                    )

                # Job-second lock and worker fencing if currently active
                active_job = await conn.fetchrow(
                    """
                    SELECT id, status, worker_id, lease_token
                    FROM public.kb_extraction_jobs
                    WHERE document_id = $1 AND status IN ('queued', 'processing')
                    FOR UPDATE
                    """,
                    document_id,
                )
                if active_job:
                    # Invalidate lease so stale worker cannot complete
                    await conn.execute(
                        """
                        UPDATE public.kb_extraction_jobs
                        SET status = 'failed',
                            error_code = 'ARCHIVED_BY_ADMIN',
                            error_message = 'Extraction interrupted: document was archived by administrator.',
                            lease_token = NULL,
                            locked_until = NULL,
                            updated_at = timezone('utc'::text, now())
                        WHERE id = $1
                        """,
                        active_job["id"],
                    )

                # Set document archived; if active job was fenced, update extraction_status to failed
                if active_job:
                    await conn.execute(
                        """
                        UPDATE public.kb_documents
                        SET is_archived = true,
                            extraction_status = 'failed',
                            error_code = 'ARCHIVED_BY_ADMIN',
                            error_message = 'Extraction interrupted: document was archived by administrator.',
                            updated_at = timezone('utc'::text, now())
                        WHERE id = $1
                        """,
                        document_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE public.kb_documents
                        SET is_archived = true,
                            updated_at = timezone('utc'::text, now())
                        WHERE id = $1
                        """,
                        document_id,
                    )

                # Fence and cancel any active indexing jobs
                await conn.execute(
                    """
                    UPDATE public.kb_indexing_jobs
                    SET status = 'cancelled',
                        lease_token = NULL,
                        locked_until = NULL,
                        error_code = 'ARCHIVED_BY_ADMIN',
                        error_message = 'Indexing cancelled: document was archived by administrator.',
                        completed_at = timezone('utc'::text, now()),
                        updated_at = timezone('utc'::text, now())
                    WHERE document_id = $1 AND status IN ('queued', 'processing');
                    """,
                    document_id,
                )

                # Fence and cancel any active embedding jobs
                await conn.execute(
                    """
                    UPDATE public.kb_embedding_jobs
                    SET status = 'cancelled',
                        lease_token = NULL,
                        locked_until = NULL,
                        error_code = 'ARCHIVED_BY_ADMIN',
                        error_message = 'Embedding cancelled: document was archived by administrator.',
                        completed_at = timezone('utc'::text, now()),
                        updated_at = timezone('utc'::text, now())
                    WHERE document_id = $1 AND status IN ('queued', 'processing');
                    """,
                    document_id,
                )

                # Deactivate any active chunks for this archived document
                await conn.execute(
                    """
                    UPDATE public.kb_document_chunks
                    SET is_active = false
                    WHERE document_id = $1;
                    """,
                    document_id,
                )

                # Record audit log within the same transaction
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.document_archive",
                    target_type="kb_document",
                    target_id=document_id,
                    payload={
                        "title": doc["title"],
                        "reason": clean_reason,
                        "had_active_job": bool(active_job),
                    },
                )

        return await KBAdminService.get_document_detail(document_id)

    @staticmethod
    async def restore_document(
        user: UserProfile, document_id: UUID, req: DocumentRestoreRequest
    ) -> AdminDocumentDetail:
        """Restore an archived document ensuring its subject and topic are currently active."""
        clean_reason = sanitize_title(req.reason) if req.reason else None

        async with get_admin_db_session() as conn:
            async with conn.transaction():
                # Document-first lock
                doc = await conn.fetchrow(
                    """
                    SELECT id, title, subject_id, topic_id, is_archived, upload_status, extraction_status
                    FROM public.kb_documents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    document_id,
                )
                if not doc or doc["upload_status"] == "deleted":
                    raise NotFoundError(
                        f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND"
                    )

                # Taxonomy active check
                tax = await conn.fetchrow(
                    """
                    SELECT s.is_active AS subject_active, s.name AS subject_name,
                           t.is_active AS topic_active, t.name AS topic_name
                    FROM public.kb_topics t
                    JOIN public.kb_subjects s ON s.id = t.subject_id
                    WHERE t.id = $1 AND s.id = $2
                    """,
                    doc["topic_id"],
                    doc["subject_id"],
                )
                if not tax or not tax["subject_active"] or not tax["topic_active"]:
                    raise BadRequestError(
                        "Cannot restore document under archived subject or topic. Restore the taxonomy first.",
                        code="TAXONOMY_INACTIVE",
                    )

                await conn.execute(
                    """
                    UPDATE public.kb_documents
                    SET is_archived = false,
                        updated_at = timezone('utc'::text, now())
                    WHERE id = $1
                    """,
                    document_id,
                )

                # If document was previously extracted and has text, re-enqueue an indexing job
                if doc["extraction_status"] == "completed":
                    await conn.execute(
                        """
                        INSERT INTO public.kb_indexing_jobs (
                            document_id,
                            source_version,
                            chunking_version,
                            status,
                            scheduled_at
                        ) VALUES ($1, 1, 1, 'queued', timezone('utc'::text, now()))
                        ON CONFLICT (document_id) WHERE status IN ('queued', 'processing')
                        DO NOTHING;
                        """,
                        document_id,
                    )

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.document_restore",
                    target_type="kb_document",
                    target_id=document_id,
                    payload={"title": doc["title"], "reason": clean_reason},
                )

        return await KBAdminService.get_document_detail(document_id)

    @staticmethod
    async def delete_document(
        user: UserProfile, document_id: UUID, req: DocumentDeleteRequest
    ) -> None:
        """Permanently remove a document through the secure deletion lifecycle."""
        clean_reason = sanitize_title(req.reason)

        # 1. Fetch document and verify existence
        async with get_admin_db_session() as conn:
            doc = await repository.get_raw_document_by_id(conn, document_id)

        if not doc or doc["upload_status"] == "deleted":
            raise NotFoundError(f"Document '{document_id}' not found.", code="DOCUMENT_NOT_FOUND")

        # 2. Transition state to 'deleting'
        async with get_admin_db_session() as conn:
            transitioned = await repository.transition_document_to_deleting(
                conn=conn,
                document_id=document_id,
                user_id=user.id,
                is_admin=True,
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
            async with conn.transaction():
                await repository.finalize_document_deletion(conn, document_id)

                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.document_delete",
                    target_type="kb_document",
                    target_id=document_id,
                    payload={
                        "title": transitioned["title"],
                        "storage_path": transitioned["storage_path"],
                        "reason": clean_reason,
                    },
                )

    # --------------------------------------------------------------------------
    # Extraction Jobs & Retry
    # --------------------------------------------------------------------------

    @staticmethod
    async def list_jobs(
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        subject_id: UUID | None = None,
        topic_id: UUID | None = None,
        search: str | None = None,
    ) -> PaginatedAdminJobsResponse:
        """List extraction jobs with dynamic retry eligibility indicators."""
        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)

        async with get_admin_db_session() as conn:
            rows, total = await admin_repository.list_admin_jobs(
                conn=conn,
                limit=bounded_limit,
                offset=bounded_offset,
                status=status,
                subject_id=subject_id,
                topic_id=topic_id,
                search=search,
            )

        items = []
        for r in rows:
            # Determine is_retryable based on failure categorization and retry count
            is_retriable = (
                r["status"] == "failed"
                and (r["retry_count"] or 0) < MAX_MANUAL_RETRIES
                and (
                    r.get("error_code") in RETRYABLE_ERROR_CODES
                    or r.get("error_code") not in PERMANENT_ERROR_CODES
                )
            )
            item_dict = dict(r)
            item_dict["is_retryable"] = is_retriable
            items.append(AdminJobListItem.model_validate(item_dict))

        return PaginatedAdminJobsResponse(
            items=items,
            total=total,
            limit=bounded_limit,
            offset=bounded_offset,
        )

    @staticmethod
    async def get_job_detail(job_id: UUID) -> AdminJobDetail:
        """Fetch sanitized job detail with explicit retry eligibility reason."""
        async with get_admin_db_session() as conn:
            job = await admin_repository.get_admin_job_detail(conn, job_id)
        if not job:
            raise NotFoundError(f"Extraction job '{job_id}' not found.", code="JOB_NOT_FOUND")

        # Determine eligibility and explanatory reason
        eligibility_reason: str | None = None
        is_retryable = False

        if job["status"] != "failed":
            eligibility_reason = (
                f"Job is in '{job['status']}' state; only failed jobs can be retried."
            )
        elif job["document_upload_status"] != "uploaded":
            eligibility_reason = "Underlying document file is not in 'uploaded' state."
        elif job["document_is_archived"]:
            eligibility_reason = (
                "Associated document is archived. Restore the document before retrying extraction."
            )
        elif (job.get("retry_count") or 0) >= MAX_MANUAL_RETRIES:
            eligibility_reason = f"Maximum manual retries ({MAX_MANUAL_RETRIES}) reached for this extraction lineage."
        elif job.get("error_code") in PERMANENT_ERROR_CODES:
            eligibility_reason = f"Permanent extraction fault ('{job['error_code']}'); cannot be retried without document re-upload."
        else:
            is_retryable = True
            eligibility_reason = "Eligible for administrator retry."

        item_dict = dict(job)
        item_dict["is_retryable"] = is_retryable
        item_dict["retry_eligibility_reason"] = eligibility_reason

        return AdminJobDetail.model_validate(item_dict)

    @staticmethod
    async def retry_job(user: UserProfile, job_id: UUID, req: JobRetryRequest) -> JobRetryResponse:
        """Atomically retry an eligible failed extraction job using document-first locking."""
        clean_reason = sanitize_title(req.reason) if req.reason else None

        async with get_admin_db_session() as conn:
            async with conn.transaction():
                # 1. Inspect target job to find associated document
                target_job = await conn.fetchrow(
                    "SELECT id, document_id, status, error_code, retry_count FROM public.kb_extraction_jobs WHERE id = $1",
                    job_id,
                )
                if not target_job:
                    raise NotFoundError(
                        f"Extraction job '{job_id}' not found.", code="JOB_NOT_FOUND"
                    )

                doc_id = target_job["document_id"]

                # 2. Document-first lock
                doc = await conn.fetchrow(
                    """
                    SELECT id, title, subject_id, topic_id, upload_status, is_archived, storage_path
                    FROM public.kb_documents
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    doc_id,
                )
                if not doc or doc["upload_status"] == "deleted":
                    raise BadRequestError(
                        "Associated document does not exist or has been deleted.",
                        code="DOCUMENT_NOT_FOUND",
                    )
                if doc["upload_status"] != "uploaded":
                    raise BadRequestError(
                        f"Document upload status is '{doc['upload_status']}', expected 'uploaded'.",
                        code="DOCUMENT_NOT_READY",
                    )
                if doc["is_archived"]:
                    raise BadRequestError(
                        "Cannot retry extraction on an archived document. Restore the document first.",
                        code="DOCUMENT_ARCHIVED",
                    )

                # 3. Check taxonomy is active
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
                    raise BadRequestError(
                        "Cannot retry extraction under an inactive subject or topic.",
                        code="TAXONOMY_INACTIVE",
                    )

                # 4. Job-second lock
                job = await conn.fetchrow(
                    """
                    SELECT id, status, error_code, retry_count, attempts, max_attempts
                    FROM public.kb_extraction_jobs
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    job_id,
                )
                if not job or job["status"] != "failed":
                    raise BadRequestError(
                        f"Job is in '{job['status'] if job else 'unknown'}' state; only failed jobs can be retried.",
                        code="JOB_NOT_FAILED",
                    )

                # 5. Check permanent error exclusion
                if job.get("error_code") in PERMANENT_ERROR_CODES:
                    raise BadRequestError(
                        f"Cannot retry job with permanent fault '{job['error_code']}'.",
                        code="PERMANENT_FAULT_NOT_RETRYABLE",
                    )

                # 6. Check manual retry limit
                current_retry_count = job.get("retry_count") or 0
                if current_retry_count >= MAX_MANUAL_RETRIES:
                    raise BadRequestError(
                        f"Maximum manual retries ({MAX_MANUAL_RETRIES}) reached for this extraction lineage.",
                        code="RETRY_LIMIT_EXHAUSTED",
                    )

                # 7. Check no active competing job exists
                active_competing = await conn.fetchval(
                    """
                    SELECT id FROM public.kb_extraction_jobs
                    WHERE document_id = $1 AND status IN ('queued', 'processing')
                    """,
                    doc_id,
                )
                if active_competing:
                    raise ConflictError(
                        "Another extraction job is already queued or processing for this document.",
                        code="ACTIVE_JOB_EXISTS",
                    )

                # 8. Insert fresh queued job
                new_job_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO public.kb_extraction_jobs (
                        id, document_id, status, scheduled_at, max_attempts, attempts,
                        retry_count, retry_of_job_id
                    )
                    VALUES ($1, $2, 'queued', timezone('utc'::text, now()), 3, 0, $3, $4)
                    """,
                    new_job_id,
                    doc_id,
                    current_retry_count + 1,
                    job_id,
                )

                # 9. Update document extraction status to 'queued' and clear error
                await conn.execute(
                    """
                    UPDATE public.kb_documents
                    SET extraction_status = 'queued',
                        error_code = NULL,
                        error_message = NULL,
                        updated_at = timezone('utc'::text, now())
                    WHERE id = $1
                    """,
                    doc_id,
                )

                # 10. Audit log within same transaction
                await admin_repository.insert_admin_audit_log(
                    conn=conn,
                    actor_id=user.id,
                    action="kb.job_retry",
                    target_type="kb_extraction_job",
                    target_id=new_job_id,
                    payload={
                        "original_job_id": str(job_id),
                        "document_id": str(doc_id),
                        "title": doc["title"],
                        "previous_error_code": job.get("error_code"),
                        "retry_count": current_retry_count + 1,
                        "reason": clean_reason,
                    },
                )

        # Return sanitized job detail for the newly queued job
        job_detail = await KBAdminService.get_job_detail(new_job_id)
        return JobRetryResponse(
            job=job_detail,
            message="Extraction job successfully queued for retry.",
        )

    # --------------------------------------------------------------------------
    # Audit Logs
    # --------------------------------------------------------------------------

    @staticmethod
    async def list_audit_logs(
        limit: int = 50,
        offset: int = 0,
        action: str | None = None,
        target_type: str | None = None,
        actor_id: UUID | None = None,
    ) -> PaginatedAdminAuditResponse:
        """Fetch audit log history for Knowledge Base administration."""
        bounded_limit = max(1, min(limit, 100))
        bounded_offset = max(0, offset)

        async with get_admin_db_session() as conn:
            rows, total = await admin_repository.list_kb_audit_logs(
                conn=conn,
                limit=bounded_limit,
                offset=bounded_offset,
                action=action,
                target_type=target_type,
                actor_id=actor_id,
            )

        items = [AdminAuditItem.model_validate(r) for r in rows]
        return PaginatedAdminAuditResponse(
            items=items,
            total=total,
            limit=bounded_limit,
            offset=bounded_offset,
        )
