"""Database repository for Knowledge Base administration, moderation, and job retry."""

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("ragbait.kb.admin_repository")


# ------------------------------------------------------------------------------
# Overview & Metrics
# ------------------------------------------------------------------------------


async def get_admin_overview_metrics(conn: asyncpg.Connection) -> dict[str, Any]:
    """Calculate aggregate counts across subjects, topics, documents, and extraction jobs."""
    stats = await conn.fetchrow(
        """
        SELECT
            (SELECT count(*)::int FROM public.kb_subjects WHERE is_active = true) AS active_subjects,
            (SELECT count(*)::int FROM public.kb_subjects) AS total_subjects,
            (SELECT count(*)::int FROM public.kb_topics WHERE is_active = true) AS active_topics,
            (SELECT count(*)::int FROM public.kb_topics) AS total_topics,
            (SELECT count(*)::int FROM public.kb_documents WHERE upload_status != 'deleted') AS total_documents,
            (SELECT count(*)::int FROM public.kb_documents WHERE is_archived = true AND upload_status != 'deleted') AS archived_documents,
            (SELECT count(*)::int FROM public.kb_extraction_jobs WHERE status = 'queued') AS queued_jobs,
            (SELECT count(*)::int FROM public.kb_extraction_jobs WHERE status = 'processing') AS processing_jobs,
            (SELECT count(*)::int FROM public.kb_extraction_jobs WHERE status = 'failed') AS failed_jobs,
            (SELECT count(*)::int FROM public.kb_extraction_jobs WHERE status = 'completed') AS completed_jobs
        """
    )
    return dict(stats) if stats else {}


# ------------------------------------------------------------------------------
# Subject Taxonomy Repository
# ------------------------------------------------------------------------------


async def list_admin_subjects(
    conn: asyncpg.Connection,
    include_inactive: bool = True,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """List all subjects with topic and document counts for administrators."""
    query = """
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
            COALESCE(t.topic_count, 0)::int AS topic_count,
            COALESCE(d.doc_count, 0)::int AS document_count
        FROM public.kb_subjects s
        LEFT JOIN (
            SELECT subject_id, count(*)::int AS topic_count
            FROM public.kb_topics
            GROUP BY subject_id
        ) t ON t.subject_id = s.id
        LEFT JOIN (
            SELECT subject_id, count(*)::int AS doc_count
            FROM public.kb_documents
            WHERE upload_status != 'deleted'
            GROUP BY subject_id
        ) d ON d.subject_id = s.id
        WHERE ($1::boolean IS TRUE OR s.is_active = true)
          AND ($2::text IS NULL OR s.name ILIKE '%' || $2 || '%' OR s.slug ILIKE '%' || $2 || '%')
        ORDER BY s.display_order ASC, s.name ASC
    """
    rows = await conn.fetch(query, include_inactive, search)
    return [dict(r) for r in rows]


async def get_admin_subject_by_id(
    conn: asyncpg.Connection, subject_id: UUID
) -> dict[str, Any] | None:
    """Fetch a single subject by ID with child counts."""
    query = """
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
            COALESCE(t.topic_count, 0)::int AS topic_count,
            COALESCE(d.doc_count, 0)::int AS document_count
        FROM public.kb_subjects s
        LEFT JOIN (
            SELECT subject_id, count(*)::int AS topic_count
            FROM public.kb_topics
            GROUP BY subject_id
        ) t ON t.subject_id = s.id
        LEFT JOIN (
            SELECT subject_id, count(*)::int AS doc_count
            FROM public.kb_documents
            WHERE upload_status != 'deleted'
            GROUP BY subject_id
        ) d ON d.subject_id = s.id
        WHERE s.id = $1
    """
    row = await conn.fetchrow(query, subject_id)
    return dict(row) if row else None


async def get_subject_by_slug(conn: asyncpg.Connection, slug: str) -> dict[str, Any] | None:
    """Check existence of a subject by slug."""
    row = await conn.fetchrow(
        "SELECT id, name, slug, is_active FROM public.kb_subjects WHERE slug = $1", slug
    )
    return dict(row) if row else None


async def get_subject_by_name(conn: asyncpg.Connection, name: str) -> dict[str, Any] | None:
    """Check existence of a subject by exact name."""
    row = await conn.fetchrow(
        "SELECT id, name, slug FROM public.kb_subjects WHERE lower(name) = lower($1)", name
    )
    return dict(row) if row else None


async def create_subject(
    conn: asyncpg.Connection,
    subject_id: UUID,
    name: str,
    slug: str,
    description: str | None,
    icon_name: str,
    display_order: int,
    created_by: UUID,
) -> dict[str, Any]:
    """Insert a new subject."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.kb_subjects (id, name, slug, description, icon_name, display_order, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, name, slug, description, icon_name, display_order, is_active, created_at, updated_at
        """,
        subject_id,
        name,
        slug,
        description,
        icon_name,
        display_order,
        created_by,
    )
    return dict(row)


async def update_subject(
    conn: asyncpg.Connection,
    subject_id: UUID,
    name: str | None,
    slug: str | None,
    description: str | None,
    icon_name: str | None,
    display_order: int | None,
) -> dict[str, Any] | None:
    """Update subject fields selectively."""
    row = await conn.fetchrow(
        """
        UPDATE public.kb_subjects
        SET name = COALESCE($2, name),
            slug = COALESCE($3, slug),
            description = COALESCE($4, description),
            icon_name = COALESCE($5, icon_name),
            display_order = COALESCE($6, display_order),
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
        RETURNING id, name, slug, description, icon_name, display_order, is_active, created_at, updated_at
        """,
        subject_id,
        name,
        slug,
        description,
        icon_name,
        display_order,
    )
    return dict(row) if row else None


async def set_subject_active_status(
    conn: asyncpg.Connection, subject_id: UUID, is_active: bool
) -> dict[str, Any] | None:
    """Set subject active status (archive or restore)."""
    row = await conn.fetchrow(
        """
        UPDATE public.kb_subjects
        SET is_active = $2,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
        RETURNING id, name, slug, description, icon_name, display_order, is_active, created_at, updated_at
        """,
        subject_id,
        is_active,
    )
    return dict(row) if row else None


# ------------------------------------------------------------------------------
# Topic Taxonomy Repository
# ------------------------------------------------------------------------------


async def list_admin_topics(
    conn: asyncpg.Connection,
    subject_id: UUID,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    """List topics for a subject with document counts."""
    query = """
        SELECT
            t.id,
            t.subject_id,
            s.name AS subject_name,
            s.slug AS subject_slug,
            t.name,
            t.slug,
            t.description,
            t.display_order,
            t.is_active,
            t.created_at,
            t.updated_at,
            COALESCE(d.doc_count, 0)::int AS document_count
        FROM public.kb_topics t
        JOIN public.kb_subjects s ON s.id = t.subject_id
        LEFT JOIN (
            SELECT topic_id, count(*)::int AS doc_count
            FROM public.kb_documents
            WHERE upload_status != 'deleted'
            GROUP BY topic_id
        ) d ON d.topic_id = t.id
        WHERE t.subject_id = $1
          AND ($2::boolean IS TRUE OR t.is_active = true)
        ORDER BY t.display_order ASC, t.name ASC
    """
    rows = await conn.fetch(query, subject_id, include_inactive)
    return [dict(r) for r in rows]


async def get_admin_topic_by_id(conn: asyncpg.Connection, topic_id: UUID) -> dict[str, Any] | None:
    """Fetch a topic by ID with parent subject metadata."""
    query = """
        SELECT
            t.id,
            t.subject_id,
            s.name AS subject_name,
            s.slug AS subject_slug,
            t.name,
            t.slug,
            t.description,
            t.display_order,
            t.is_active,
            t.created_at,
            t.updated_at,
            (SELECT count(*)::int FROM public.kb_documents d WHERE d.topic_id = t.id AND d.upload_status != 'deleted') AS document_count
        FROM public.kb_topics t
        JOIN public.kb_subjects s ON s.id = t.subject_id
        WHERE t.id = $1
    """
    row = await conn.fetchrow(query, topic_id)
    return dict(row) if row else None


async def get_topic_by_subject_and_slug(
    conn: asyncpg.Connection, subject_id: UUID, slug: str
) -> dict[str, Any] | None:
    """Check existence of topic slug within a subject."""
    row = await conn.fetchrow(
        "SELECT id, subject_id, name, slug FROM public.kb_topics WHERE subject_id = $1 AND slug = $2",
        subject_id,
        slug,
    )
    return dict(row) if row else None


async def create_topic(
    conn: asyncpg.Connection,
    topic_id: UUID,
    subject_id: UUID,
    name: str,
    slug: str,
    description: str | None,
    display_order: int,
    created_by: UUID,
) -> dict[str, Any]:
    """Insert a new topic."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.kb_topics (id, subject_id, name, slug, description, display_order, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, subject_id, name, slug, description, display_order, is_active, created_at, updated_at
        """,
        topic_id,
        subject_id,
        name,
        slug,
        description,
        display_order,
        created_by,
    )
    return dict(row)


async def update_topic(
    conn: asyncpg.Connection,
    topic_id: UUID,
    name: str | None,
    slug: str | None,
    description: str | None,
    display_order: int | None,
) -> dict[str, Any] | None:
    """Update topic fields selectively."""
    row = await conn.fetchrow(
        """
        UPDATE public.kb_topics
        SET name = COALESCE($2, name),
            slug = COALESCE($3, slug),
            description = COALESCE($4, description),
            display_order = COALESCE($5, display_order),
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
        RETURNING id, subject_id, name, slug, description, display_order, is_active, created_at, updated_at
        """,
        topic_id,
        name,
        slug,
        description,
        display_order,
    )
    return dict(row) if row else None


async def set_topic_active_status(
    conn: asyncpg.Connection, topic_id: UUID, is_active: bool
) -> dict[str, Any] | None:
    """Set topic active status (archive or restore)."""
    row = await conn.fetchrow(
        """
        UPDATE public.kb_topics
        SET is_active = $2,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
        RETURNING id, subject_id, name, slug, description, display_order, is_active, created_at, updated_at
        """,
        topic_id,
        is_active,
    )
    return dict(row) if row else None


# ------------------------------------------------------------------------------
# Document Moderation Repository
# ------------------------------------------------------------------------------


async def list_admin_documents(
    conn: asyncpg.Connection,
    limit: int = 20,
    offset: int = 0,
    subject_id: UUID | None = None,
    topic_id: UUID | None = None,
    uploader_id: UUID | None = None,
    upload_status: str | None = None,
    extraction_status: str | None = None,
    is_archived: bool | None = None,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """List documents with global administrative filters and total count."""
    base_where = "WHERE d.upload_status != 'deleted'"
    params: list[Any] = []
    param_idx = 1

    if subject_id:
        base_where += f" AND d.subject_id = ${param_idx}"
        params.append(subject_id)
        param_idx += 1

    if topic_id:
        base_where += f" AND d.topic_id = ${param_idx}"
        params.append(topic_id)
        param_idx += 1

    if uploader_id:
        base_where += f" AND d.uploader_id = ${param_idx}"
        params.append(uploader_id)
        param_idx += 1

    if upload_status:
        base_where += f" AND d.upload_status = ${param_idx}"
        params.append(upload_status)
        param_idx += 1

    if extraction_status:
        base_where += f" AND d.extraction_status = ${param_idx}"
        params.append(extraction_status)
        param_idx += 1

    if is_archived is not None:
        base_where += f" AND d.is_archived = ${param_idx}"
        params.append(is_archived)
        param_idx += 1

    if search:
        base_where += (
            f" AND (d.title ILIKE '%' || ${param_idx} || '%' "
            f"OR d.original_filename ILIKE '%' || ${param_idx} || '%' "
            f"OR p.display_name ILIKE '%' || ${param_idx} || '%' "
            f"OR p.email ILIKE '%' || ${param_idx} || '%')"
        )
        params.append(search)
        param_idx += 1

    count_query = f"""
        SELECT count(*)::int
        FROM public.kb_documents d
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        {base_where}
    """
    total = await conn.fetchval(count_query, *params) or 0

    query = f"""
        SELECT
            d.id,
            d.subject_id,
            s.name AS subject_name,
            d.topic_id,
            t.name AS topic_name,
            d.uploader_id,
            p.display_name AS uploader_display_name,
            p.email AS uploader_email,
            d.title,
            d.original_filename,
            d.mime_type,
            d.file_size_bytes,
            d.upload_status,
            d.extraction_status,
            d.total_pages,
            d.processed_pages,
            d.pages_with_text,
            d.is_archived,
            d.created_at,
            d.updated_at
        FROM public.kb_documents d
        JOIN public.kb_subjects s ON s.id = d.subject_id
        JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        {base_where}
        ORDER BY d.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows], total


async def get_admin_document_detail(
    conn: asyncpg.Connection, document_id: UUID
) -> dict[str, Any] | None:
    """Fetch complete document detail for moderation inspection."""
    query = """
        SELECT
            d.id,
            d.subject_id,
            s.name AS subject_name,
            d.topic_id,
            t.name AS topic_name,
            d.uploader_id,
            p.display_name AS uploader_display_name,
            p.email AS uploader_email,
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
        JOIN public.kb_subjects s ON s.id = d.subject_id
        JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        WHERE d.id = $1
    """
    row = await conn.fetchrow(query, document_id)
    return dict(row) if row else None


async def set_document_archived_status(
    conn: asyncpg.Connection, document_id: UUID, is_archived: bool
) -> dict[str, Any] | None:
    """Set document archived status."""
    row = await conn.fetchrow(
        """
        UPDATE public.kb_documents
        SET is_archived = $2,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
        RETURNING id, title, storage_path, is_archived, upload_status, extraction_status
        """,
        document_id,
        is_archived,
    )
    return dict(row) if row else None


# ------------------------------------------------------------------------------
# Extraction Job Monitoring & Retry Repository
# ------------------------------------------------------------------------------


async def list_admin_jobs(
    conn: asyncpg.Connection,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    subject_id: UUID | None = None,
    topic_id: UUID | None = None,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """List extraction jobs with filters and pagination."""
    base_where = "WHERE 1=1"
    params: list[Any] = []
    param_idx = 1

    if status:
        base_where += f" AND j.status = ${param_idx}"
        params.append(status)
        param_idx += 1

    if subject_id:
        base_where += f" AND d.subject_id = ${param_idx}"
        params.append(subject_id)
        param_idx += 1

    if topic_id:
        base_where += f" AND d.topic_id = ${param_idx}"
        params.append(topic_id)
        param_idx += 1

    if search:
        base_where += (
            f" AND (d.title ILIKE '%' || ${param_idx} || '%' "
            f"OR j.error_code ILIKE '%' || ${param_idx} || '%')"
        )
        params.append(search)
        param_idx += 1

    count_query = f"""
        SELECT count(*)::int
        FROM public.kb_extraction_jobs j
        JOIN public.kb_documents d ON d.id = j.document_id
        {base_where}
    """
    total = await conn.fetchval(count_query, *params) or 0

    query = f"""
        SELECT
            j.id,
            j.document_id,
            d.title AS document_title,
            d.subject_id,
            s.name AS subject_name,
            d.topic_id,
            t.name AS topic_name,
            p.display_name AS uploader_display_name,
            j.status,
            j.attempts,
            j.max_attempts,
            j.scheduled_at,
            j.locked_until,
            j.error_code,
            j.error_message,
            j.retry_count,
            j.retry_of_job_id,
            j.created_at,
            j.updated_at
        FROM public.kb_extraction_jobs j
        JOIN public.kb_documents d ON d.id = j.document_id
        JOIN public.kb_subjects s ON s.id = d.subject_id
        JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        {base_where}
        ORDER BY j.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows], total


async def get_admin_job_detail(conn: asyncpg.Connection, job_id: UUID) -> dict[str, Any] | None:
    """Fetch complete sanitized job detail for administration."""
    query = """
        SELECT
            j.id,
            j.document_id,
            d.title AS document_title,
            d.subject_id,
            s.name AS subject_name,
            d.topic_id,
            t.name AS topic_name,
            p.display_name AS uploader_display_name,
            d.file_size_bytes,
            d.mime_type,
            d.upload_status AS document_upload_status,
            d.is_archived AS document_is_archived,
            j.status,
            j.attempts,
            j.max_attempts,
            j.scheduled_at,
            j.locked_until,
            j.error_code,
            j.error_message,
            j.retry_count,
            j.retry_of_job_id,
            j.created_at,
            j.updated_at
        FROM public.kb_extraction_jobs j
        JOIN public.kb_documents d ON d.id = j.document_id
        JOIN public.kb_subjects s ON s.id = d.subject_id
        JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.profiles p ON p.id = d.uploader_id
        WHERE j.id = $1
    """
    row = await conn.fetchrow(query, job_id)
    return dict(row) if row else None


# ------------------------------------------------------------------------------
# Audit Activity Repository
# ------------------------------------------------------------------------------


async def list_kb_audit_logs(
    conn: asyncpg.Connection,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    target_type: str | None = None,
    actor_id: UUID | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve filtered audit history for Knowledge Base entities."""
    base_where = (
        "WHERE a.target_type IN ('kb_subject', 'kb_topic', 'kb_document', 'kb_extraction_job')"
    )
    params: list[Any] = []
    param_idx = 1

    if action:
        base_where += f" AND a.action = ${param_idx}"
        params.append(action)
        param_idx += 1

    if target_type:
        base_where += f" AND a.target_type = ${param_idx}"
        params.append(target_type)
        param_idx += 1

    if actor_id:
        base_where += f" AND a.actor_id = ${param_idx}"
        params.append(actor_id)
        param_idx += 1

    count_query = f"SELECT count(*)::int FROM public.audit_logs a {base_where}"
    total = await conn.fetchval(count_query, *params) or 0

    query = f"""
        SELECT
            a.id,
            a.actor_id,
            p.display_name AS actor_display_name,
            p.email AS actor_email,
            a.action,
            a.target_type,
            a.target_id,
            a.payload,
            a.created_at
        FROM public.audit_logs a
        LEFT JOIN public.profiles p ON p.id = a.actor_id
        {base_where}
        ORDER BY a.created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])
    rows = await conn.fetch(query, *params)

    # Format payload if string
    results = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                d["payload"] = {}
        elif d.get("payload") is None:
            d["payload"] = {}
        results.append(d)

    return results, total


async def insert_admin_audit_log(
    conn: asyncpg.Connection,
    actor_id: UUID,
    action: str,
    target_type: str,
    target_id: UUID,
    payload: dict[str, Any],
) -> None:
    """Insert an immutable audit log entry within the caller's transaction."""
    await conn.execute(
        """
        INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        actor_id,
        action,
        target_type,
        target_id,
        json.dumps(payload),
    )
