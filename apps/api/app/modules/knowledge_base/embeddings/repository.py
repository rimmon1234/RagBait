"""Database repository for durable RAG document embedding queue and vector persistence."""

import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("ragbait.kb.embeddings.repository")


async def enqueue_embedding_job(
    conn: asyncpg.Connection,
    document_id: UUID,
    chunk_version: int,
    model_fingerprint: str,
) -> UUID | None:
    """Idempotently enqueue a background embedding job for an active document chunk version."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.kb_embedding_jobs (
            document_id,
            chunk_version,
            model_fingerprint,
            status,
            scheduled_at
        ) VALUES ($1, $2, $3, 'queued', timezone('utc'::text, now()))
        ON CONFLICT (document_id, model_fingerprint) WHERE status IN ('queued', 'processing')
        DO NOTHING
        RETURNING id;
        """,
        document_id,
        chunk_version,
        model_fingerprint,
    )
    return row["id"] if row else None


async def claim_next_embedding_job(
    conn: asyncpg.Connection,
    worker_id: str,
    lease_ttl_seconds: int = 120,
) -> dict[str, Any] | None:
    """Atomically claim the next eligible embedding job using document-first locking."""
    row = await conn.fetchrow(
        """
        WITH eligible_doc AS (
            SELECT
                d.id AS document_id,
                d.title,
                d.active_chunk_version
            FROM public.kb_documents d
            JOIN public.kb_embedding_jobs j ON j.document_id = d.id
            JOIN public.kb_topics t ON t.id = d.topic_id
            JOIN public.kb_subjects s ON s.id = t.subject_id
            WHERE (
                (j.status = 'queued' AND j.scheduled_at <= timezone('utc'::text, now()))
                OR (j.status = 'processing' AND j.locked_until < timezone('utc'::text, now()))
            )
            AND d.upload_status = 'uploaded'
            AND d.indexing_status = 'indexed'
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
            SET embedding_status = 'embedding',
                updated_at = timezone('utc'::text, now())
            FROM eligible_doc ed
            WHERE d.id = ed.document_id
            RETURNING d.id
        )
        UPDATE public.kb_embedding_jobs j
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
        RETURNING j.id AS job_id, j.document_id, j.chunk_version, j.model_fingerprint,
                  j.worker_id, j.lease_token, j.attempts, j.max_attempts, j.locked_until,
                  ed.title, ed.active_chunk_version;
        """,
        worker_id,
        str(lease_ttl_seconds),
    )
    return dict(row) if row else None


async def renew_embedding_lease(
    conn: asyncpg.Connection,
    job_id: UUID,
    worker_id: str,
    lease_token: UUID,
    lease_ttl_seconds: int = 120,
) -> bool:
    """Renew worker lease and heartbeat timestamp for an active embedding job."""
    result = await conn.execute(
        """
        UPDATE public.kb_embedding_jobs
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
    return bool(result == "UPDATE 1")


async def fetch_chunks_for_embedding(
    conn: asyncpg.Connection,
    document_id: UUID,
    chunk_version: int,
) -> list[dict[str, Any]]:
    """Fetch active chunks for a document version that need embedding."""
    rows = await conn.fetch(
        """
        SELECT id, document_id, chunk_index, raw_text, token_count, content_hash
        FROM public.kb_document_chunks
        WHERE document_id = $1
          AND chunking_version = $2
          AND is_active = true
        ORDER BY chunk_index ASC;
        """,
        document_id,
        chunk_version,
    )
    return [dict(r) for r in rows]


async def persist_chunk_embeddings(
    conn: asyncpg.Connection,
    records: list[tuple[UUID, UUID, int, str, str, int, str, str]],
) -> int:
    """Atomically insert or update chunk vector embeddings in public.kb_chunk_embeddings.

    Records schema:
    (chunk_id, document_id, chunk_version, model_id, model_fingerprint, dimensions, vector_str, source_content_hash)
    """
    if not records:
        return 0

    await conn.executemany(
        """
        INSERT INTO public.kb_chunk_embeddings (
            chunk_id,
            document_id,
            chunk_version,
            model_id,
            model_fingerprint,
            dimensions,
            embedding,
            source_content_hash,
            updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7::extensions.vector, $8, timezone('utc'::text, now())
        )
        ON CONFLICT (chunk_id, model_fingerprint)
        DO UPDATE SET
            embedding = EXCLUDED.embedding,
            source_content_hash = EXCLUDED.source_content_hash,
            updated_at = timezone('utc'::text, now());
        """,
        records,
    )
    return len(records)


async def complete_embedding_job_transaction(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    chunk_version: int,
    model_fingerprint: str,
) -> bool:
    """Atomically verify completeness and mark document and job as embedded."""
    async with conn.transaction():
        # 1. Lock document first to maintain strict lock ordering
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.upload_status, d.is_archived, d.indexing_status, d.active_chunk_version,
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
            or doc["indexing_status"] != "indexed"
            or doc["is_archived"]
            or not doc["topic_active"]
            or not doc["subject_active"]
            or doc["active_chunk_version"] != chunk_version
        ):
            logger.warning(
                f"Document {document_id} was modified, archived, or version superseded during embedding. Aborting."
            )
            return False

        # 2. Lock job and verify lease token
        job = await conn.fetchrow(
            """
            SELECT id
            FROM public.kb_embedding_jobs
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
            logger.warning(
                f"Worker {worker_id} lost lease on embedding job {job_id}. Aborting completion."
            )
            return False

        # 3. Verify that ALL expected active chunks have valid embeddings
        total_chunks = await conn.fetchval(
            """
            SELECT count(*)
            FROM public.kb_document_chunks
            WHERE document_id = $1 AND chunking_version = $2 AND is_active = true;
            """,
            document_id,
            chunk_version,
        )
        embedded_chunks = await conn.fetchval(
            """
            SELECT count(*)
            FROM public.kb_chunk_embeddings e
            JOIN public.kb_document_chunks c ON c.id = e.chunk_id
            WHERE c.document_id = $1
              AND c.chunking_version = $2
              AND c.is_active = true
              AND e.model_fingerprint = $3;
            """,
            document_id,
            chunk_version,
            model_fingerprint,
        )

        if total_chunks is None or total_chunks == 0 or embedded_chunks < total_chunks:
            logger.warning(
                f"Incomplete embedding set for document {document_id}: "
                f"expected {total_chunks} chunks, found {embedded_chunks} embedded. Cannot complete."
            )
            return False

        # 4. Update document embedding metadata
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET embedding_status = 'embedded',
                active_embedding_fingerprint = $2,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
            model_fingerprint,
        )

        # 5. Mark embedding job completed
        await conn.execute(
            """
            UPDATE public.kb_embedding_jobs
            SET status = 'completed',
                lease_token = NULL,
                locked_until = NULL,
                total_chunks = $2,
                embedded_chunks = $3,
                completed_at = timezone('utc'::text, now()),
                error_code = NULL,
                error_message = NULL,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            total_chunks,
            embedded_chunks,
        )
        return True


async def fail_embedding_job(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    error_code: str,
    error_message: str,
    is_transient: bool = True,
) -> None:
    """Record job failure and schedule exponential retry or mark terminal failure."""
    job = await conn.fetchrow(
        "SELECT attempts, max_attempts FROM public.kb_embedding_jobs WHERE id = $1",
        job_id,
    )
    if not job:
        return

    attempts = job["attempts"]
    max_attempts = job["max_attempts"]

    if is_transient and attempts < max_attempts:
        backoff_seconds = min(300, 2 ** (attempts + 1))
        logger.warning(
            f"Scheduling retry {attempts}/{max_attempts} for job {job_id} in {backoff_seconds}s: [{error_code}] {error_message}"
        )
        await conn.execute(
            """
            UPDATE public.kb_embedding_jobs
            SET status = 'queued',
                lease_token = NULL,
                locked_until = NULL,
                scheduled_at = timezone('utc'::text, now()) + ($2 || ' seconds')::interval,
                error_code = $3,
                error_message = $4,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            str(backoff_seconds),
            error_code,
            error_message[:500],
        )
    else:
        logger.error(
            f"Embedding job {job_id} reached terminal failure ({attempts}/{max_attempts} attempts): [{error_code}] {error_message}"
        )
        await conn.execute(
            """
            UPDATE public.kb_embedding_jobs
            SET status = 'failed',
                lease_token = NULL,
                locked_until = NULL,
                error_code = $2,
                error_message = $3,
                completed_at = timezone('utc'::text, now()),
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            error_code,
            error_message[:500],
        )
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET embedding_status = 'failed',
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
        )


async def cancel_embedding_jobs_for_document(
    conn: asyncpg.Connection,
    document_id: UUID,
    reason: str = "cancelled",
) -> int:
    """Cancel any pending or processing embedding jobs for a document."""
    res = await conn.execute(
        """
        UPDATE public.kb_embedding_jobs
        SET status = 'cancelled',
            lease_token = NULL,
            locked_until = NULL,
            error_code = 'CANCELLED',
            error_message = $2,
            completed_at = timezone('utc'::text, now()),
            updated_at = timezone('utc'::text, now())
        WHERE document_id = $1 AND status IN ('queued', 'processing');
        """,
        document_id,
        f"Embedding cancelled: {reason}",
    )
    return int(res.split(" ")[1]) if " " in res else 0
