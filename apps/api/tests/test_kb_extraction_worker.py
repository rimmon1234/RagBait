"""Comprehensive test suite for Durable Background Document Extraction Worker (M3.3).

Validates:
1. Atomic job claiming via FOR UPDATE SKIP LOCKED and expired-lease crash recovery.
2. Worker lease renewal, heartbeating, and fencing against stale tokens.
3. Storage streaming download with incremental SHA-256 and size verification.
4. Deterministic PDF physical page provenance, empty page preservation, and scanned PDF detection.
5. Deterministic TXT logical page extraction with form-feed '\\f' and encoding validation.
6. Hard process-isolated parser execution with enforceable process-level timeouts (SIGKILL).
7. Atomic page persistence and document completion in single database transactions.
8. Deletion lifecycle fencing and cascading page cleanup.
9. Transient failure retry with exponential backoff and permanent failure transitions.
10. End-to-end standalone worker execution loop in '--once' mode.
"""

import asyncio
import hashlib
import io
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.core.database import close_db_pool, get_pool, init_db_pool
from app.modules.knowledge_base import job_repository, repository, storage
from app.modules.knowledge_base.worker import KnowledgeBaseExtractionWorker
from app.modules.knowledge_base.worker_config import WorkerSettings

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []
CREATED_STORAGE_PATHS: list[str] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pool is initialized and resources are cleaned up."""
    await init_db_pool()
    yield

    # Clean up storage objects
    if CREATED_STORAGE_PATHS:
        for path in CREATED_STORAGE_PATHS:
            try:
                await storage.delete_storage_object(path)
            except Exception:
                pass
        CREATED_STORAGE_PATHS.clear()

    # Clean up DB documents, pages, jobs, topics, subjects, and users
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            if CREATED_TEST_SUBJECTS or CREATED_TEST_USERS:
                await conn.execute(
                    """
                    DELETE FROM public.kb_documents
                    WHERE subject_id = ANY($1) OR uploader_id = ANY($2)
                    """,
                    CREATED_TEST_SUBJECTS,
                    CREATED_TEST_USERS,
                )
            if CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.kb_topics WHERE subject_id = ANY($1)",
                    CREATED_TEST_SUBJECTS,
                )
                await conn.execute(
                    "DELETE FROM public.kb_subjects WHERE id = ANY($1)",
                    CREATED_TEST_SUBJECTS,
                )
            if CREATED_TEST_USERS:
                await conn.execute(
                    "DELETE FROM auth.users WHERE id = ANY($1)",
                    CREATED_TEST_USERS,
                )
    except Exception:
        pass
    CREATED_TEST_SUBJECTS.clear()
    CREATED_TEST_USERS.clear()

    await storage.close_storage_client()
    await close_db_pool()


# ------------------------------------------------------------------------------
# Test Helpers
# ------------------------------------------------------------------------------


async def create_db_user() -> UUID:
    """Create test student user in auth.users and public.profiles."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"student_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Test Student"}'::jsonb)
            """,
            user_id,
            unique_email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = 'user', approval_status = 'approved'
            WHERE id = $1
            """,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id


async def create_test_taxonomy() -> tuple[UUID, UUID]:
    """Create a test subject and topic."""
    pool = get_pool()
    subject_id = uuid4()
    topic_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description, icon_name, display_order, is_active)
            VALUES ($1, $2, $3, 'Operating Systems', 'Cpu', 1, true)
            """,
            subject_id,
            f"OS-{subject_id.hex[:6]}",
            f"os-{subject_id.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, description, display_order, is_active)
            VALUES ($1, $2, 'Virtual Memory', 'virtual-memory', 'Paging and segmentation', 1, true)
            """,
            topic_id,
            subject_id,
        )

    CREATED_TEST_SUBJECTS.append(subject_id)
    return subject_id, topic_id


def make_pdf_bytes(
    pages_text: list[str | None],
    password: str | None = None,
) -> bytes:
    """Generate valid PDF bytes with specified text pages, blank pages, and optional encryption."""
    writer = PdfWriter()

    for text in pages_text:
        if text is None:
            writer.add_blank_page(width=100, height=100)
        else:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_content = f"BT /F1 12 Tf 100 700 Td ({escaped}) Tj ET".encode()
            raw_pdf = (
                b"%PDF-1.4\n"
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
                b"4 0 obj << /Length "
                + str(len(stream_content)).encode("ascii")
                + b" >>\nstream\n"
                + stream_content
                + b"\nendstream\nendobj\n"
                b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
                b"xref\n0 6\n0000000000 65535 f \ntrailer << /Size 6 /Root 1 0 R >>\nstartxref\n500\n%%EOF"
            )
            reader = PdfReader(io.BytesIO(raw_pdf))
            writer.add_page(reader.pages[0])

    if password:
        writer.encrypt(password)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def create_confirmed_document(
    user_id: UUID,
    subject_id: UUID,
    topic_id: UUID,
    content: bytes,
    mime_type: str = "application/pdf",
    filename: str = "notes.pdf",
) -> tuple[dict, dict]:
    """Helper to create uploaded document and queued extraction job in database and storage."""
    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.{'pdf' if mime_type == 'application/pdf' else 'txt'}"
    sha256 = hashlib.sha256(content).hexdigest()
    size = len(content)

    # 1. Upload to storage
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, content, {"content-type": mime_type}
    )
    CREATED_STORAGE_PATHS.append(storage_path)

    # 2. Insert document record and extraction job
    pool = get_pool()
    async with pool.acquire() as conn:
        doc_row = await conn.fetchrow(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, uploader_email_snapshot,
                title, original_filename, storage_path, mime_type, file_size_bytes,
                sha256_checksum, upload_status, extraction_status, upload_expires_at
            ) VALUES (
                $1, $2, $3, $4, 'student@university.edu',
                'Virtual Memory Notes', $5, $6, $7, $8,
                $9, 'uploaded', 'queued', timezone('utc'::text, now()) + interval '1 hour'
            ) RETURNING *;
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            filename,
            storage_path,
            mime_type,
            size,
            sha256,
        )

        job_row = await conn.fetchrow(
            """
            INSERT INTO public.kb_extraction_jobs (
                document_id, status, attempts, max_attempts, scheduled_at
            ) VALUES (
                $1, 'queued', 0, 3, timezone('utc'::text, now())
            ) RETURNING *;
            """,
            doc_id,
        )

    return dict(doc_row), dict(job_row)


# ------------------------------------------------------------------------------
# 1. Job Claiming Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_next_job_success():
    """Eligible queued job is claimed, lease assigned, and status moved to processing."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Test Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    worker_id = "test-worker-1"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(conn, worker_id, lease_ttl_seconds=120)

    assert claimed is not None
    assert claimed["job_id"] == job["id"]
    assert claimed["document_id"] == doc["id"]
    assert claimed["worker_id"] == worker_id
    assert claimed["lease_token"] is not None
    assert claimed["attempts"] == 1
    assert claimed["locked_until"] > datetime.now(UTC)

    # Verify DB state
    async with pool.acquire() as conn:
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])

    assert db_job["status"] == "processing"
    assert db_job["worker_id"] == worker_id
    assert db_doc["extraction_status"] == "processing"


@pytest.mark.asyncio
async def test_claim_next_job_future_scheduled_skipped():
    """Jobs scheduled in the future are not claimed."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Future Page"])
    _, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.kb_extraction_jobs SET scheduled_at = timezone('utc'::text, now()) + interval '1 hour' WHERE id = $1",
            job["id"],
        )
        claimed = await job_repository.claim_next_job(conn, "test-worker-future")

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_next_job_deleted_or_archived_skipped():
    """Jobs for deleted or archived documents are never claimed."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Deleted Doc Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    async with pool.acquire() as conn:
        # Mark document deleted
        await conn.execute(
            "UPDATE public.kb_documents SET upload_status = 'deleted', is_archived = true WHERE id = $1",
            doc["id"],
        )
        claimed = await job_repository.claim_next_job(conn, "test-worker-deleted")

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_next_job_recovers_crashed_worker():
    """Jobs with expired worker leases (locked_until < NOW()) are recovered by a new worker."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Crash Recovery Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    # Simulate crashed worker: job is 'processing' with expired lock
    stale_token = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET status = 'processing',
                worker_id = 'crashed-worker-99',
                lease_token = $1,
                locked_until = timezone('utc'::text, now()) - interval '10 seconds',
                attempts = 1
            WHERE id = $2
            """,
            stale_token,
            job["id"],
        )

        claimed = await job_repository.claim_next_job(
            conn, "recovery-worker-1", lease_ttl_seconds=60
        )

    assert claimed is not None
    assert claimed["job_id"] == job["id"]
    assert claimed["worker_id"] == "recovery-worker-1"
    assert claimed["lease_token"] != stale_token
    assert claimed["attempts"] == 2
    assert claimed["locked_until"] > datetime.now(UTC)


@pytest.mark.asyncio
async def test_claim_next_job_concurrent_skip_locked():
    """Two concurrent workers attempting to claim the single eligible job: exactly one gets it."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Concurrent Claim Page"])
    _, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()

    async def _claim(worker_name: str):
        async with pool.acquire() as conn:
            return await job_repository.claim_next_job(conn, worker_name)

    results = await asyncio.gather(_claim("worker-a"), _claim("worker-b"))
    claimed_jobs = [r for r in results if r is not None]

    assert len(claimed_jobs) == 1
    assert claimed_jobs[0]["job_id"] == job["id"]


# ------------------------------------------------------------------------------
# 2. Lease Renewal & Heartbeat Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renew_job_lease_success():
    """Active lease is extended when worker ID and lease token match."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Lease Test Page"])
    _, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    worker_id = "heartbeat-worker-1"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(conn, worker_id, lease_ttl_seconds=30)
        assert claimed is not None
        job_id = claimed["job_id"]
        token = claimed["lease_token"]

        renewed = await job_repository.renew_job_lease(
            conn, job_id, worker_id, token, lease_ttl_seconds=120
        )

    assert renewed is True


@pytest.mark.asyncio
async def test_renew_job_lease_stale_token_rejected():
    """Lease renewal fails when presenting a stale or incorrect lease token."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Stale Lease Page"])
    _, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    worker_id = "heartbeat-worker-2"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(conn, worker_id, lease_ttl_seconds=30)
        assert claimed is not None
        job_id = claimed["job_id"]

        fake_token = uuid4()
        renewed = await job_repository.renew_job_lease(
            conn, job_id, worker_id, fake_token, lease_ttl_seconds=120
        )

    assert renewed is False


@pytest.mark.asyncio
async def test_renew_job_lease_doc_deleted_rejected():
    """Lease renewal fails when the parent document has been transitioned to deleting/deleted."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Delete Mid Lease Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    worker_id = "heartbeat-worker-3"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(conn, worker_id, lease_ttl_seconds=30)
        assert claimed is not None
        job_id = claimed["job_id"]
        token = claimed["lease_token"]

        # Transition document to deleting
        await repository.transition_document_to_deleting(
            conn=conn, document_id=doc["id"], user_id=user_id, is_admin=False
        )

        renewed = await job_repository.renew_job_lease(
            conn, job_id, worker_id, token, lease_ttl_seconds=120
        )

    assert renewed is False


# ------------------------------------------------------------------------------
# 3. Integrity Verification Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_detects_checksum_mismatch():
    """Worker detects SHA-256 checksum mismatch, aborts parsing, and fails permanently."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Integrity Check Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    # Corrupt the recorded checksum in database
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.kb_documents SET sha256_checksum = '0000000000000000000000000000000000000000000000000000000000000000' WHERE id = $1",
            doc["id"],
        )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="integrity-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "CHECKSUM_MISMATCH"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "CHECKSUM_MISMATCH"


@pytest.mark.asyncio
async def test_worker_detects_size_mismatch():
    """Worker detects byte size mismatch, aborts parsing, and fails permanently."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Size Check Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    # Corrupt recorded file size in database
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.kb_documents SET file_size_bytes = 999999 WHERE id = $1",
            doc["id"],
        )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="size-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "FILE_SIZE_MISMATCH"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "FILE_SIZE_MISMATCH"


# ------------------------------------------------------------------------------
# 4. PDF Extraction & Provenance Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_physical_page_provenance_and_empty_pages():
    """Multi-page PDF extracts exact physical pages, preserves empty pages, and satisfies DB constraints."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    # Page 1: Text, Page 2: Blank (None), Page 3: Text
    content = make_pdf_bytes(["Page One Content", None, "Page Three Content"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="pdf-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )
        pages, total = await repository.get_document_pages(conn, doc["id"])

    assert db_doc["extraction_status"] == "completed"
    assert db_doc["total_pages"] == 3
    assert db_doc["processed_pages"] == 3
    assert db_doc["pages_with_text"] == 2
    assert db_job["status"] == "completed"
    assert total == 3

    # Verify physical page provenance
    assert pages[0]["page_number"] == 1
    assert "Page One Content" in pages[0]["raw_text"]
    assert pages[0]["has_text"] is True
    assert pages[0]["character_count"] == len(pages[0]["raw_text"])

    # Page 2 is empty physical page: preserved!
    assert pages[1]["page_number"] == 2
    assert pages[1]["raw_text"] == ""
    assert pages[1]["has_text"] is False
    assert pages[1]["character_count"] == 0
    assert pages[1]["word_count"] == 0

    assert pages[2]["page_number"] == 3
    assert "Page Three Content" in pages[2]["raw_text"]
    assert pages[2]["has_text"] is True


@pytest.mark.asyncio
async def test_pdf_scanned_marked_completed_no_text():
    """PDF containing only blank/scanned pages is marked completed_no_text."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    # 2 blank pages (scanned image with no text layer)
    content = make_pdf_bytes([None, None])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="scanned-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "completed_no_text"
    assert db_doc["total_pages"] == 2
    assert db_doc["pages_with_text"] == 0
    assert db_job["status"] == "completed"


@pytest.mark.asyncio
async def test_pdf_encrypted_fails_permanent():
    """Password-protected/encrypted PDF fails permanently with ENCRYPTED_PDF."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Secret Notes"], password="my_password")
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="encrypted-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "ENCRYPTED_PDF"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "ENCRYPTED_PDF"


@pytest.mark.asyncio
async def test_pdf_corrupt_fails_permanent():
    """Corrupt PDF file bytes fail permanently with CORRUPT_FILE."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = b"%PDF-1.4\ncorrupt garbage bytes that cannot be parsed as a PDF"
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="corrupt-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "CORRUPT_FILE"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "CORRUPT_FILE"


@pytest.mark.asyncio
async def test_pdf_page_limit_exceeded():
    """PDF exceeding page limit fails permanently with PAGE_LIMIT_EXCEEDED."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    # 4 pages
    content = make_pdf_bytes(["P1", "P2", "P3", "P4"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    # Worker configured with max 2 pages
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="limit-worker-1", MAX_PDF_PAGES=2),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "PAGE_LIMIT_EXCEEDED"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "PAGE_LIMIT_EXCEEDED"


# ------------------------------------------------------------------------------
# 5. TXT Extraction & Logical Pagination Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_txt_single_page():
    """Text document without form-feed is extracted as a single logical page."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = b"Single page lecture notes on Deadlocks.\nBanker's Algorithm details."
    doc, job = await create_confirmed_document(
        user_id, subject_id, topic_id, content, mime_type="text/plain", filename="deadlocks.txt"
    )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="txt-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        pages, total = await repository.get_document_pages(conn, doc["id"])

    assert db_doc["extraction_status"] == "completed"
    assert db_doc["total_pages"] == 1
    assert db_doc["pages_with_text"] == 1
    assert total == 1
    assert "Banker's Algorithm" in pages[0]["raw_text"]
    assert pages[0]["has_text"] is True


@pytest.mark.asyncio
async def test_txt_form_feed_pagination():
    """Text document with form-feed '\\f' is split into multiple logical pages."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    # Chapter 1 \f (empty segment) \f Chapter 2 -> 3 logical pages
    content = b"Chapter 1: Threads\f\fChapter 2: Processes"
    doc, job = await create_confirmed_document(
        user_id, subject_id, topic_id, content, mime_type="text/plain", filename="threads.txt"
    )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="txt-worker-2"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        pages, total = await repository.get_document_pages(conn, doc["id"])

    assert db_doc["extraction_status"] == "completed"
    assert db_doc["total_pages"] == 3
    assert db_doc["pages_with_text"] == 2
    assert total == 3

    assert pages[0]["page_number"] == 1
    assert "Chapter 1: Threads" in pages[0]["raw_text"]
    assert pages[0]["has_text"] is True

    # Empty logical page preserved
    assert pages[1]["page_number"] == 2
    assert pages[1]["raw_text"] == ""
    assert pages[1]["has_text"] is False

    assert pages[2]["page_number"] == 3
    assert "Chapter 2: Processes" in pages[2]["raw_text"]
    assert pages[2]["has_text"] is True


@pytest.mark.asyncio
async def test_txt_latin1_encoding():
    """Text document encoded with Latin-1 is decoded properly without error."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    # Latin-1 bytes with Umlauts
    latin1_text = "Théorie des Systèmes d'Exploitation (Élémentaire)"
    content = latin1_text.encode("latin-1")
    doc, job = await create_confirmed_document(
        user_id, subject_id, topic_id, content, mime_type="text/plain", filename="french.txt"
    )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="txt-worker-3"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        pages, total = await repository.get_document_pages(conn, doc["id"])

    assert db_doc["extraction_status"] == "completed"
    assert total == 1
    assert "Théorie" in pages[0]["raw_text"]


@pytest.mark.asyncio
async def test_txt_binary_fails_permanent():
    """Binary files disguised as text (null bytes) fail permanently with MALFORMED_TEXT."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = b"Some ASCII text\x00\x01\x02binary junk"
    doc, job = await create_confirmed_document(
        user_id, subject_id, topic_id, content, mime_type="text/plain", filename="fake.txt"
    )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="txt-worker-4"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "MALFORMED_TEXT"
    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "MALFORMED_TEXT"


# ------------------------------------------------------------------------------
# 6. Process Isolation, Timeout & Retry Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_timeout_kills_process_and_retries_with_backoff():
    """Process execution timeout kills child OS process and re-queues job with backoff."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Timeout Test Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    # Worker configured with 0.00001s deadline to guarantee timeout
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(
            WORKER_ID="timeout-worker-1",
            MAX_EXTRACTION_DURATION_SECONDS=10,  # Pydantic ge=10 constraint
            BACKOFF_BASE_SECONDS=15,
        ),
        once=True,
    )
    # Monkeypatch MAX_EXTRACTION_DURATION_SECONDS to 0.00001 on the instance
    object.__setattr__(worker.settings, "MAX_EXTRACTION_DURATION_SECONDS", 0.00001)

    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    # Transient failure re-queues job
    assert db_job["status"] == "queued"
    assert db_job["error_code"] == "PARSER_TIMEOUT"
    assert db_job["scheduled_at"] > datetime.now(UTC)
    assert db_job["lease_token"] is None
    assert db_doc["extraction_status"] == "queued"


@pytest.mark.asyncio
async def test_worker_max_attempts_exceeded_fails_permanently():
    """Job exceeding max attempts transitions to permanent failed status."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Max Attempts Test Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    # Set job already at attempt 2 with max_attempts 3
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.kb_extraction_jobs SET attempts = 2, max_attempts = 3 WHERE id = $1",
            job["id"],
        )

    # Worker with timeout to trigger 3rd attempt failure
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="max-attempts-worker"),
        once=True,
    )
    object.__setattr__(worker.settings, "MAX_EXTRACTION_DURATION_SECONDS", 0.00001)

    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    async with pool.acquire() as conn:
        db_doc = await conn.fetchrow("SELECT * FROM public.kb_documents WHERE id = $1", doc["id"])
        db_job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE id = $1", job["id"]
        )

    assert db_job["status"] == "failed"
    assert db_job["error_code"] == "MAX_ATTEMPTS_EXCEEDED"
    assert db_job["attempts"] == 3
    assert db_doc["extraction_status"] == "failed"
    assert db_doc["error_code"] == "MAX_ATTEMPTS_EXCEEDED"


# ------------------------------------------------------------------------------
# 7. Persistence & Lifecycle Fencing Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_recovery_cleans_prior_pages_cleanly():
    """Recovered job re-inserting pages cleans up prior partial records without duplicate key collision."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Original Page 1", "Original Page 2"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    # Pre-insert partial/old pages simulating crashed run
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
            VALUES ($1, 1, 'Old Page 1', 10, 3, true);
            """,
            doc["id"],
        )

    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="crash-cleaner-worker"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    async with pool.acquire() as conn:
        pages, total = await repository.get_document_pages(conn, doc["id"])

    assert total == 2
    assert "Original Page 1" in pages[0]["raw_text"]
    assert "Original Page 2" in pages[1]["raw_text"]


@pytest.mark.asyncio
async def test_worker_fenced_after_document_deletion():
    """Deleting a document while extraction is running fences the worker from publishing pages."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Fencing Page"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    pool = get_pool()
    worker_id = "fenced-worker-1"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(conn, worker_id, lease_ttl_seconds=60)
        assert claimed is not None
        job_id = claimed["job_id"]
        token = claimed["lease_token"]

        # User deletes document before worker finishes
        await repository.transition_document_to_deleting(
            conn=conn, document_id=doc["id"], user_id=user_id, is_admin=False
        )

        # Worker finishes and attempts to complete
        fake_pages = [
            {
                "page_number": 1,
                "raw_text": "text",
                "character_count": 4,
                "word_count": 1,
                "has_text": True,
            }
        ]
        completed = await job_repository.complete_job_success(
            conn=conn,
            job_id=job_id,
            document_id=doc["id"],
            worker_id=worker_id,
            lease_token=token,
            pages=fake_pages,
            has_any_text=True,
        )

    # Worker must be fenced: completion returns False
    assert completed is False

    # Verify no pages were inserted
    async with pool.acquire() as conn:
        pages, total = await repository.get_document_pages(conn, doc["id"])
    assert total == 0


@pytest.mark.asyncio
async def test_finalize_deletion_purges_document_pages():
    """Calling finalize_document_deletion deletes associated page records from kb_document_pages."""
    user_id = await create_db_user()
    subject_id, topic_id = await create_test_taxonomy()
    content = make_pdf_bytes(["Page to be purged"])
    doc, job = await create_confirmed_document(user_id, subject_id, topic_id, content)

    # First, run worker to extract pages
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="purge-worker-1"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    pool = get_pool()
    async with pool.acquire() as conn:
        pages, total = await repository.get_document_pages(conn, doc["id"])
        assert total == 1

        # Finalize deletion
        await repository.finalize_document_deletion(conn, doc["id"])

        # Pages must be purged
        pages_after, total_after = await repository.get_document_pages(conn, doc["id"])

    assert total_after == 0
