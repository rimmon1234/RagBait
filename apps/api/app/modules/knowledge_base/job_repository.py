"""Database operations for durable extraction job claiming, heartbeating, and atomic completion."""

import logging
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

logger = logging.getLogger("ragbait.kb.job_repository")


async def claim_next_job(
    conn: asyncpg.Connection,
    worker_id: str,
    lease_ttl_seconds: int = 120,
) -> dict[str, Any] | None:
    """Atomically claim the next eligible extraction job using FOR UPDATE SKIP LOCKED.

    Eligible jobs are:
    1. status = 'queued' AND scheduled_at <= NOW()
       OR status = 'processing' AND locked_until < NOW() (expired worker lease)
    2. Document is uploaded (upload_status = 'uploaded') and not archived.
    3. attempts < max_attempts.

    Updates job to 'processing', sets lease_token and locked_until, increments attempts,
    and updates document extraction_status to 'processing'.
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            WITH eligible_job AS (
                SELECT
                    j.id AS eligible_job_id,
                    j.document_id,
                    d.storage_path,
                    d.mime_type,
                    d.file_size_bytes,
                    d.sha256_checksum,
                    d.title
                FROM public.kb_extraction_jobs j
                JOIN public.kb_documents d ON d.id = j.document_id
                WHERE (
                    (j.status = 'queued' AND j.scheduled_at <= timezone('utc'::text, now()))
                    OR (j.status = 'processing' AND j.locked_until < timezone('utc'::text, now()))
                )
                AND d.upload_status = 'uploaded'
                AND d.is_archived = false
                AND j.attempts < j.max_attempts
                ORDER BY j.scheduled_at ASC
                LIMIT 1
                FOR UPDATE OF j SKIP LOCKED
            )
            UPDATE public.kb_extraction_jobs j
            SET status = 'processing',
                worker_id = $1,
                lease_token = gen_random_uuid(),
                locked_until = timezone('utc'::text, now()) + ($2 || ' seconds')::interval,
                attempts = j.attempts + 1,
                updated_at = timezone('utc'::text, now())
            FROM eligible_job ej
            WHERE j.id = ej.eligible_job_id
            RETURNING j.id AS job_id, j.document_id, j.worker_id, j.lease_token, j.attempts,
                      j.max_attempts, j.locked_until, ej.storage_path, ej.mime_type,
                      ej.file_size_bytes, ej.sha256_checksum, ej.title;
            """,
            worker_id,
            str(lease_ttl_seconds),
        )

        if not row:
            return None

        # Update parent document status to 'processing'
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET extraction_status = 'processing',
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            row["document_id"],
        )

        return dict(row)


async def renew_job_lease(
    conn: asyncpg.Connection,
    job_id: UUID,
    worker_id: str,
    lease_token: UUID,
    lease_ttl_seconds: int = 120,
) -> bool:
    """Extend the job lease deadline if the worker still holds the valid lease token.

    Also checks that the document is still active and has not been deleted or archived.
    Returns True if lease renewed, False if lost or cancelled.
    """
    row = await conn.fetchrow(
        """
        UPDATE public.kb_extraction_jobs j
        SET locked_until = timezone('utc'::text, now()) + ($4 || ' seconds')::interval,
            updated_at = timezone('utc'::text, now())
        FROM public.kb_documents d
        WHERE j.id = $1
          AND j.worker_id = $2
          AND j.lease_token = $3
          AND j.status = 'processing'
          AND j.document_id = d.id
          AND d.upload_status = 'uploaded'
          AND d.is_archived = false
        RETURNING j.id;
        """,
        job_id,
        worker_id,
        lease_token,
        str(lease_ttl_seconds),
    )
    return row is not None


async def complete_job_success(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    pages: list[dict[str, Any]],
    has_any_text: bool,
) -> bool:
    """Atomically persist extracted pages and mark document and job as completed.

    Guarantees:
    1. Lock ordering: kb_documents locked first, then kb_extraction_jobs.
    2. Lease re-verification prevents stale workers from publishing results.
    3. Re-cleans previous page rows for document_id (safe against retries/crashes).
    4. Batch inserts all page records.
    5. Sets document extraction_status to 'completed' or 'completed_no_text'.
    6. Marks job status = 'completed' and clears lease_token.
    """
    async with conn.transaction():
        # 1. Lock document first to maintain strict lock ordering with deletion
        doc = await conn.fetchrow(
            """
            SELECT id, upload_status, is_archived
            FROM public.kb_documents
            WHERE id = $1
            FOR UPDATE;
            """,
            document_id,
        )
        if not doc or doc["upload_status"] != "uploaded" or doc["is_archived"]:
            logger.warning(
                f"Document {document_id} was deleted/archived during processing. Aborting completion."
            )
            return False

        # 2. Lock job and verify lease token
        job = await conn.fetchrow(
            """
            SELECT id
            FROM public.kb_extraction_jobs
            WHERE id = $1 AND lease_token = $2 AND worker_id = $3 AND status = 'processing'
            FOR UPDATE;
            """,
            job_id,
            lease_token,
            worker_id,
        )
        if not job:
            logger.warning(f"Worker {worker_id} lost lease on job {job_id}. Aborting completion.")
            return False

        # 3. Clean any existing pages (e.g. from a prior crashed run)
        await conn.execute(
            """
            DELETE FROM public.kb_document_pages
            WHERE document_id = $1;
            """,
            document_id,
        )

        # 4. Batch insert extracted pages
        if pages:
            records = [
                (
                    document_id,
                    p["page_number"],
                    p["raw_text"],
                    p["character_count"],
                    p["word_count"],
                    p["has_text"],
                )
                for p in pages
            ]
            await conn.executemany(
                """
                INSERT INTO public.kb_document_pages (
                    document_id,
                    page_number,
                    raw_text,
                    character_count,
                    word_count,
                    has_text
                ) VALUES ($1, $2, $3, $4, $5, $6);
                """,
                records,
            )

        total_pages = len(pages)
        pages_with_text = sum(1 for p in pages if p["has_text"])
        final_doc_status = "completed" if has_any_text else "completed_no_text"

        # 5. Update document metadata
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET extraction_status = $2::public.kb_extraction_status,
                total_pages = $3,
                processed_pages = $3,
                pages_with_text = $4,
                error_code = NULL,
                error_message = NULL,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
            final_doc_status,
            total_pages,
            pages_with_text,
        )

        # 6. Update extraction job to completed
        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET status = 'completed',
                lease_token = NULL,
                locked_until = NULL,
                error_code = NULL,
                error_message = NULL,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
        )

        return True


async def fail_job_permanent(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    error_code: str,
    error_message: str,
) -> bool:
    """Record permanent failure for a job and document. Will not be retried."""
    async with conn.transaction():
        # Lock document
        doc = await conn.fetchrow(
            """
            SELECT id FROM public.kb_documents WHERE id = $1 FOR UPDATE;
            """,
            document_id,
        )
        if not doc:
            return False

        # Lock job and verify lease
        job = await conn.fetchrow(
            """
            SELECT id FROM public.kb_extraction_jobs
            WHERE id = $1 AND lease_token = $2 AND worker_id = $3 FOR UPDATE;
            """,
            job_id,
            lease_token,
            worker_id,
        )
        if not job:
            return False

        # Update document
        await conn.execute(
            """
            UPDATE public.kb_documents
            SET extraction_status = 'failed',
                error_code = $2,
                error_message = $3,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
            error_code,
            error_message[:1000],
        )

        # Update job
        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET status = 'failed',
                lease_token = NULL,
                locked_until = NULL,
                error_code = $2,
                error_message = $3,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            error_code,
            error_message[:1000],
        )

        return True


async def fail_job_transient(
    conn: asyncpg.Connection,
    job_id: UUID,
    document_id: UUID,
    worker_id: str,
    lease_token: UUID,
    error_code: str,
    error_message: str,
    backoff_base_seconds: int = 10,
    backoff_max_seconds: int = 300,
) -> bool:
    """Handle a transient failure with exponential backoff retry.

    If attempts >= max_attempts, fails permanently.
    """
    async with conn.transaction():
        doc = await conn.fetchrow(
            """
            SELECT id FROM public.kb_documents WHERE id = $1 FOR UPDATE;
            """,
            document_id,
        )
        if not doc:
            return False

        job = await conn.fetchrow(
            """
            SELECT id, attempts, max_attempts FROM public.kb_extraction_jobs
            WHERE id = $1 AND lease_token = $2 AND worker_id = $3 FOR UPDATE;
            """,
            job_id,
            lease_token,
            worker_id,
        )
        if not job:
            return False

        attempts = job["attempts"]
        max_attempts = job["max_attempts"]

        if attempts >= max_attempts:
            # Exhausted retries -> permanent failure
            final_err_code = "MAX_ATTEMPTS_EXCEEDED"
            final_err_msg = (
                f"Extraction failed after {attempts} attempts. Last error: {error_message}"
            )
            await conn.execute(
                """
                UPDATE public.kb_documents
                SET extraction_status = 'failed',
                    error_code = $2,
                    error_message = $3,
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                document_id,
                final_err_code,
                final_err_msg[:1000],
            )
            await conn.execute(
                """
                UPDATE public.kb_extraction_jobs
                SET status = 'failed',
                    lease_token = NULL,
                    locked_until = NULL,
                    error_code = $2,
                    error_message = $3,
                    updated_at = timezone('utc'::text, now())
                WHERE id = $1;
                """,
                job_id,
                final_err_code,
                final_err_msg[:1000],
            )
            return True

        # Re-queue for next retry with exponential backoff
        backoff_mult = 2 ** max(0, attempts - 1)
        delay_seconds = min(backoff_max_seconds, backoff_base_seconds * backoff_mult)

        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET status = 'queued',
                scheduled_at = timezone('utc'::text, now()) + ($2 || ' seconds')::interval,
                lease_token = NULL,
                locked_until = NULL,
                error_code = $3,
                error_message = $4,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            job_id,
            str(delay_seconds),
            error_code,
            error_message[:1000],
        )

        await conn.execute(
            """
            UPDATE public.kb_documents
            SET extraction_status = 'queued',
                error_code = $2,
                error_message = $3,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            document_id,
            error_code,
            error_message[:1000],
        )

        return True
