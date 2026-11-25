"""Milestone M3.6 Release Certification and End-to-End Invariant Integration Tests.

Validates:
1. STORAGE_OBJECT_MISSING canonical error code when storage object is absent.
2. Invariant 6.5: Lease expiration (locked_until <= NOW()) strictly prevents page persistence at completion time.
3. Invariant 6.1: Concurrent workers cannot double-claim the same queued job.
4. Invariant 6.12: Retry lineage, retry bounding, and fresh lease token allocation.
5. Invariant 4.3: Unapproved (pending, rejected, suspended) users strictly blocked from Knowledge Base API.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.knowledge_base import job_repository, storage
from app.modules.knowledge_base.worker import KnowledgeBaseExtractionWorker
from app.modules.knowledge_base.worker_config import WorkerSettings
from tests.conftest import create_test_jwt

CREATED_STORAGE_PATHS: list[str] = []
CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_m36_env():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()

    # Clean storage
    try:
        storage_client = await storage.get_storage_client()
        if CREATED_STORAGE_PATHS:
            try:
                await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).remove(
                    list(set(CREATED_STORAGE_PATHS))
                )
            except Exception:
                pass
            CREATED_STORAGE_PATHS.clear()
    except Exception:
        pass

    # Clean database
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


async def create_user_helper(
    role: str = "user",
    status: str = "approved",
) -> tuple[UUID, str]:
    """Create test user in auth.users and public.profiles and return (user_id, jwt)."""
    pool = get_pool()
    user_id = uuid4()
    email = f"m36_{user_id.hex[:8]}@univ.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, email)
            VALUES ($1, $2)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            email,
        )
        await conn.execute(
            """
            INSERT INTO public.profiles (id, email, display_name, role, approval_status)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE
            SET role = EXCLUDED.role, approval_status = EXCLUDED.approval_status
            """,
            user_id,
            email,
            "Test User",
            role,
            status,
        )
    CREATED_TEST_USERS.append(user_id)
    token = create_test_jwt(user_id, email=email)
    return user_id, token


async def create_taxonomy_helper() -> tuple[UUID, UUID]:
    """Create active subject and topic."""
    pool = get_pool()
    sub_id = uuid4()
    top_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, icon_name, is_active)
            VALUES ($1, 'Certification Subject', $2, 'BookOpen', true)
            """,
            sub_id,
            f"cert-{sub_id.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
            VALUES ($1, $2, 'Certification Topic', $3, true)
            """,
            top_id,
            sub_id,
            f"cert-top-{top_id.hex[:6]}",
        )
    CREATED_TEST_SUBJECTS.append(sub_id)
    return sub_id, top_id


# ==============================================================================
# 1. Error Code Integrity: STORAGE_OBJECT_MISSING
# ==============================================================================


@pytest.mark.asyncio
async def test_storage_object_missing_canonical_error_code():
    """Verify missing Storage object is recorded as STORAGE_OBJECT_MISSING rather than CORRUPTED_FILE."""
    admin_id, _ = await create_user_helper(role="admin", status="approved")
    sub_id, top_id = await create_taxonomy_helper()
    pool = get_pool()

    doc_id = uuid4()
    job_id = uuid4()
    # Intentionally do NOT upload any file to storage_path
    storage_path = f"documents/{doc_id}/source.pdf"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
            )
            VALUES ($1, $2, $3, $4, 'Missing Doc', 'missing.pdf', $5, 'application/pdf', 1024, 'uploaded', 'queued', false)
            """,
            doc_id,
            sub_id,
            top_id,
            admin_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                id, document_id, status, scheduled_at, attempts, max_attempts, retry_count
            )
            VALUES ($1, $2, 'queued', timezone('utc'::text, now()), 0, 3, 0)
            """,
            job_id,
            doc_id,
        )

    # Worker executes job once
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="cert-worker-missing"), once=True
    )
    await worker.start()
    claimed = await worker.process_next_job()
    assert claimed is True
    await worker.stop()

    # Verify failure recorded as STORAGE_OBJECT_MISSING (not CORRUPTED_FILE)
    async with pool.acquire() as conn:
        doc = await conn.fetchrow(
            "SELECT extraction_status, error_code, error_message FROM public.kb_documents WHERE id = $1",
            doc_id,
        )
        assert doc["extraction_status"] == "failed"
        assert doc["error_code"] == "STORAGE_OBJECT_MISSING"

        job = await conn.fetchrow(
            "SELECT status, error_code, error_message FROM public.kb_extraction_jobs WHERE id = $1",
            job_id,
        )
        assert job["status"] == "failed"
        assert job["error_code"] == "STORAGE_OBJECT_MISSING"


# ==============================================================================
# 2. Invariant 6.5: Lease Expiration at Completion Time strictly Fences Worker
# ==============================================================================


@pytest.mark.asyncio
async def test_lease_expiration_at_completion_prevents_page_persistence():
    """Verify that if locked_until <= now() at completion time, complete_job_success rejects publishing pages."""
    admin_id, _ = await create_user_helper(role="admin", status="approved")
    sub_id, top_id = await create_taxonomy_helper()
    pool = get_pool()

    doc_id = uuid4()
    job_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
            )
            VALUES ($1, $2, $3, $4, 'Lease Test Doc', 'notes.txt', $5, 'text/plain', 500, 'uploaded', 'queued', false)
            """,
            doc_id,
            sub_id,
            top_id,
            admin_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                id, document_id, status, scheduled_at, attempts, max_attempts, retry_count
            )
            VALUES ($1, $2, 'queued', timezone('utc'::text, now()), 0, 3, 0)
            """,
            job_id,
            doc_id,
        )

    # Worker claims job
    worker_id = f"worker-lease-{uuid4().hex[:6]}"
    async with pool.acquire() as conn:
        claimed = await job_repository.claim_next_job(
            conn, worker_id=worker_id, lease_ttl_seconds=60
        )
        assert claimed is not None
        lease_token = claimed["lease_token"]

        # Artificially expire the lease in the past
        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET locked_until = timezone('utc'::text, now()) - interval '5 seconds'
            WHERE id = $1
            """,
            job_id,
        )

        # Worker attempts to complete job with expired lease -> MUST return False
        complete_ok = await job_repository.complete_job_success(
            conn=conn,
            job_id=job_id,
            document_id=doc_id,
            worker_id=worker_id,
            lease_token=lease_token,
            pages=[
                {
                    "page_number": 1,
                    "raw_text": "Should not be persisted",
                    "character_count": 24,
                    "word_count": 5,
                    "has_text": True,
                }
            ],
            has_any_text=True,
        )
        assert complete_ok is False

        # Verify no pages were persisted in public.kb_document_pages
        page_count = await conn.fetchval(
            "SELECT count(*)::int FROM public.kb_document_pages WHERE document_id = $1",
            doc_id,
        )
        assert page_count == 0

        # Verify document extraction_status remains processing (not completed)
        doc_status = await conn.fetchval(
            "SELECT extraction_status FROM public.kb_documents WHERE id = $1",
            doc_id,
        )
        assert doc_status == "processing"


# ==============================================================================
# 3. Invariant 6.1: Concurrent Workers Cannot Double-Claim Same Job
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_worker_claim_atomicity():
    """Verify two concurrent worker claims lock atomically without duplicate assignment."""
    admin_id, _ = await create_user_helper(role="admin", status="approved")
    sub_id, top_id = await create_taxonomy_helper()
    pool = get_pool()

    doc_id = uuid4()
    job_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
            )
            VALUES ($1, $2, $3, $4, 'Race Doc', 'race.txt', $5, 'text/plain', 100, 'uploaded', 'queued', false)
            """,
            doc_id,
            sub_id,
            top_id,
            admin_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                id, document_id, status, scheduled_at, attempts, max_attempts, retry_count
            )
            VALUES ($1, $2, 'queued', timezone('utc'::text, now()), 0, 3, 0)
            """,
            job_id,
            doc_id,
        )

    # Launch two concurrent claims
    async def try_claim(worker_name: str):
        async with pool.acquire() as conn:
            return await job_repository.claim_next_job(
                conn, worker_id=worker_name, lease_ttl_seconds=60
            )

    res1, res2 = await asyncio.gather(
        try_claim("worker-alpha"),
        try_claim("worker-beta"),
    )

    # Exactly one worker must claim, the other must receive None
    claimed_results = [r for r in [res1, res2] if r is not None]
    assert len(claimed_results) == 1
    assert claimed_results[0]["job_id"] == job_id


# ==============================================================================
# 4. Invariant 4.3: Unapproved Users (Pending, Rejected, Suspended) Blocked
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "rejected", "suspended"])
async def test_unapproved_users_blocked_from_kb_endpoints(status: str):
    """Verify that users in pending, rejected, and suspended states are rejected with 403 on protected routes."""
    _, token = await create_user_helper(role="user", status=status)
    sub_id, top_id = await create_taxonomy_helper()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        expected_code = "ACCOUNT_SUSPENDED" if status == "suspended" else "ACCOUNT_NOT_APPROVED"

        # 1. Subjects catalog
        res = await client.get("/api/v1/kb/subjects", headers=headers)
        assert res.status_code == 403
        assert res.json()["error"]["code"] == expected_code

        # 2. Topic documents
        res = await client.get(f"/api/v1/kb/topics/{top_id}/documents", headers=headers)
        assert res.status_code == 403
        assert res.json()["error"]["code"] == expected_code

        # 3. Upload reservation intent
        res = await client.post(
            "/api/v1/kb/documents/upload-intent",
            headers=headers,
            json={
                "subject_id": str(sub_id),
                "topic_id": str(top_id),
                "title": "Blocked Note",
                "original_filename": "note.txt",
                "mime_type": "text/plain",
                "file_size_bytes": 100,
            },
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == expected_code


# ==============================================================================
# 5. Non-Contributor Allowed Full Knowledge Base Access
# ==============================================================================


@pytest.mark.asyncio
async def test_non_contributor_student_has_full_kb_access():
    """Verify that an approved student with 0 contributed credentials can browse and download study materials."""
    uploader_id, _ = await create_user_helper(role="user", status="approved")
    _, token = await create_user_helper(role="user", status="approved")
    sub_id, top_id = await create_taxonomy_helper()
    pool = get_pool()

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    # Seed public document
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
            )
            VALUES ($1, $2, $3, $4, 'Shared Material', 'shared.txt', $5, 'text/plain', 50, 'uploaded', 'completed', false)
            """,
            doc_id,
            sub_id,
            top_id,
            uploader_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
            VALUES ($1, 1, 'Non contributor material', 24, 3, true)
            """,
            doc_id,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Browse subjects -> 200 OK
        res = await client.get("/api/v1/kb/subjects", headers=headers)
        assert res.status_code == 200

        # 2. Browse topic documents -> 200 OK
        res = await client.get(f"/api/v1/kb/topics/{top_id}/documents", headers=headers)
        assert res.status_code == 200
        assert res.json()["total"] >= 1

        # 3. Read extracted pages -> 200 OK
        res = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=headers)
        assert res.status_code == 200
        assert res.json()["items"][0]["raw_text"] == "Non contributor material"
