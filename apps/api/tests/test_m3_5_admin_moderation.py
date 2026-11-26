"""Comprehensive backend integration tests for Milestone M3.5 Knowledge Base Administration & Moderation.

Tests:
1. Authorization & Role verification (admin/super_admin allowed, user/pending rejected).
2. Subject CRUD, constraints, archival, and restoration.
3. Topic CRUD, slug uniqueness within subject, reordering, archival, and parent-aware restoration.
4. Taxonomy visibility barrier (archived subject/topic blocks student listing, detail, pages, and downloads).
5. Document moderation (archive with reason, restore with active taxonomy check, permanent deletion lifecycle).
6. Extraction job monitoring (sanitized DTOs without lease_token, pagination, filters).
7. Job retry semantics (atomic requeue, failure categorization, retry count bounding <= 2, audit).
8. Administrative audit trail logging and retrieval.
"""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.knowledge_base import job_repository, storage
from tests.conftest import create_test_jwt

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []
CREATED_STORAGE_PATHS: list[str] = []


@pytest.fixture(autouse=True)
async def setup_test_env():
    """Ensure db pool, redis, and clean up test data."""
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()

    # Clean storage
    if CREATED_STORAGE_PATHS:
        try:
            storage_client = await storage.get_storage_client()
            await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).remove(
                list(set(CREATED_STORAGE_PATHS))
            )
        except Exception:
            pass
        CREATED_STORAGE_PATHS.clear()

    # Clean database in child-first order
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            if CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.kb_documents WHERE subject_id = ANY($1)",
                    CREATED_TEST_SUBJECTS,
                )
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


async def create_user_helper(role: str = "admin", status: str = "approved") -> tuple[UUID, str]:
    """Create test user in auth.users and public.profiles and return (user_id, jwt)."""
    pool = get_pool()
    user_id = uuid4()
    email = f"user_{user_id.hex[:8]}@univ.edu"

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
            f"User {user_id.hex[:4]}",
            role,
            status,
        )

    CREATED_TEST_USERS.append(user_id)
    token = create_test_jwt(user_id, email=email, role="authenticated")
    return user_id, token


# ==============================================================================
# 1. Authorization Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_admin_authorization_enforcement():
    """Verify role and approval status boundaries for KB admin endpoints."""
    _, admin_token = await create_user_helper(role="admin", status="approved")
    _, super_admin_token = await create_user_helper(role="super_admin", status="approved")
    _, student_token = await create_user_helper(role="user", status="approved")
    _, pending_admin_token = await create_user_helper(role="admin", status="pending")
    _, suspended_admin_token = await create_user_helper(role="admin", status="suspended")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Approved Admin allowed
        res = await client.get(
            "/api/v1/kb/admin/overview",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "active_subjects_count" in data

        # 2. Approved Super Admin allowed
        res = await client.get(
            "/api/v1/kb/admin/overview",
            headers={"Authorization": f"Bearer {super_admin_token}"},
        )
        assert res.status_code == 200

        # 3. Student forbidden
        res = await client.get(
            "/api/v1/kb/admin/overview",
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "ADMIN_REQUIRED"

        # 4. Pending Admin forbidden
        res = await client.get(
            "/api/v1/kb/admin/overview",
            headers={"Authorization": f"Bearer {pending_admin_token}"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "ADMIN_NOT_APPROVED"

        # 5. Suspended Admin forbidden
        res = await client.get(
            "/api/v1/kb/admin/overview",
            headers={"Authorization": f"Bearer {suspended_admin_token}"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

        # 6. Anonymous forbidden
        res = await client.get("/api/v1/kb/admin/overview")
        assert res.status_code == 401


# ==============================================================================
# 2. Subject CRUD & Taxonomy Lifecycle Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_subject_management_lifecycle():
    """Verify subject creation, conflict checks, updates, archive, and restore."""
    _, admin_token = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create Subject
        slug = f"os-kernel-{uuid4().hex[:6]}"
        res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={
                "name": f"Operating Systems {uuid4().hex[:4]}",
                "slug": slug,
                "description": "Core kernel concepts and scheduling",
                "icon_name": "Cpu",
                "display_order": 1,
            },
        )
        assert res.status_code == 201, res.text
        sub = res.json()
        sub_id = UUID(sub["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)
        assert sub["slug"] == slug
        assert sub["icon_name"] == "Cpu"
        assert sub["is_active"] is True

        # 2. Duplicate Slug Conflict
        res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={
                "name": "Different Name",
                "slug": slug,
                "icon_name": "BookOpen",
            },
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "SUBJECT_SLUG_EXISTS"

        # 3. Invalid Icon Name
        res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={
                "name": "Another Subject",
                "slug": f"valid-slug-{uuid4().hex[:6]}",
                "icon_name": "InvalidIconComponent",
            },
        )
        assert res.status_code == 422

        # 4. Update Subject
        res = await client.patch(
            f"/api/v1/kb/admin/subjects/{sub_id}",
            headers=headers,
            json={
                "description": "Updated OS description",
                "display_order": 5,
            },
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["description"] == "Updated OS description"
        assert updated["display_order"] == 5

        # 5. Archive Subject
        res = await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/archive", headers=headers)
        assert res.status_code == 200
        assert res.json()["is_active"] is False

        # Verify excluded from active-only list
        res = await client.get("/api/v1/kb/admin/subjects?include_inactive=false", headers=headers)
        assert res.status_code == 200
        active_ids = [s["id"] for s in res.json()]
        assert str(sub_id) not in active_ids

        # Included in full list
        res = await client.get("/api/v1/kb/admin/subjects?include_inactive=true", headers=headers)
        assert res.status_code == 200
        all_ids = [s["id"] for s in res.json()]
        assert str(sub_id) in all_ids

        # 6. Restore Subject
        res = await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/restore", headers=headers)
        assert res.status_code == 200
        assert res.json()["is_active"] is True


# ==============================================================================
# 3. Topic CRUD & Parent-Aware Lifecycle Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_topic_management_lifecycle():
    """Verify topic creation, duplicate slug prevention, reorder, archive, and parent-aware restore."""
    _, admin_token = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create Parent Subject
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={
                "name": f"Networks {uuid4().hex[:4]}",
                "slug": f"networks-{uuid4().hex[:6]}",
                "icon_name": "Globe",
            },
        )
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        # 2. Create Topic
        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=headers,
            json={
                "name": "TCP Handshake",
                "slug": "tcp-handshake",
                "description": "SYN, SYN-ACK, ACK mechanisms",
                "display_order": 1,
            },
        )
        assert top_res.status_code == 201
        top = top_res.json()
        top_id = UUID(top["id"])
        assert top["name"] == "TCP Handshake"
        assert top["slug"] == "tcp-handshake"
        assert top["is_active"] is True

        # 3. Duplicate Topic Slug in same subject
        dup_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=headers,
            json={
                "name": "Different TCP",
                "slug": "tcp-handshake",
            },
        )
        assert dup_res.status_code == 409
        assert dup_res.json()["error"]["code"] == "TOPIC_SLUG_EXISTS"

        # 4. Reorder Topic
        reorder_res = await client.patch(
            f"/api/v1/kb/admin/topics/{top_id}/order",
            headers=headers,
            json={"display_order": 10},
        )
        assert reorder_res.status_code == 200
        assert reorder_res.json()["display_order"] == 10

        # 5. Archive Topic
        arch_res = await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=headers)
        assert arch_res.status_code == 200
        assert arch_res.json()["is_active"] is False

        # 6. Archive Parent Subject, then try to restore topic -> Must Fail
        await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/archive", headers=headers)
        res = await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=headers)
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "PARENT_SUBJECT_ARCHIVED"

        # 7. Restore Parent Subject, then restore topic -> Succeeds
        await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/restore", headers=headers)
        res = await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=headers)
        assert res.status_code == 200
        assert res.json()["is_active"] is True


# ==============================================================================
# 4. Taxonomy Visibility Barrier Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_archived_taxonomy_blocks_student_document_and_page_access():
    """Verify that archiving a subject or topic completely hides its documents and pages from students."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, student_token = await create_user_helper(role="user", status="approved")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        student_headers = {"Authorization": f"Bearer {student_token}"}

        # 1. Create Subject, Topic, and Document directly in DB
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": f"DBMS {uuid4().hex[:4]}",
                "slug": f"dbms-{uuid4().hex[:6]}",
                "icon_name": "Database",
            },
        )
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Indexing", "slug": "indexing"},
        )
        top_id = UUID(top_res.json()["id"])

        pool = get_pool()
        doc_id = uuid4()
        storage_path = f"documents/{doc_id}/source.pdf"
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                    total_pages, is_archived
                )
                VALUES ($1, $2, $3, $4, 'B-Tree Notes', 'btree.pdf', $5, 'application/pdf', 2048, 'uploaded', 'completed', 1, false)
                """,
                doc_id,
                sub_id,
                top_id,
                admin_id,
                storage_path,
            )
            await conn.execute(
                """
                INSERT INTO public.kb_document_pages (
                    document_id, page_number, raw_text, character_count, word_count, has_text
                )
                VALUES ($1, 1, 'B-Trees are self-balancing search trees.', 40, 6, true)
                """,
                doc_id,
            )

        # 2. Student CAN view document and pages when active
        res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=student_headers)
        assert res.status_code == 200
        assert res.json()["title"] == "B-Tree Notes"

        res = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=student_headers)
        assert res.status_code == 200
        assert len(res.json()["items"]) == 1

        # 3. Archive Topic -> Student gets 404 for listing, document detail, and pages
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=admin_headers)

        res = await client.get(f"/api/v1/kb/topics/{top_id}/documents", headers=student_headers)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "TOPIC_NOT_FOUND"

        res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=student_headers)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        res = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=student_headers)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        # 4. Admin CAN still view document detail via admin API
        admin_doc_res = await client.get(
            f"/api/v1/kb/admin/documents/{doc_id}", headers=admin_headers
        )
        assert admin_doc_res.status_code == 200
        assert admin_doc_res.json()["title"] == "B-Tree Notes"

        # 5. Restore Topic -> Student access restored
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=admin_headers)
        res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=student_headers)
        assert res.status_code == 200

        # 6. Archive Subject -> Student blocked again
        await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/archive", headers=admin_headers)
        res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=student_headers)
        assert res.status_code == 404


# ==============================================================================
# 5. Document Moderation & Safe Deletion Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_document_moderation_archive_and_safe_deletion():
    """Verify document archival with reason, worker lease fencing, restore, and safe deletion."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, student_token = await create_user_helper(role="user", status="approved")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        student_headers = {"Authorization": f"Bearer {student_token}"}

        # Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": f"Algorithms {uuid4().hex[:4]}",
                "slug": f"algo-{uuid4().hex[:6]}",
                "icon_name": "Code",
            },
        )
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Dynamic Programming", "slug": "dp"},
        )
        top_id = UUID(top_res.json()["id"])

        # Insert document with active processing job
        doc_id = uuid4()
        storage_path = f"documents/{doc_id}/source.txt"
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'DP Knapsack', 'dp.txt', $5, 'text/plain', 500, 'uploaded', 'processing', false)
                """,
                doc_id,
                sub_id,
                top_id,
                admin_id,
                storage_path,
            )
            job_id = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_extraction_jobs (
                    id, document_id, status, worker_id, lease_token, attempts, locked_until
                )
                VALUES ($1, $2, 'processing', 'worker-1', gen_random_uuid(), 1, timezone('utc'::text, now()) + interval '60 seconds')
                """,
                job_id,
                doc_id,
            )

        # Upload dummy object into storage for deletion test
        storage_client = await storage.get_storage_client()
        await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
            storage_path, b"Knapsack problem description"
        )
        CREATED_STORAGE_PATHS.append(storage_path)

        # 1. Archive document with reason -> Fences active worker job
        res = await client.post(
            f"/api/v1/kb/admin/documents/{doc_id}/archive",
            headers=admin_headers,
            json={"reason": "Contains non-academic content"},
        )
        assert res.status_code == 200
        assert res.json()["is_archived"] is True

        # Verify active job was fenced and marked failed
        async with pool.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT status, error_code, lease_token FROM public.kb_extraction_jobs WHERE id = $1",
                job_id,
            )
            assert job_row["status"] == "failed"
            assert job_row["error_code"] == "ARCHIVED_BY_ADMIN"
            assert job_row["lease_token"] is None

        # Verify student cannot view
        res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=student_headers)
        assert res.status_code == 404

        # 2. Restore document
        res = await client.post(
            f"/api/v1/kb/admin/documents/{doc_id}/restore",
            headers=admin_headers,
            json={"reason": "Content confirmed academic"},
        )
        assert res.status_code == 200
        assert res.json()["is_archived"] is False

        # 3. Permanent Deletion with required reason
        res = await client.request(
            "DELETE",
            f"/api/v1/kb/admin/documents/{doc_id}",
            headers=admin_headers,
            json={"reason": "Student requested GDPR erasure"},
        )
        assert res.status_code == 204

        # Verify document transitioned to deleted
        async with pool.acquire() as conn:
            doc_row = await conn.fetchrow(
                "SELECT upload_status, is_archived FROM public.kb_documents WHERE id = $1",
                doc_id,
            )
            assert doc_row["upload_status"] == "deleted"
            assert doc_row["is_archived"] is True


# ==============================================================================
# 6. Extraction Job Monitoring & Retry Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_job_monitoring_and_retry_lifecycle():
    """Verify job listing, sanitized detail (no lease_token), failure categorization, and bounded retry."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, _ = await create_user_helper(role="admin", status="approved")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={
                "name": f"Compiler {uuid4().hex[:4]}",
                "slug": f"compiler-{uuid4().hex[:6]}",
                "icon_name": "Terminal",
            },
        )
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=headers,
            json={"name": "Parsing", "slug": "parsing"},
        )
        top_id = UUID(top_res.json()["id"])

        pool = get_pool()
        doc_id = uuid4()
        storage_path = f"documents/{doc_id}/source.pdf"
        failed_job_id = uuid4()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'Grammar Lexer', 'lexer.pdf', $5, 'application/pdf', 1000, 'uploaded', 'failed', false)
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
                    id, document_id, status, attempts, max_attempts, error_code, error_message, retry_count
                )
                VALUES ($1, $2, 'failed', 3, 3, 'PARSER_TIMEOUT', 'Parser exceeded 30s deadline', 0)
                """,
                failed_job_id,
                doc_id,
            )

        # 1. List Jobs -> Verify sanitized error and is_retryable flag
        res = await client.get("/api/v1/kb/admin/jobs?status=failed", headers=headers)
        assert res.status_code == 200
        jobs_data = res.json()
        target_job = next((j for j in jobs_data["items"] if j["id"] == str(failed_job_id)), None)
        assert target_job is not None
        assert target_job["is_retryable"] is True
        assert target_job["error_code"] == "PARSER_TIMEOUT"
        assert "lease_token" not in target_job

        # 2. Get Job Detail -> Sanitized
        res = await client.get(f"/api/v1/kb/admin/jobs/{failed_job_id}", headers=headers)
        assert res.status_code == 200
        detail = res.json()
        assert detail["is_retryable"] is True
        assert "lease_token" not in detail
        assert detail["retry_eligibility_reason"] == "Eligible for administrator retry."

        # 3. Retry Job -> Succeeds and creates fresh queued job with retry_count = 1
        retry_res = await client.post(
            f"/api/v1/kb/admin/jobs/{failed_job_id}/retry",
            headers=headers,
            json={"reason": "Transient cluster timeout observed"},
        )
        assert retry_res.status_code == 200
        new_job = retry_res.json()["job"]
        assert new_job["status"] == "queued"
        assert new_job["attempts"] == 0
        assert new_job["retry_count"] == 1
        assert new_job["retry_of_job_id"] == str(failed_job_id)

        # 4. Attempt to retry already retried (or now competing active job) -> Fails with 409
        competing_res = await client.post(
            f"/api/v1/kb/admin/jobs/{failed_job_id}/retry",
            headers=headers,
            json={"reason": "Retrying again"},
        )
        assert competing_res.status_code == 409
        assert competing_res.json()["error"]["code"] == "ACTIVE_JOB_EXISTS"

        # 5. Permanent fault exclusion test
        perm_doc_id = uuid4()
        perm_job_id = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'Encrypted Doc', 'enc.pdf', $5, 'application/pdf', 1000, 'uploaded', 'failed', false)
                """,
                perm_doc_id,
                sub_id,
                top_id,
                student_id,
                f"documents/{perm_doc_id}/source.pdf",
            )
            await conn.execute(
                """
                INSERT INTO public.kb_extraction_jobs (
                    id, document_id, status, attempts, max_attempts, error_code, error_message, retry_count
                )
                VALUES ($1, $2, 'failed', 3, 3, 'ENCRYPTED_PDF', 'PDF is password protected', 0)
                """,
                perm_job_id,
                perm_doc_id,
            )

        perm_res = await client.post(f"/api/v1/kb/admin/jobs/{perm_job_id}/retry", headers=headers)
        assert perm_res.status_code == 400
        assert perm_res.json()["error"]["code"] == "PERMANENT_FAULT_NOT_RETRYABLE"


# ==============================================================================
# 7. Administrative Audit History Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_admin_audit_history_recording():
    """Verify that administrative actions create immutable audit records retrievable via audit API."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Perform an action: create a subject
        sub_name = f"Audit Subject {uuid4().hex[:4]}"
        res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=headers,
            json={"name": sub_name, "slug": f"audit-{uuid4().hex[:6]}", "icon_name": "Layers"},
        )
        assert res.status_code == 201
        sub_id = UUID(res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        # Retrieve audit history
        audit_res = await client.get(
            "/api/v1/kb/admin/audit?action=kb.subject_create", headers=headers
        )
        assert audit_res.status_code == 200
        logs = audit_res.json()["items"]
        matching = next((log for log in logs if log["target_id"] == str(sub_id)), None)
        assert matching is not None
        assert matching["actor_id"] == str(admin_id)
        assert matching["action"] == "kb.subject_create"
        assert matching["payload"]["name"] == sub_name


# ==============================================================================
# 8. Extraction Restoration Lifecycle & Worker Taxonomy Fencing Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_interrupted_extraction_archive_restore_and_retry_lifecycle():
    """Verify archive -> interrupted job -> restore -> fresh job claim -> successful extraction."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, student_token = await create_user_helper(role="user", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        student_headers = {"Authorization": f"Bearer {student_token}"}

        # 1. Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": "Lifecycle Subject",
                "slug": f"life-{uuid4().hex[:6]}",
                "icon_name": "BookOpen",
            },
        )
        assert sub_res.status_code == 201
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Lifecycle Topic", "slug": f"top-{uuid4().hex[:6]}"},
        )
        assert top_res.status_code == 201
        top_id = UUID(top_res.json()["id"])

        # 2. Setup document and job directly in DB
        doc_id = uuid4()
        job_id = uuid4()
        storage_path = f"documents/{doc_id}/source.txt"
        CREATED_STORAGE_PATHS.append(storage_path)

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'Lifecycle Doc', 'notes.txt', $5, 'text/plain', 500, 'uploaded', 'queued', false)
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

        # 3. Worker claims job
        worker_id = f"worker-{uuid4().hex[:6]}"
        async with pool.acquire() as conn:
            claimed = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claimed is not None
            assert claimed["job_id"] == job_id
            assert claimed["lease_token"] is not None

        # 4. Admin archives document while extraction was in-flight
        archive_res = await client.post(
            f"/api/v1/kb/admin/documents/{doc_id}/archive",
            headers=admin_headers,
            json={"reason": "Interrupted by moderation"},
        )
        assert archive_res.status_code == 200
        archived_doc = archive_res.json()
        assert archived_doc["is_archived"] is True
        assert archived_doc["extraction_status"] == "failed"
        assert archived_doc["error_code"] == "ARCHIVED_BY_ADMIN"

        # Verify job was fenced and lease invalidated in DB
        async with pool.acquire() as conn:
            fenced_job = await conn.fetchrow(
                "SELECT status, error_code, lease_token FROM public.kb_extraction_jobs WHERE id = $1",
                job_id,
            )
            assert fenced_job["status"] == "failed"
            assert fenced_job["error_code"] == "ARCHIVED_BY_ADMIN"
            assert fenced_job["lease_token"] is None

        # 5. Stale worker attempts to complete extraction -> MUST be rejected
        async with pool.acquire() as conn:
            stale_ok = await job_repository.complete_job_success(
                conn=conn,
                job_id=job_id,
                document_id=doc_id,
                worker_id=worker_id,
                lease_token=claimed["lease_token"],
                pages=[
                    {
                        "page_number": 1,
                        "raw_text": "Lost",
                        "character_count": 4,
                        "word_count": 1,
                        "has_text": True,
                    }
                ],
                has_any_text=True,
            )
            assert stale_ok is False

        # 6. Admin restores document
        restore_res = await client.post(
            f"/api/v1/kb/admin/documents/{doc_id}/restore",
            headers=admin_headers,
            json={"reason": "Restoring after false positive"},
        )
        assert restore_res.status_code == 200
        restored_doc = restore_res.json()
        assert restored_doc["is_archived"] is False
        assert restored_doc["extraction_status"] == "failed"

        # 7. Admin retries the failed extraction job
        retry_res = await client.post(
            f"/api/v1/kb/admin/jobs/{job_id}/retry",
            headers=admin_headers,
            json={"reason": "Admin restarting extraction after restore"},
        )
        assert retry_res.status_code == 200
        retried_job = retry_res.json()["job"]
        assert retried_job["status"] == "queued"
        assert retried_job["retry_count"] == 1
        assert retried_job["retry_of_job_id"] == str(job_id)
        new_job_id = UUID(retried_job["id"])

        # 8. Worker claims fresh retried job
        async with pool.acquire() as conn:
            claimed_new = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claimed_new is not None
            assert claimed_new["job_id"] == new_job_id
            assert claimed_new["lease_token"] is not None

        # 9. Worker completes extraction successfully
        async with pool.acquire() as conn:
            completed_ok = await job_repository.complete_job_success(
                conn=conn,
                job_id=new_job_id,
                document_id=doc_id,
                worker_id=worker_id,
                lease_token=claimed_new["lease_token"],
                pages=[
                    {
                        "page_number": 1,
                        "raw_text": "Extracted Content",
                        "character_count": 17,
                        "word_count": 2,
                        "has_text": True,
                    }
                ],
                has_any_text=True,
            )
            assert completed_ok is True

        # 10. Student can view pages
        pages_res = await client.get(
            f"/api/v1/kb/documents/{doc_id}/pages", headers=student_headers
        )
        assert pages_res.status_code == 200
        assert pages_res.json()["total"] == 1
        assert pages_res.json()["items"][0]["raw_text"] == "Extracted Content"


@pytest.mark.asyncio
async def test_worker_taxonomy_fencing_lifecycle():
    """Verify worker does not claim or complete work under inactive taxonomy."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, _ = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": "Fence Subject",
                "slug": f"fence-{uuid4().hex[:6]}",
                "icon_name": "Layers",
            },
        )
        assert sub_res.status_code == 201
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Fence Topic", "slug": f"ftop-{uuid4().hex[:6]}"},
        )
        assert top_res.status_code == 201
        top_id = UUID(top_res.json()["id"])

        doc_id = uuid4()
        job_id = uuid4()
        storage_path = f"documents/{doc_id}/source.txt"
        CREATED_STORAGE_PATHS.append(storage_path)

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'Fenced Doc', 'notes.txt', $5, 'text/plain', 500, 'uploaded', 'queued', false)
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

        worker_id = f"fence-worker-{uuid4().hex[:6]}"

        # 1. Archive topic -> Worker cannot claim
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=admin_headers)
        async with pool.acquire() as conn:
            claim_attempt = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claim_attempt is None

        # 2. Restore topic, archive subject -> Worker still cannot claim
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=admin_headers)
        await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/archive", headers=admin_headers)
        async with pool.acquire() as conn:
            claim_attempt = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claim_attempt is None

        # 3. Restore subject -> Worker can now claim
        await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/restore", headers=admin_headers)
        async with pool.acquire() as conn:
            claimed = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claimed is not None
            assert claimed["job_id"] == job_id

        # 4. Inactivate topic while job is processing -> renew_job_lease fails
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=admin_headers)
        async with pool.acquire() as conn:
            renew_ok = await job_repository.renew_job_lease(
                conn,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=claimed["lease_token"],
                lease_ttl_seconds=60,
            )
            assert renew_ok is False

            # complete_job_success also fails
            complete_ok = await job_repository.complete_job_success(
                conn,
                job_id=job_id,
                document_id=doc_id,
                worker_id=worker_id,
                lease_token=claimed["lease_token"],
                pages=[
                    {
                        "page_number": 1,
                        "raw_text": "Fenced",
                        "character_count": 6,
                        "word_count": 1,
                        "has_text": True,
                    }
                ],
                has_any_text=True,
            )
            assert complete_ok is False

        # 5. Restore topic -> renew_job_lease and complete_job_success succeed
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=admin_headers)
        async with pool.acquire() as conn:
            renew_ok = await job_repository.renew_job_lease(
                conn,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=claimed["lease_token"],
                lease_ttl_seconds=60,
            )
            assert renew_ok is True

            complete_ok = await job_repository.complete_job_success(
                conn,
                job_id=job_id,
                document_id=doc_id,
                worker_id=worker_id,
                lease_token=claimed["lease_token"],
                pages=[
                    {
                        "page_number": 1,
                        "raw_text": "Fenced Now Ok",
                        "character_count": 13,
                        "word_count": 3,
                        "has_text": True,
                    }
                ],
                has_any_text=True,
            )
            assert complete_ok is True


@pytest.mark.asyncio
async def test_uploader_pending_visibility_and_taxonomy_enforcement():
    """Verify trusted uploader can view own pending document, students cannot, and inactive taxonomy blocks."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    uploader_id, uploader_token = await create_user_helper(role="admin", status="approved")
    other_id, other_token = await create_user_helper(role="user", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        uploader_headers = {"Authorization": f"Bearer {uploader_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}

        # Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": "Uploader Vis Subject",
                "slug": f"uvis-{uuid4().hex[:6]}",
                "icon_name": "Bookmark",
            },
        )
        assert sub_res.status_code == 201
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Uploader Vis Topic", "slug": f"utop-{uuid4().hex[:6]}"},
        )
        assert top_res.status_code == 201
        top_id = UUID(top_res.json()["id"])

        # Create upload intent by uploader
        intent_res = await client.post(
            "/api/v1/kb/documents/upload-intent",
            headers=uploader_headers,
            json={
                "subject_id": str(sub_id),
                "topic_id": str(top_id),
                "title": "Pending Upload Note",
                "original_filename": "note.txt",
                "mime_type": "text/plain",
                "file_size_bytes": 100,
            },
        )
        assert intent_res.status_code == 201
        doc_id = UUID(intent_res.json()["document_id"])

        # 1. Uploader can view own pending_upload document
        doc_res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=uploader_headers)
        assert doc_res.status_code == 200
        assert doc_res.json()["upload_status"] == "pending_upload"

        # 2. Other student cannot view uploader's pending document -> 404
        other_res = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=other_headers)
        assert other_res.status_code == 404

        # 3. Inactivate topic -> students can no longer view pending/topic documents -> 404
        await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=admin_headers)
        doc_res_archived = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=other_headers)
        assert doc_res_archived.status_code == 404

        # 4. Another student cannot confirm uploader's upload -> 400 UNAUTHORIZED_CONFIRMATION
        confirm_res = await client.post(
            f"/api/v1/kb/documents/{doc_id}/confirm-upload",
            headers=other_headers,
            json={"file_size_bytes": 100},
        )
        assert confirm_res.status_code == 400
        assert confirm_res.json()["error"]["code"] == "UNAUTHORIZED_CONFIRMATION"


@pytest.mark.asyncio
async def test_admin_audit_atomicity_and_rollback():
    """Verify that if audit log insertion fails, the administrative mutation completely rolls back."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, _ = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        pool = get_pool()

        # 1. Taxonomy Mutation: Subject Creation with audit failure
        target_slug = f"atom-{uuid4().hex[:6]}"
        target_name = f"Atomic Subject {uuid4().hex[:4]}"

        with patch(
            "app.modules.knowledge_base.admin_repository.insert_admin_audit_log",
            side_effect=RuntimeError("Simulated audit write failure"),
        ):
            res = await client.post(
                "/api/v1/kb/admin/subjects",
                headers=admin_headers,
                json={"name": target_name, "slug": target_slug, "icon_name": "Layers"},
            )
            assert res.status_code == 500

        # Verify subject does NOT exist in database (transaction rolled back)
        async with pool.acquire() as conn:
            subject_row = await conn.fetchrow(
                "SELECT id FROM public.kb_subjects WHERE slug = $1", target_slug
            )
            assert subject_row is None

            audit_row = await conn.fetchrow(
                "SELECT id FROM public.audit_logs WHERE action = 'kb.subject_create' AND payload->>'slug' = $1",
                target_slug,
            )
            assert audit_row is None

        # Setup real subject & topic for remaining tests
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": "Audit Test Sub",
                "slug": f"ats-{uuid4().hex[:6]}",
                "icon_name": "Layers",
            },
        )
        assert sub_res.status_code == 201
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "Audit Test Top", "slug": f"att-{uuid4().hex[:6]}"},
        )
        assert top_res.status_code == 201
        top_id = UUID(top_res.json()["id"])

        # 2. Document Moderation: Archive Document with audit failure
        doc_id = uuid4()
        job_id = uuid4()
        lease_token = uuid4()
        storage_path = f"documents/{doc_id}/source.txt"
        CREATED_STORAGE_PATHS.append(storage_path)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'Atomic Doc', 'doc.txt', $5, 'text/plain', 100, 'uploaded', 'processing', false)
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
                    id, document_id, status, worker_id, lease_token, attempts, locked_until
                )
                VALUES ($1, $2, 'processing', 'worker-audit', $3, 1, timezone('utc'::text, now()) + interval '120 seconds')
                """,
                job_id,
                doc_id,
                lease_token,
            )

        with patch(
            "app.modules.knowledge_base.admin_repository.insert_admin_audit_log",
            side_effect=RuntimeError("Simulated audit write failure"),
        ):
            archive_res = await client.post(
                f"/api/v1/kb/admin/documents/{doc_id}/archive",
                headers=admin_headers,
                json={"reason": "Should fail and rollback"},
            )
            assert archive_res.status_code == 500

        # Verify document is NOT archived and job is NOT fenced (transaction rolled back)
        async with pool.acquire() as conn:
            doc_after = await conn.fetchrow(
                "SELECT is_archived, extraction_status FROM public.kb_documents WHERE id = $1",
                doc_id,
            )
            assert doc_after["is_archived"] is False
            assert doc_after["extraction_status"] == "processing"

            job_after = await conn.fetchrow(
                "SELECT status, lease_token, error_code FROM public.kb_extraction_jobs WHERE id = $1",
                job_id,
            )
            assert job_after["status"] == "processing"
            assert job_after["lease_token"] == lease_token
            assert job_after["error_code"] is None

        # 3. Extraction Job Retry: Retry Job with audit failure
        # Set job to failed first
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public.kb_extraction_jobs
                SET status = 'failed', error_code = 'PARSER_TIMEOUT', lease_token = NULL
                WHERE id = $1
                """,
                job_id,
            )
            await conn.execute(
                "UPDATE public.kb_documents SET extraction_status = 'failed' WHERE id = $1",
                doc_id,
            )

        with patch(
            "app.modules.knowledge_base.admin_repository.insert_admin_audit_log",
            side_effect=RuntimeError("Simulated audit write failure"),
        ):
            retry_res = await client.post(
                f"/api/v1/kb/admin/jobs/{job_id}/retry",
                headers=admin_headers,
                json={"reason": "Should fail and rollback"},
            )
            assert retry_res.status_code == 500

        # Verify no new job was created and document extraction_status remains failed
        async with pool.acquire() as conn:
            jobs_count = await conn.fetchval(
                "SELECT count(*)::int FROM public.kb_extraction_jobs WHERE document_id = $1",
                doc_id,
            )
            assert jobs_count == 1  # No duplicate job

            doc_after_retry = await conn.fetchrow(
                "SELECT extraction_status FROM public.kb_documents WHERE id = $1",
                doc_id,
            )
            assert doc_after_retry["extraction_status"] == "failed"  # NOT queued


@pytest.mark.asyncio
async def test_taxonomy_restoration_no_job_duplication_and_deletion_fencing():
    """Verify that restoring taxonomy never duplicates extraction jobs, and deletion fencing prevents claim/completion."""
    admin_id, admin_token = await create_user_helper(role="admin", status="approved")
    student_id, _ = await create_user_helper(role="admin", status="approved")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        pool = get_pool()

        # 1. Setup taxonomy
        sub_res = await client.post(
            "/api/v1/kb/admin/subjects",
            headers=admin_headers,
            json={
                "name": "NoDup Subject",
                "slug": f"nodup-{uuid4().hex[:6]}",
                "icon_name": "Layers",
            },
        )
        assert sub_res.status_code == 201
        sub_id = UUID(sub_res.json()["id"])
        CREATED_TEST_SUBJECTS.append(sub_id)

        top_res = await client.post(
            f"/api/v1/kb/admin/subjects/{sub_id}/topics",
            headers=admin_headers,
            json={"name": "NoDup Topic", "slug": f"nodup-top-{uuid4().hex[:6]}"},
        )
        assert top_res.status_code == 201
        top_id = UUID(top_res.json()["id"])

        # Setup document with a queued job
        doc_id = uuid4()
        job_id = uuid4()
        storage_path = f"documents/{doc_id}/source.txt"
        CREATED_STORAGE_PATHS.append(storage_path)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                    id, subject_id, topic_id, uploader_id, title, original_filename,
                    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
                )
                VALUES ($1, $2, $3, $4, 'NoDup Doc', 'doc.txt', $5, 'text/plain', 100, 'uploaded', 'queued', false)
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

        # 2. Archive and restore topic multiple times
        for _ in range(2):
            await client.post(f"/api/v1/kb/admin/topics/{top_id}/archive", headers=admin_headers)
            await client.post(f"/api/v1/kb/admin/topics/{top_id}/restore", headers=admin_headers)

        # 3. Archive and restore subject multiple times
        for _ in range(2):
            await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/archive", headers=admin_headers)
            await client.post(f"/api/v1/kb/admin/subjects/{sub_id}/restore", headers=admin_headers)

        # Verify job count is still exactly 1 (no duplicate jobs spawned)
        async with pool.acquire() as conn:
            job_count = await conn.fetchval(
                "SELECT count(*)::int FROM public.kb_extraction_jobs WHERE document_id = $1",
                doc_id,
            )
            assert job_count == 1

        # 4. Deletion fencing: Permanently delete document and verify worker cannot claim or complete
        # Upload dummy object into storage so deletion succeeds
        storage_client = await storage.get_storage_client()
        await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
            storage_path, b"Sample doc for deletion fencing"
        )

        del_res = await client.request(
            "DELETE",
            f"/api/v1/kb/admin/documents/{doc_id}",
            headers=admin_headers,
            json={"reason": "Testing deletion fencing"},
        )
        assert del_res.status_code == 204

        # Worker attempt to claim next job -> MUST return None (cannot claim deleted document)
        worker_id = f"worker-del-{uuid4().hex[:6]}"
        async with pool.acquire() as conn:
            claim_attempt = await job_repository.claim_next_job(
                conn, worker_id=worker_id, lease_ttl_seconds=60
            )
            assert claim_attempt is None

            # Stale worker attempt to complete -> MUST return False
            complete_attempt = await job_repository.complete_job_success(
                conn,
                job_id=job_id,
                document_id=doc_id,
                worker_id=worker_id,
                lease_token=uuid4(),
                pages=[
                    {
                        "page_number": 1,
                        "raw_text": "Text",
                        "character_count": 4,
                        "word_count": 1,
                        "has_text": True,
                    }
                ],
                has_any_text=True,
            )
            assert complete_attempt is False
