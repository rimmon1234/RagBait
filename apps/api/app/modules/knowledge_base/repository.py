"""Database repository for Knowledge Base and document operations."""

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]


async def get_active_subjects(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Retrieve all active subjects ordered by display_order with child counts."""
    rows = await conn.fetch(
        """
        SELECT
            s.id,
            s.name,
            s.slug,
            s.description,
            s.icon_name,
            s.display_order,
            s.is_active,
            s.created_at,
            s.updated_at,
            (
                SELECT COUNT(*)::int
                FROM public.kb_topics t
                WHERE t.subject_id = s.id AND t.is_active = true
            ) AS topic_count,
            (
                SELECT COUNT(*)::int
                FROM public.kb_documents d
                WHERE d.subject_id = s.id AND d.is_archived = false AND d.upload_status = 'uploaded'
            ) AS document_count
        FROM public.kb_subjects s
        WHERE s.is_active = true
        ORDER BY s.display_order ASC, s.name ASC;
        """
    )
    return [dict(row) for row in rows]


async def get_active_topics_for_subject(
    conn: asyncpg.Connection, subject_id: UUID
) -> list[dict[str, Any]]:
    """Retrieve all active topics for a specific subject with document count."""
    rows = await conn.fetch(
        """
        SELECT
            t.id,
            t.subject_id,
            t.name,
            t.slug,
            t.description,
            t.display_order,
            t.is_active,
            t.created_at,
            t.updated_at,
            (
                SELECT COUNT(*)::int
                FROM public.kb_documents d
                WHERE d.topic_id = t.id AND d.is_archived = false AND d.upload_status = 'uploaded'
            ) AS document_count
        FROM public.kb_topics t
        WHERE t.subject_id = $1 AND t.is_active = true
        ORDER BY t.display_order ASC, t.name ASC;
        """,
        subject_id,
    )
    return [dict(row) for row in rows]


async def get_topic_by_id(conn: asyncpg.Connection, topic_id: UUID) -> dict[str, Any] | None:
    """Fetch single topic by ID."""
    row = await conn.fetchrow(
        """
        SELECT id, subject_id, name, slug, description, display_order, is_active, created_at, updated_at
        FROM public.kb_topics
        WHERE id = $1;
        """,
        topic_id,
    )
    return dict(row) if row else None


async def get_subject_by_id(conn: asyncpg.Connection, subject_id: UUID) -> dict[str, Any] | None:
    """Fetch single subject by ID."""
    row = await conn.fetchrow(
        """
        SELECT id, name, slug, description, icon_name, display_order, is_active, created_at, updated_at
        FROM public.kb_subjects
        WHERE id = $1;
        """,
        subject_id,
    )
    return dict(row) if row else None


async def list_documents_in_topic(
    conn: asyncpg.Connection, topic_id: UUID, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """List uploaded and active documents in a topic with pagination."""
    total = await conn.fetchval(
        """
        SELECT COUNT(*)::int
        FROM public.kb_documents d
        WHERE d.topic_id = $1 AND d.is_archived = false AND d.upload_status = 'uploaded';
        """,
        topic_id,
    )

    rows = await conn.fetch(
        """
        SELECT
            d.id,
            d.subject_id,
            d.topic_id,
            d.uploader_id,
            p.display_name AS uploader_display_name,
            d.title,
            d.original_filename,
            d.mime_type,
            d.file_size_bytes,
            d.upload_status,
            d.extraction_status,
            d.total_pages,
            d.processed_pages,
            d.pages_with_text,
            d.created_at,
            d.updated_at
        FROM public.kb_documents d
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        WHERE d.topic_id = $1 AND d.is_archived = false AND d.upload_status = 'uploaded'
        ORDER BY d.created_at DESC
        LIMIT $2 OFFSET $3;
        """,
        topic_id,
        limit,
        offset,
    )
    return [dict(row) for row in rows], total or 0


async def get_raw_document_by_id(
    conn: asyncpg.Connection, document_id: UUID
) -> dict[str, Any] | None:
    """Fetch un-filtered document row for internal service operations."""
    row = await conn.fetchrow(
        """
        SELECT
            d.id,
            d.subject_id,
            d.topic_id,
            d.uploader_id,
            d.uploader_email_snapshot,
            p.display_name AS uploader_display_name,
            d.title,
            d.original_filename,
            d.storage_path,
            d.mime_type,
            d.file_size_bytes,
            d.sha256_checksum,
            d.upload_status,
            d.extraction_status,
            d.total_pages,
            d.processed_pages,
            d.pages_with_text,
            d.error_code,
            d.error_message,
            d.upload_expires_at,
            d.is_archived,
            d.created_at,
            d.updated_at
        FROM public.kb_documents d
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        WHERE d.id = $1;
        """,
        document_id,
    )
    return dict(row) if row else None


async def create_pending_document(
    conn: asyncpg.Connection,
    document_id: UUID,
    subject_id: UUID,
    topic_id: UUID,
    uploader_id: UUID,
    uploader_email_snapshot: str | None,
    title: str,
    original_filename: str,
    storage_path: str,
    mime_type: str,
    file_size_bytes: int,
    sha256_checksum: str | None,
    upload_expires_at: datetime,
) -> dict[str, Any]:
    """Insert a pending_upload reservation document record."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.kb_documents (
            id,
            subject_id,
            topic_id,
            uploader_id,
            uploader_email_snapshot,
            title,
            original_filename,
            storage_path,
            mime_type,
            file_size_bytes,
            sha256_checksum,
            upload_status,
            extraction_status,
            upload_expires_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'pending_upload', 'pending', $12
        ) RETURNING *;
        """,
        document_id,
        subject_id,
        topic_id,
        uploader_id,
        uploader_email_snapshot,
        title,
        original_filename,
        storage_path,
        mime_type,
        file_size_bytes,
        sha256_checksum,
        upload_expires_at,
    )
    if not row:
        raise RuntimeError("Failed to insert pending document record.")
    return dict(row)


async def confirm_uploaded_document(
    conn: asyncpg.Connection,
    document_id: UUID,
    actual_size: int,
    actual_sha256: str,
) -> dict[str, Any] | None:
    """Atomically mark document uploaded, queued for extraction, and enqueue extraction job."""
    # Run in transaction: update doc and insert job
    updated_doc = await conn.fetchrow(
        """
        UPDATE public.kb_documents
        SET upload_status = 'uploaded',
            extraction_status = 'queued',
            file_size_bytes = $2,
            sha256_checksum = $3,
            error_code = NULL,
            error_message = NULL,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1 AND upload_status = 'pending_upload' AND is_archived = false
        RETURNING *;
        """,
        document_id,
        actual_size,
        actual_sha256,
    )
    if not updated_doc:
        return None

    # Insert extraction job
    await conn.execute(
        """
        INSERT INTO public.kb_extraction_jobs (
            document_id,
            status,
            attempts,
            max_attempts,
            scheduled_at
        ) VALUES (
            $1, 'queued', 0, 3, timezone('utc'::text, now())
        );
        """,
        document_id,
    )

    return dict(updated_doc)


async def mark_upload_failed(
    conn: asyncpg.Connection,
    document_id: UUID,
    error_code: str,
    error_message: str,
) -> None:
    """Mark a document upload as failed with error details."""
    await conn.execute(
        """
        UPDATE public.kb_documents
        SET upload_status = 'upload_failed',
            error_code = $2,
            error_message = $3,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1;
        """,
        document_id,
        error_code,
        error_message,
    )


async def get_document_pages(
    conn: asyncpg.Connection, document_id: UUID, limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve paginated extracted pages for a document."""
    total = await conn.fetchval(
        """
        SELECT COUNT(*)::int
        FROM public.kb_document_pages
        WHERE document_id = $1;
        """,
        document_id,
    )

    rows = await conn.fetch(
        """
        SELECT
            id,
            document_id,
            page_number,
            raw_text,
            character_count,
            word_count,
            has_text,
            created_at
        FROM public.kb_document_pages
        WHERE document_id = $1
        ORDER BY page_number ASC
        LIMIT $2 OFFSET $3;
        """,
        document_id,
        limit,
        offset,
    )
    return [dict(row) for row in rows], total or 0


async def transition_document_to_deleting(
    conn: asyncpg.Connection,
    document_id: UUID,
    user_id: UUID | None,
    is_admin: bool,
) -> dict[str, Any] | None:
    """Transition a document to deleting state if owned by user or if caller is admin.

    Also cancels any active extraction jobs and clears lease tokens to fence stale workers.
    """
    row = await conn.fetchrow(
        """
        UPDATE public.kb_documents
        SET upload_status = 'deleting',
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
          AND is_archived = false
          AND upload_status != 'deleted'
          AND ($2::boolean OR uploader_id = $3)
        RETURNING id, storage_path, uploader_id, title;
        """,
        document_id,
        is_admin,
        user_id,
    )
    if not row:
        return None

    # Cancel active extraction jobs and invalidate lease tokens (fencing protection)
    await conn.execute(
        """
        UPDATE public.kb_extraction_jobs
        SET status = 'failed',
            error_code = 'DOCUMENT_DELETED',
            error_message = 'Document was deleted before extraction completed.',
            lease_token = NULL,
            updated_at = timezone('utc'::text, now())
        WHERE document_id = $1 AND status IN ('queued', 'processing');
        """,
        document_id,
    )

    return dict(row)


async def finalize_document_deletion(conn: asyncpg.Connection, document_id: UUID) -> None:
    """Finalize document deletion by purging extracted pages and archiving record."""
    await conn.execute(
        """
        DELETE FROM public.kb_document_pages
        WHERE document_id = $1;
        """,
        document_id,
    )
    await conn.execute(
        """
        UPDATE public.kb_documents
        SET upload_status = 'deleted',
            is_archived = true,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1;
        """,
        document_id,
    )


async def sweep_abandoned_pending_uploads(
    conn: asyncpg.Connection, batch_size: int = 50
) -> list[dict[str, Any]]:
    """Find expired pending uploads, transition to upload_failed, and return paths for storage cleanup."""
    rows = await conn.fetch(
        """
        WITH expired AS (
            SELECT id, storage_path
            FROM public.kb_documents
            WHERE upload_status = 'pending_upload'
              AND upload_expires_at < timezone('utc'::text, now())
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE public.kb_documents d
        SET upload_status = 'upload_failed',
            error_code = 'RESERVATION_EXPIRED',
            error_message = 'Upload reservation expired without confirmation.',
            updated_at = timezone('utc'::text, now())
        FROM expired
        WHERE d.id = expired.id
        RETURNING d.id, d.storage_path;
        """,
        batch_size,
    )
    return [dict(row) for row in rows]


async def sweep_stale_deleting_documents(
    conn: asyncpg.Connection, stale_threshold_seconds: int = 600, batch_size: int = 50
) -> list[dict[str, Any]]:
    """Find documents stuck in 'deleting' for longer than threshold to finalize or retry storage purge."""
    rows = await conn.fetch(
        """
        SELECT id, storage_path, uploader_id, title
        FROM public.kb_documents
        WHERE upload_status = 'deleting'
          AND is_archived = false
          AND updated_at < timezone('utc'::text, now()) - ($1 || ' seconds')::interval
        LIMIT $2
        FOR UPDATE SKIP LOCKED;
        """,
        str(stale_threshold_seconds),
        batch_size,
    )
    return [dict(row) for row in rows]
