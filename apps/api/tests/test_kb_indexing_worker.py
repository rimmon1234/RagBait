"""Integration tests for durable RAG document indexing worker, lifecycle, and fencing."""

from uuid import UUID, uuid4

import pytest

from app.cli.backfill_indexing import run_backfill
from app.core.config import settings
from app.core.database import get_pool, init_db_pool
from app.modules.knowledge_base.indexing_repository import (
    cancel_indexing_jobs_for_document,
    claim_next_indexing_job,
    enqueue_indexing_job,
)
from app.modules.knowledge_base.indexing_worker import KnowledgeBaseIndexingWorker

CREATED_FIXTURES: list[tuple[UUID, UUID, UUID]] = []  # (subject_id, topic_id, doc_id)


async def create_indexing_fixture(
    num_pages: int = 3,
    words_per_page: int = 100,
    has_text: bool = True,
) -> tuple[UUID, UUID, UUID]:
    """Helper to create subject, topic, document, and extracted pages."""
    pool = get_pool()
    sub_id = uuid4()
    top_id = uuid4()
    doc_id = uuid4()

    async with pool.acquire() as conn:
        # 1. Subject
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description)
            VALUES ($1, $2, $3, 'Test indexing subject')
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
                total_pages, processed_pages, pages_with_text
            )
            VALUES (
                $1, $2, $3, 'Indexing Test Doc', 'test.pdf',
                $4, 'application/pdf', 10240, 'uploaded', 'completed',
                $5, $5, $5
            )
            """,
            doc_id,
            sub_id,
            top_id,
            f"documents/{doc_id}/source.pdf",
            num_pages,
        )

        # 4. Pages
        if has_text:
            page_text = "Operating systems manage hardware concurrency, CPU scheduling, and virtual memory. " * (words_per_page // 10)
            for p_num in range(1, num_pages + 1):
                full_text = f"Page {p_num} text: {page_text}"
                await conn.execute(
                    """
                    INSERT INTO public.kb_document_pages (
                        document_id, page_number, raw_text, character_count, word_count, has_text, extraction_version
                    ) VALUES ($1, $2, $3, $4, $5, true, 1)
                    """,
                    doc_id,
                    p_num,
                    full_text,
                    len(full_text),
                    words_per_page,
                )

    CREATED_FIXTURES.append((sub_id, top_id, doc_id))
    return sub_id, top_id, doc_id


async def _purge_test_fixtures() -> None:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.kb_indexing_jobs WHERE document_id IN (SELECT id FROM public.kb_documents WHERE title = 'Indexing Test Doc')"
            )
            await conn.execute(
                "DELETE FROM public.kb_documents WHERE title = 'Indexing Test Doc'"
            )
            await conn.execute(
                "DELETE FROM public.kb_topics WHERE slug LIKE 'top-%'"
            )
            await conn.execute(
                "DELETE FROM public.kb_subjects WHERE slug LIKE 'sub-%'"
            )
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def cleanup_indexing_fixtures():
    """Clean up any database records created during test runs."""
    await init_db_pool()
    await _purge_test_fixtures()
    yield
    await _purge_test_fixtures()
    CREATED_FIXTURES.clear()


@pytest.mark.asyncio
async def test_enqueue_and_worker_step_success():
    """Worker claims queued job, generates chunks with physical page provenance, and completes."""
    _, _, doc_id = await create_indexing_fixture(num_pages=3, words_per_page=120)

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_indexing_job(conn, doc_id)
        assert job_id is not None

        # Verify job is queued
        job = await conn.fetchrow("SELECT status, attempts FROM public.kb_indexing_jobs WHERE id = $1", job_id)
        assert job["status"] == "queued"
        assert job["attempts"] == 0

    # Run worker step
    worker = KnowledgeBaseIndexingWorker(once=True)
    worker.pool = pool
    processed = await worker.step()
    assert processed is True

    async with pool.acquire() as conn:
        # Verify job completed
        job_after = await conn.fetchrow("SELECT * FROM public.kb_indexing_jobs WHERE id = $1", job_id)
        assert job_after["status"] == "completed"
        assert job_after["total_chunks"] > 0
        assert job_after["total_tokens"] > 0
        assert job_after["lease_token"] is None
        assert job_after["completed_at"] is not None

        # Verify document updated
        doc = await conn.fetchrow("SELECT indexing_status, active_chunk_version FROM public.kb_documents WHERE id = $1", doc_id)
        assert doc["indexing_status"] == "indexed"
        assert doc["active_chunk_version"] == 1

        # Verify chunks created and active
        chunks = await conn.fetch(
            "SELECT * FROM public.kb_document_chunks WHERE document_id = $1 ORDER BY chunk_index ASC",
            doc_id,
        )
        assert len(chunks) == job_after["total_chunks"]
        for idx, c in enumerate(chunks):
            assert c["chunk_index"] == idx
            assert c["is_active"] is True
            assert c["token_count"] <= 420
            assert c["token_count"] > 0
            assert c["page_number_start"] >= 1
            assert c["page_number_end"] >= c["page_number_start"]
            assert len(c["content_hash"]) == 64


@pytest.mark.asyncio
async def test_lease_expiration_and_recovery():
    """Expired worker lease allows another worker to reclaim and complete the job."""
    _, _, doc_id = await create_indexing_fixture(num_pages=2, words_per_page=100)

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_indexing_job(conn, doc_id)

        # Worker A claims job with expired lease (locked_until in the past)
        stale_worker = "worker-stale-01"
        claimed = await claim_next_indexing_job(conn, worker_id=stale_worker, lease_ttl_seconds=-10)
        assert claimed is not None
        assert claimed["job_id"] == job_id
        assert claimed["worker_id"] == stale_worker
        assert claimed["attempts"] == 1

        # Worker B claims next job: should reclaim the expired job
        fresh_worker = "worker-fresh-02"
        reclaimed = await claim_next_indexing_job(conn, worker_id=fresh_worker, lease_ttl_seconds=120)
        assert reclaimed is not None
        assert reclaimed["job_id"] == job_id
        assert reclaimed["worker_id"] == fresh_worker
        assert reclaimed["attempts"] == 2


@pytest.mark.asyncio
async def test_archival_cancels_active_job_and_deactivates_chunks():
    """Archiving a document cancels queued/processing jobs and deactivates chunks."""
    _, _, doc_id = await create_indexing_fixture(num_pages=2, words_per_page=100)

    pool = get_pool()
    async with pool.acquire() as conn:
        job_id = await enqueue_indexing_job(conn, doc_id)

        # Cancel active jobs via archival helper
        cancelled_count = await cancel_indexing_jobs_for_document(conn, doc_id, reason="admin_archived")
        assert cancelled_count == 1

        # Verify job is cancelled
        job = await conn.fetchrow("SELECT status, error_code FROM public.kb_indexing_jobs WHERE id = $1", job_id)
        assert job["status"] == "cancelled"
        assert job["error_code"] == "CANCELLED"

        # Worker step should find no eligible jobs
        worker = KnowledgeBaseIndexingWorker(once=True)
        worker.pool = pool
        had_job = await worker.step()
        assert had_job is False


@pytest.mark.asyncio
async def test_backfill_cli_discovery_and_enqueue():
    """Backfill CLI finds unindexed completed documents and enqueues jobs idempotently."""
    _, _, doc_id = await create_indexing_fixture(num_pages=2, words_per_page=100)

    # Dry run
    discovered, enqueued = await run_backfill(dsn=settings.DATABASE_URL, dry_run=True)
    assert discovered >= 1
    assert enqueued == 0

    # Real run
    discovered_2, enqueued_2 = await run_backfill(dsn=settings.DATABASE_URL, dry_run=False)
    assert discovered_2 >= 1
    assert enqueued_2 >= 1

    # Second real run: idempotent, nothing new to enqueue
    discovered_3, enqueued_3 = await run_backfill(dsn=settings.DATABASE_URL, dry_run=False)
    assert enqueued_3 == 0
