"""Repository for immutable evidence snapshots and live KB access revalidation."""

import json
from uuid import UUID

import asyncpg

from app.core.errors import AuthorizationError, NotFoundError
from app.modules.chat.citations.schemas import (
    UNAVAILABLE_SOURCE_PLACEHOLDER,
    MessageSourceRead,
)
from app.modules.chat.context.schemas import SelectedEvidenceSource, SelectedWebSource


async def insert_included_source_snapshots(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    selected_evidence: list[SelectedEvidenceSource],
    referenced_keys: set[str] | None = None,
) -> int:
    """Persist immutable Stage-3/Stage-4 source snapshots for a chat assistant message."""
    if not selected_evidence:
        return 0

    ref_set = referenced_keys or set()
    records = [
        (
            message_id,
            ev.citation_key,
            ev.chunk_id,
            ev.document_id,
            ev.chunk_id,
            ev.document_id,
            ev.document_title,
            ev.page_number_start,
            ev.page_number_end,
            json.dumps(ev.page_spans),
            ev.raw_text,
            ev.content_hash,
            ev.chunk_version,
            ev.retrieval_rank,
            float(ev.rrf_score),
            True,  # included_in_context
            ev.citation_key in ref_set,  # referenced_in_answer
        )
        for ev in selected_evidence
    ]

    await conn.executemany(
        """
        INSERT INTO public.chat_message_sources (
            message_id,
            citation_key,
            chunk_id,
            document_id,
            source_chunk_id,
            source_document_id,
            document_title_snapshot,
            page_number_start,
            page_number_end,
            page_spans,
            excerpt,
            content_hash,
            chunk_version,
            retrieval_rank,
            relevance_score,
            included_in_context,
            referenced_in_answer
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13, $14, $15, $16, $17)
        """,
        records,
    )
    return len(records)


async def load_server_included_keys(
    conn: asyncpg.Connection,
    message_id: UUID,
) -> list[dict[str, object]]:
    """Load Stage-3 server-recorded citation keys and live visibility state for finalization."""
    rows = await conn.fetch(
        """
        SELECT
            s.id,
            s.citation_key,
            s.source_chunk_id,
            s.source_document_id,
            s.content_hash,
            (
                d.id IS NOT NULL
                AND d.is_archived = false
                AND d.upload_status = 'uploaded'
                AND d.extraction_status IN ('completed', 'completed_no_text')
                AND t.id IS NOT NULL
                AND t.is_active = true
                AND subj.id IS NOT NULL
                AND subj.is_active = true
            ) AS is_currently_available
        FROM public.chat_message_sources s
        LEFT JOIN public.kb_documents d ON d.id = s.document_id
        LEFT JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.kb_subjects subj ON subj.id = d.subject_id
        WHERE s.message_id = $1
          AND s.included_in_context = true
        ORDER BY substring(s.citation_key FROM 2)::integer ASC;
        """,
        message_id,
    )
    return [dict(r) for r in rows]


async def mark_referenced_citations(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    referenced_keys: list[str],
) -> int:
    """Update `referenced_in_answer = true` strictly via controlled backend transition."""
    if not referenced_keys:
        return 0

    unique_keys = list(dict.fromkeys(referenced_keys))
    rows = await conn.fetch(
        """
        SELECT citation_key, included_in_context
        FROM public.chat_message_sources
        WHERE message_id = $1
          AND citation_key = ANY($2::text[]);
        """,
        message_id,
        unique_keys,
    )
    included_map = {str(r["citation_key"]): bool(r["included_in_context"]) for r in rows}
    invalid = [k for k in unique_keys if not included_map.get(k, False)]
    if invalid:
        raise ValueError(
            f"Cannot mark unincluded or unknown citations as referenced_in_answer: {invalid}"
        )

    updated = await conn.fetchval(
        "SELECT public.mark_chat_message_sources_referenced($1, $2::text[]);",
        message_id,
        unique_keys,
    )
    return int(updated or 0)


async def get_message_sources_with_revalidation(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    user_id: UUID,
) -> tuple[UUID, list[MessageSourceRead]]:
    """Retrieve historical source snapshots with live KB visibility revalidation & masking.

    Security Guarantees:
    1. Verifies caller profile approval status ('approved').
    2. Verifies caller owns the parent conversation and conversation is not 'deleted'.
    3. Joins live kb_documents, kb_topics, and kb_subjects to confirm document availability.
    4. If archived, soft-deleted, unapproved, or parent taxonomy deactivated:
       - Marks `is_available = False`
       - Replaces excerpt with UNAVAILABLE_SOURCE_PLACEHOLDER
       - Sets `document_viewer_url = None`
    5. Preserves original immutable snapshots cleanly when source remains authorized.
    """
    profile_row = await conn.fetchrow(
        """
        SELECT approval_status
        FROM public.profiles
        WHERE id = $1
        """,
        user_id,
    )
    if not profile_row or profile_row["approval_status"] != "approved":
        raise AuthorizationError(
            "User account is not approved to view conversation sources",
            code="ACCOUNT_NOT_APPROVED",
        )

    msg_row = await conn.fetchrow(
        """
        SELECT m.id, m.conversation_id
        FROM public.chat_messages m
        JOIN public.chat_conversations c ON c.id = m.conversation_id
        WHERE m.id = $1
          AND c.user_id = $2
          AND c.status != 'deleted'
        """,
        message_id,
        user_id,
    )
    if not msg_row:
        raise NotFoundError("Message not found", code="MESSAGE_NOT_FOUND")

    conversation_id: UUID = msg_row["conversation_id"]

    rows = await conn.fetch(
        """
        SELECT
            s.id,
            s.citation_key,
            s.source_document_id AS document_id,
            s.document_title_snapshot AS document_title,
            d.mime_type AS file_type,
            s.page_number_start,
            s.page_number_end,
            s.relevance_score,
            s.included_in_context,
            s.referenced_in_answer,
            (
                d.id IS NOT NULL
                AND d.is_archived = false
                AND d.upload_status = 'uploaded'
                AND d.extraction_status IN ('completed', 'completed_no_text')
                AND t.id IS NOT NULL
                AND t.is_active = true
                AND subj.id IS NOT NULL
                AND subj.is_active = true
            ) AS is_available,
            CASE
                WHEN d.id IS NOT NULL
                 AND d.is_archived = false
                 AND d.upload_status = 'uploaded'
                 AND d.extraction_status IN ('completed', 'completed_no_text')
                 AND t.id IS NOT NULL
                 AND t.is_active = true
                 AND subj.id IS NOT NULL
                 AND subj.is_active = true
                THEN s.excerpt
                ELSE $2::text
            END AS excerpt,
            CASE
                WHEN d.id IS NOT NULL
                 AND d.is_archived = false
                 AND d.upload_status = 'uploaded'
                 AND d.extraction_status IN ('completed', 'completed_no_text')
                 AND t.id IS NOT NULL
                 AND t.is_active = true
                 AND subj.id IS NOT NULL
                 AND subj.is_active = true
                THEN '/knowledge-base/document/' || s.source_document_id::text || '?page=' || s.page_number_start::text
                ELSE NULL
            END AS document_viewer_url
        FROM public.chat_message_sources s
        LEFT JOIN public.kb_documents d ON d.id = s.document_id
        LEFT JOIN public.kb_topics t ON t.id = d.topic_id
        LEFT JOIN public.kb_subjects subj ON subj.id = d.subject_id
        WHERE s.message_id = $1
          AND s.included_in_context = true
        ORDER BY substring(s.citation_key FROM 2)::integer ASC;
        """,
        message_id,
        UNAVAILABLE_SOURCE_PLACEHOLDER,
    )

    sources = [
        MessageSourceRead.model_validate({"source_type": "kb_note", **dict(r)}) for r in rows
    ]

    web_rows = await conn.fetch(
        """
        SELECT
            w.id,
            w.citation_key,
            w.title AS document_title,
            w.excerpt,
            w.relevance_score,
            w.included_in_context,
            w.referenced_in_answer,
            (w.availability_status = 'available') AS is_available,
            w.url,
            w.domain,
            w.publisher,
            w.published_at,
            w.retrieved_at,
            w.snippet_only
        FROM public.chat_message_web_sources w
        WHERE w.message_id = $1
          AND w.included_in_context = true
        ORDER BY substring(w.citation_key FROM 2)::integer ASC;
        """,
        message_id,
    )
    for wr in web_rows:
        sources.append(
            MessageSourceRead.model_validate(
                {
                    "source_type": "web_source",
                    "document_id": None,
                    "page_number_start": None,
                    "page_number_end": None,
                    "document_viewer_url": None,
                    **dict(wr),
                }
            )
        )
    return conversation_id, sources


async def insert_included_web_source_snapshots(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    selected_web_sources: list[SelectedWebSource],
    referenced_keys: set[str] | None = None,
) -> int:
    """Persist immutable external web source snapshots for a chat assistant message."""
    if not selected_web_sources:
        return 0

    ref_set = referenced_keys or set()
    records = [
        (
            message_id,
            w.citation_key,
            w.title,
            w.url,
            w.domain,
            w.publisher,
            w.published_at,
            w.retrieved_at,
            w.excerpt,
            w.content_hash,
            bool(w.snippet_only),
            w.retrieval_rank,
            float(w.relevance_score),
            True,  # included_in_context
            w.citation_key in ref_set,
        )
        for w in selected_web_sources
    ]

    await conn.executemany(
        """
        INSERT INTO public.chat_message_web_sources (
            message_id,
            citation_key,
            title,
            url,
            domain,
            publisher,
            published_at,
            retrieved_at,
            excerpt,
            content_hash,
            snippet_only,
            retrieval_rank,
            relevance_score,
            included_in_context,
            referenced_in_answer
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        records,
    )
    return len(records)


async def load_server_included_web_keys(
    conn: asyncpg.Connection,
    message_id: UUID,
) -> list[str]:
    """Load Stage-3 server-recorded web citation keys ([W1], ...) for finalization."""
    rows = await conn.fetch(
        """
        SELECT citation_key
        FROM public.chat_message_web_sources
        WHERE message_id = $1
          AND included_in_context = true
        ORDER BY substring(citation_key FROM 2)::integer ASC;
        """,
        message_id,
    )
    return [str(r["citation_key"]) for r in rows]


async def mark_referenced_web_citations(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    referenced_keys: list[str],
) -> int:
    """Update `referenced_in_answer = true` on web sources strictly via controlled transition."""
    if not referenced_keys:
        return 0
    unique_keys = list(dict.fromkeys(referenced_keys))
    updated = await conn.fetchval(
        "SELECT public.mark_chat_message_web_sources_referenced($1, $2::text[]);",
        message_id,
        unique_keys,
    )
    return int(updated or 0)
