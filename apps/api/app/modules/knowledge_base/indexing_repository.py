"""Database repository for durable RAG document indexing queue and chunk persistence.

Follows the battle-tested locking and lifecycle patterns established in M3.1:
- Consistent document-first locking (FOR UPDATE OF d, j SKIP LOCKED).
- Lease tokens and worker heartbeats with automatic expiration recovery.
- Atomic chunk insertion and activation within a single database transaction.
- Safe cancellation on document archival, deletion, or moderation.
"""

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.modules.knowledge_base.chunking import ChunkOutput

logger = logging.getLogger("ragbait.kb.indexing_repository")


async def claim_next_indexing_job(
    conn: asyncpg.Connection,
    worker_id: str,
    lease_ttl_seconds: int = 120,
) -> dict[str, Any] | None:
    """Atomically claim the next eligible indexing job using document-first locking.

    Eligible jobs are:
    1. status = 'queued' AND scheduled_at <= NOW()
       OR status = 'processing' AND locked_until < NOW() (expired worker lease)
    2. Document is uploaded, completed extraction, not archived, and within active topic/subject.
    3. attempts < max_attempts.
    """
    row = await conn.fetchrow(
        """
        WITH eligible_doc AS (
            SELECT
                d.id AS document_id,
                d.title,
                d.extraction_version,
                d.active_chunk_version
            FROM public.kb_documents d
            JOIN public.kb_indexing_jobs j ON j.document_id = d.id
            JOIN public.kb_topics t ON t.id = d.topic_id
            JOIN public.kb_subjects s ON s.id = t.subject_id
            WHERE (
                (j.status = 'queued' AND j.scheduled_at <= timezone('utc'::text, now()))
                OR (j.status = 'processing' AND j.locked_until < timezone('utc'::text, now()))
            )
            AND d.upload_status = 'uploaded'
            AND d.extraction_status = 'completed'
            AND d.is_archived = false
            AND t.is_active = true
            AND s.is_active = true
            AND j.attempts < j.max_attempts
            ORDER BY j.scheduled_at ASC
            LIMIT 1
            FOR UPDATE OF d, j SKIP LOCKED
        ),
        updated_doc AS (
            UPDATE public.kb_documents d
            SET indexing_status = 'indexing',
                updated_at = timezone('utc'::text, now())
            FROM eligible_doc ed
            WHERE d.id = ed.document_id
            RETURNING d.id
        )
        UPDATE public.kb_indexing_jobs j
        SET status = 'processing',
            worker_id = $1,
            lease_token = gen_random_uuid(),
            locked_until = timezone('utc'::text, now()) + ($2 || ' seconds')::interval,
            heartbeat_at = timezone('utc'::text, now()),
            attempts = j.attempts + 1,
            updated_at = timezone('utc'::text, now())
        FROM eligible_doc ed
        JOIN updated_doc ud ON ud.id = ed.document_id
        WHERE j.document_id = ed.document_id
          AND (
              (j.status = 'queued' AND j.scheduled_at <= timezone('utc'::text, now()))
              OR (j.status = 'processing' AND j.locked_until < timezone('utc'::text, now()))
          )
          AND j.attempts < j.max_attempts
        RETURNING j.id AS job_id, j.document_id, j.worker_id, j.lease_token, j.attempts,
                  j.max_attempts, j.locked_until, j.source_version, j.chunking_version,
                  ed.title, ed.extraction_version, ed.active_chunk_version;
        """,
        worker_id,
        str(lease_ttl_seconds),
    )

    if not row:
        return None

    return dict(row)


async def renew_indexing_lease(
    conn: asyncpg.Connection,
    job_id: UUID,
    worker_id: str,
    lease_token: UUID,
    lease_ttl_seconds: int = 120,
) -> bool:
    """Renew worker lease and heartbeat timestamp for an active indexing job."""
    result = await conn.execute(
        """
        UPDATE public.kb_indexing_jobs
        SET locked_until = timezone('utc'::text, now()) + ($4 || ' seconds')::interval,
            heartbeat_at = timezone('utc'::text, now()),
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
          AND worker_id = $2
          AND lease_token = $3
          AND status = 'processing';
        """,
        job_id,
        worker_id,
        lease_token,
        str(lease_ttl_seconds),
    )
    return result == "UPDATE 1"


async def fetch_document_pages_for_indexing(
    conn: asyncpg.Connection,
    document_id: UUID,
) -> list[dict[str, Any]]:
    """Fetch extracted pages for a document ordered sequentially."""
    rows = await conn.fetch(
        """
        SELECT page_number, raw_text, has_text, character_count
        FROM public.kb_document_pages
        WHERE document_id = $1
        ORDER BY page_number ASC;
        """,
        document_id,
    )
    return [dict(r) for r in rows]


async def complete_indexing_job_transaction(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    chunks: list[ChunkOutput],
    chunking_version: int = 1,
    source_version: int = 1,
) -> bool:
    """Atomically persist generated chunks, update document indexing_status, and complete job."""
    async with conn.transaction():
        # 1. Lock document first to maintain strict lock ordering
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.upload_status, d.is_archived, d.extraction_status,
                   t.is_active AS topic_active, s.is_active AS subject_active
            FROM public.kb_documents d
            JOIN public.kb_topics t ON t.id = d.topic_id
            JOIN public.kb_subjects s ON s.id = t.subject_id
            WHERE d.id = $1
            FOR UPDATE OF d;
            """,
            document_id,
        )
        if (
            not doc
            or doc["upload_status"] != "uploaded"
            or doc["extraction_status"] != "completed"
            or doc["is_archived"]
            or not doc["topic_active"]
            or not doc["subject_active"]
        ):
            logger.warning(
                f"Document {document_id} was archived, deleted, or taxonomy deactivated during indexing. Aborting."
            )
            return False

        # 2. Lock job and verify worker lease token
        job = await conn.fetchrow(
            """
            SELECT id
            FROM public.kb_indexing_jobs
            WHERE id = $1
              AND lease_token = $2
              AND worker_id = $3
              AND status = 'processing'
              AND locked_until > timezone('utc'::text, now())
            FOR UPDATE;
            """,
            job_id,
            lease_token,
            worker_id,
        )
        if not job:
            logger.warning(f"Worker {worker_id} lost lease on indexing job {job_id}. Aborting completion.")
            return False

        # 3. Clean up any previous chunks for this version (or prior runs)
        await conn.execute(
            """
            DELETE FROM public.kb_document_chunks
            WHERE document_id = $1 AND chunking_version = $2;
            """,
            document_id,
            chunking_version,
        )

        # 4. Deactivate all existing active chunks for this document
        await conn.execute(
            """
            UPDATE public.kb_document_chunks
            SET is_active = false
            WHERE document_id = $1;
            """,
            document_id,
        )

        total_chunks = len(chunks)
        total_tokens = sum(c.token_count for c in chunks)

        # 5. Insert newly created chunks with is_active = true
        if chunks:
            records = [
                (
                    document_id,
                    c.chunk_index,
                    c.page_number_start,
                    c.page_number_end,
                    c.raw_text,
                    c.character_count,
                    c.token_count,
                    c.content_hash,
                    chunking_version,
                    source_version,
                    job_id,
                    True,  # is_active
                    json.dumps(c.page_spans),
                )
                for c in chunks
            ]
            await conn.executemany(
                """
                INSERT INTO public.kb_document_chunks (
                    document_id,
                    chunk_index,
                    page_number_start,
                    page_number_end,
                    raw_text,
                    character_count,
                    token_count,
                    content_hash,
                    chunking_version,
                    source_version,
                    indexing_job_id,
                    is_active,
                    page_spans
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb);
                """,
                records,
            )

        # 6. Update document indexing status
        final_indexing_status = "indexed" if chunks else "not_indexable"
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET indexing_status = $2,
                active_chunk_version = $3,
                embedding_status = CASE WHEN $2 = 'indexed' THEN 'unembedded' ELSE 'not_applicable' END,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
            final_indexing_status,
            chunking_version if chunks else None,
        )

        if chunks:
            # Enqueue durable embedding job for this document and active chunk version
            await conn.execute(
                """
                INSERT INTO public.kb_embedding_jobs (
                    document_id,
                    chunk_version,
                    model_fingerprint,
                    status,
                    scheduled_at
                ) VALUES ($1, $2, 'e5_small_int8_761b726d_f80102d3', 'queued', timezone('utc'::text, now()))
                ON CONFLICT (document_id, model_fingerprint) WHERE status IN ('queued', 'processing')
                DO NOTHING;
                """,
                document_id,
                chunking_version,
            )

        # 7. Update indexing job to completed
        await conn.execute(
            """
            UPDATE public.kb_indexing_jobs
            SET status = 'completed',
                lease_token = NULL,
                locked_until = NULL,
                total_chunks = $2,
                total_tokens = $3,
                completed_at = timezone('utc'::text, now()),
                error_code = NULL,
                error_message = NULL,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            total_chunks,
            total_tokens,
        )

        return True


async def fail_indexing_job(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    error_code: str,
    error_message: str,
    is_transient: bool = True,
) -> bool:
    """Record indexing job failure with exponential backoff or permanent failure."""
    async with conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT id, attempts, max_attempts
            FROM public.kb_indexing_jobs
            WHERE id = $1
              AND lease_token = $2
              AND worker_id = $3
              AND status = 'processing'
            FOR UPDATE;
            """,
            job_id,
            lease_token,
            worker_id,
        )
        if not job:
            return False

        attempts = job["attempts"]
        max_attempts = job["max_attempts"]

        if is_transient and attempts < max_attempts:
            # Exponential backoff: 10s, 20s, 40s...
            delay_seconds = 10 * (2 ** (attempts - 1))
            await conn.execute(
                """
                UPDATE public.kb_indexing_jobs
                SET status = 'queued',
                    worker_id = NULL,
                    lease_token = NULL,
                    locked_until = NULL,
                    scheduled_at = timezone('utc'::text, now()) + ($2 || ' seconds')::interval,
                    error_code = $3,
                    error_message = $4,
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                job_id,
                str(delay_seconds),
                error_code,
                error_message,
            )
            await conn.execute(
                """
                UPDATE public.kb_documents
                SET indexing_status = 'unindexed',
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                document_id,
            )
        else:
            # Permanent failure
            await conn.execute(
                """
                UPDATE public.kb_indexing_jobs
                SET status = 'failed',
                    worker_id = NULL,
                    lease_token = NULL,
                    locked_until = NULL,
                    completed_at = timezone('utc'::text, now()),
                    error_code = $2,
                    error_message = $3,
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                job_id,
                error_code,
                error_message,
            )
            await conn.execute(
                """
                UPDATE public.kb_documents
                SET indexing_status = 'failed',
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                document_id,
            )

        return True


async def enqueue_indexing_job(
    conn: asyncpg.Connection,
    document_id: UUID,
    source_version: int = 1,
    chunking_version: int = 1,
) -> UUID | None:
    """Enqueue an indexing job for a document if no active job exists."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.kb_indexing_jobs (
            document_id,
            source_version,
            chunking_version,
            status,
            scheduled_at
        )
        VALUES ($1, $2, $3, 'queued', timezone('utc'::text, now()))
        ON CONFLICT (document_id) WHERE status IN ('queued', 'processing')
        DO NOTHING
        RETURNING id;
        """,
        document_id,
        source_version,
        chunking_version,
    )
    return row["id"] if row else None


async def cancel_indexing_jobs_for_document(
    conn: asyncpg.Connection,
    document_id: UUID,
    reason: str = "document_archived_or_deleted",
) -> int:
    """Cancel any active indexing jobs for a document and reset document indexing status."""
    res = await conn.execute(
        """
        UPDATE public.kb_indexing_jobs
        SET status = 'cancelled',
            lease_token = NULL,
            locked_until = NULL,
            error_code = 'CANCELLED',
            error_message = $2,
            completed_at = timezone('utc'::text, now()),
            updated_at = timezone('utc'::text, now())
        WHERE document_id = $1
          AND status IN ('queued', 'processing');
        """,
        document_id,
        reason,
    )
    # Extracts count from "UPDATE N"
    count = int(res.split(" ")[1]) if " " in res else 0

    await conn.execute(
        """
        UPDATE public.kb_documents
        SET indexing_status = 'unindexed',
            updated_at = timezone('utc'::text, now())
        WHERE id = $1;
        """,
        document_id,
    )
    return count
