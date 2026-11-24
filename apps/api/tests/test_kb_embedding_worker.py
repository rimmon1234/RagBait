"""Integration tests for durable RAG document embedding worker, lifecycle, and fencing."""

from uuid import UUID, uuid4

import pytest

from app.cli.backfill_embeddings import run_embedding_backfill
from app.core.config import settings
from app.core.database import get_pool, init_db_pool
from app.modules.knowledge_base.embeddings.model_manifest import get_model_manifest
from app.modules.knowledge_base.embeddings.repository import (
    cancel_embedding_jobs_for_document,
    claim_next_embedding_job,
    enqueue_embedding_job,
)
from app.modules.knowledge_base.embeddings.worker import KnowledgeBaseEmbeddingWorker

CREATED_FIXTURES: list[tuple[UUID, UUID, UUID]] = []  # (subject_id, topic_id, doc_id)


async def create_embedding_fixture(
    num_chunks: int = 3,
) -> tuple[UUID, UUID, UUID]:
    """Helper to create subject, topic, document, and active chunks."""
    pool = get_pool()
    sub_id = uuid4()
    top_id = uuid4()
    doc_id = uuid4()

    async with pool.acquire() as conn:
        # 1. Subject
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description)
            VALUES ($1, $2, $3, 'Test embedding subject')
            """,
            sub_id,
            f"Subject {sub_id.hex[:6]}",
            f"sub-{sub_id.hex[:6]}",
        )
        # 2. Topic
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug)
            VALUES ($1, $2, $3, $4)
            """,
            top_id,
            sub_id,
            f"Topic {top_id.hex[:6]}",
            f"top-{top_id.hex[:6]}",
        )
        # 3. Document
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                indexing_status, active_chunk_version
            )
            VALUES (
                $1, $2, $3, 'Embedding Test Doc', 'test.pdf',
                $4, 'application/pdf', 10240, 'uploaded', 'completed',
                'indexed', 1
            )
            """,
            doc_id,
            sub_id,
            top_id,
            f"documents/{doc_id}/source.pdf",
        )

        # 4. Chunks
        for c_idx in range(num_chunks):
            chunk_text = (
                f"Chunk {c_idx}: Operating systems provide virtual memory and CPU abstraction. "
                f"Processes communicate via IPC channels and shared memory regions."
            )
            await conn.execute(
                """
                INSERT INTO public.kb_document_chunks (
                    document_id, chunk_index, page_number_start, page_number_end,
                    raw_text, character_count, token_count, content_hash,
                    chunking_version, is_active
                ) VALUES ($1, $2, $3, $3, $4, $5, 25, $6, 1, true)
                """,
                doc_id,
                c_idx,
                c_idx + 1,
                chunk_text,
                len(chunk_text),
                f"hash_{c_idx}_{doc_id.hex[:6]}",
            )

    CREATED_FIXTURES.append((sub_id, top_id, doc_id))
    return sub_id, top_id, doc_id


async def _purge_test_fixtures() -> None:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.kb_chunk_embeddings WHERE document_id IN (SELECT id FROM public.kb_documents WHERE title = 'Embedding Test Doc')"
            )
            await conn.execute(
                "DELETE FROM public.kb_embedding_jobs WHERE document_id IN (SELECT id FROM public.kb_documents WHERE title = 'Embedding Test Doc')"
            )
            await conn.execute(
                "DELETE FROM public.kb_document_chunks WHERE document_id IN (SELECT id FROM public.kb_documents WHERE title = 'Embedding Test Doc')"
            )
            await conn.execute("DELETE FROM public.kb_documents WHERE title = 'Embedding Test Doc'")
            await conn.execute("DELETE FROM public.kb_topics WHERE slug LIKE 'top-%'")
            await conn.execute("DELETE FROM public.kb_subjects WHERE slug LIKE 'sub-%'")
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def cleanup_embedding_fixtures():
    """Clean up any database records created during test runs."""
    await init_db_pool()
    await _purge_test_fixtures()
    yield
    await _purge_test_fixtures()


@pytest.mark.asyncio
async def test_enqueue_and_worker_step_embedding_success():
    """Verify worker claims job, generates embeddings, persists vectors, and completes."""
    sub_id, top_id, doc_id = await create_embedding_fixture(num_chunks=3)
    manifest = get_model_manifest()
    fingerprint = manifest.model_fingerprint

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_embedding_job(
            conn=conn,
            document_id=doc_id,
            chunk_version=1,
            model_fingerprint=fingerprint,
        )
        assert job_id is not None

        # Verify job is queued
        job = await conn.fetchrow(
            "SELECT status FROM public.kb_embedding_jobs WHERE id = $1", job_id
        )
        assert job["status"] == "queued"

    # Run worker step
    worker = KnowledgeBaseEmbeddingWorker(worker_id="test-embed-worker-01", once=True)
    await worker.start()
    try:
        did_work = await worker.step()
        assert did_work is True
    finally:
        await worker.stop()

    async with pool.acquire() as conn:
        # Verify job is completed
        job_after = await conn.fetchrow(
            "SELECT status, total_chunks, embedded_chunks FROM public.kb_embedding_jobs WHERE id = $1",
            job_id,
        )
        assert job_after["status"] == "completed"
        assert job_after["total_chunks"] == 3
        assert job_after["embedded_chunks"] == 3

        # Verify document updated
        doc = await conn.fetchrow(
            "SELECT embedding_status, active_embedding_fingerprint FROM public.kb_documents WHERE id = $1",
            doc_id,
        )
        assert doc["embedding_status"] == "embedded"
        assert doc["active_embedding_fingerprint"] == fingerprint

        # Verify vector rows in public.kb_chunk_embeddings
        emb_rows = await conn.fetch(
            "SELECT * FROM public.kb_chunk_embeddings WHERE document_id = $1",
            doc_id,
        )
        assert len(emb_rows) == 3
        for r in emb_rows:
            assert r["model_fingerprint"] == fingerprint
            assert r["dimensions"] == 384


@pytest.mark.asyncio
async def test_lease_expiration_and_recovery():
    """An expired worker lease is automatically reclaimed by a subsequent worker."""
    sub_id, top_id, doc_id = await create_embedding_fixture(num_chunks=2)
    manifest = get_model_manifest()
    fingerprint = manifest.model_fingerprint

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_embedding_job(
            conn=conn,
            document_id=doc_id,
            chunk_version=1,
            model_fingerprint=fingerprint,
        )
        # Simulate worker-crashed: status=processing, locked_until in past
        await conn.execute(
            """
            UPDATE public.kb_embedding_jobs
            SET status = 'processing',
                worker_id = 'crashed-worker',
                lease_token = gen_random_uuid(),
                locked_until = timezone('utc'::text, now()) - interval '10 seconds'
            WHERE id = $1
            """,
            job_id,
        )

        # Fresh worker claims expired lease
        fresh_worker = "fresh-worker-02"
        reclaimed = await claim_next_embedding_job(
            conn, worker_id=fresh_worker, lease_ttl_seconds=120
        )
        assert reclaimed is not None
        assert reclaimed["job_id"] == job_id
        assert reclaimed["worker_id"] == fresh_worker
        assert reclaimed["attempts"] == 1


@pytest.mark.asyncio
async def test_archival_cancels_active_embedding_job():
    """Archiving a document cancels any active embedding jobs."""
    sub_id, top_id, doc_id = await create_embedding_fixture(num_chunks=2)
    manifest = get_model_manifest()
    fingerprint = manifest.model_fingerprint

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_embedding_job(
            conn=conn,
            document_id=doc_id,
            chunk_version=1,
            model_fingerprint=fingerprint,
        )
        # Cancel via archival helper
        cancelled_count = await cancel_embedding_jobs_for_document(
            conn, doc_id, reason="admin_archived"
        )
        assert cancelled_count == 1

        # Verify job is cancelled
        job = await conn.fetchrow(
            "SELECT status, error_code FROM public.kb_embedding_jobs WHERE id = $1",
            job_id,
        )
        assert job["status"] == "cancelled"
        assert job["error_code"] == "CANCELLED"


@pytest.mark.asyncio
async def test_backfill_cli_discovery_and_enqueue():
    """Backfill CLI finds indexed documents missing embeddings and enqueues them."""
    sub_id, top_id, doc_id = await create_embedding_fixture(num_chunks=2)

    # 1. Dry run discovers 1 document
    discovered, enqueued = await run_embedding_backfill(
        dsn=settings.DATABASE_URL,
        subject_id=sub_id,
        dry_run=True,
    )
    assert discovered == 1
    assert enqueued == 0

    # 2. Live backfill enqueues 1 job
    discovered, enqueued = await run_embedding_backfill(
        dsn=settings.DATABASE_URL,
        subject_id=sub_id,
        dry_run=False,
    )
    assert discovered == 1
    assert enqueued == 1

    # 3. Repeated run finds document already queued, enqueues 0
    discovered, enqueued = await run_embedding_backfill(
        dsn=settings.DATABASE_URL,
        subject_id=sub_id,
        dry_run=False,
    )
    assert enqueued == 0
