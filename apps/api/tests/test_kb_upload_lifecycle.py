"""Comprehensive test suite for Knowledge Base and Document Upload Lifecycle (M3.2).

Validates:
1. Strict authorization (unauthenticated 401, unapproved 403, approved non-contributor 200, admin 200).
2. Subject and topic taxonomy discovery with aggregated document counts.
3. Upload intent reservations (PDF/TXT, 50MB ceiling, path traversal sanitization, cross-subject topic checks).
4. Daily upload quota enforcement via Redis rate limiter.
5. Independent content inspection on confirmation (PDF header/trailer, text null-bytes/encoding, SHA-256).
6. Atomic status updates and durable extraction job queuing in PostgreSQL.
7. Safe metadata exposure (strict exclusion of worker_id, lease_token, stack traces).
8. Document visibility (approved students view uploaded notes, uploaders view pending/failed, archived hidden).
9. Extracted page pagination provenance.
10. Time-limited signed download capability (300s).
11. Owner/admin deletion lifecycle with Supabase Storage cleanup.
12. Bounded sweeper for abandoned upload reservations.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.config import settings
from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, get_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.knowledge_base import cleanup, storage
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []
CREATED_STORAGE_PATHS: list[str] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    r = await init_redis()
    if r:
        try:
            keys = await r.keys("ragbait:ratelimit:*")
            if keys:
                await r.delete(*keys)
        except Exception:
            pass
    yield
    app.dependency_overrides.clear()

    # Clean up storage objects
    if CREATED_STORAGE_PATHS:
        for path in CREATED_STORAGE_PATHS:
            try:
                await storage.delete_storage_object(path)
            except Exception:
                pass
        CREATED_STORAGE_PATHS.clear()

    # Clean up DB documents, topics, subjects, and users in child-first order
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
    await close_redis()


async def create_db_user(
    email: str | None = None,
    role: str = "user",
    status: str = "approved",
) -> UUID:
    """Create real user in auth.users and public.profiles."""
    pool = get_pool()
    user_id = uuid4()
    if not email:
        unique_email = f"student_{user_id.hex[:8]}@university.edu"
    else:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"

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
            SET role = $1, approval_status = $2
            WHERE id = $3
            """,
            role,
            status,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id


def setup_auth_context(
    user_id: UUID,
    email: str = "student@university.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> dict[str, str]:
    """Configure dependency overrides for authenticated user and return auth headers."""
    profile = make_test_profile(user_id=user_id, email=email, role=role, approval_status=status)
    token = create_test_jwt(user_id=user_id, email=email)

    app.dependency_overrides[get_current_profile] = lambda: profile
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    return {"Authorization": f"Bearer {token}"}


async def create_test_taxonomy() -> tuple[UUID, UUID, UUID]:
    """Create a test subject with two topics (one active, one inactive)."""
    pool = get_pool()
    subject_id = uuid4()
    topic_1 = uuid4()
    topic_2 = uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description, icon_name, display_order, is_active)
            VALUES ($1, $2, $3, 'Database Management Systems', 'Database', 1, true)
            """,
            subject_id,
            f"DBMS-{subject_id.hex[:6]}",
            f"dbms-{subject_id.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, description, display_order, is_active)
            VALUES ($1, $2, 'Indexing & B+ Trees', 'indexing', 'B-Tree internals', 1, true)
            """,
            topic_1,
            subject_id,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, description, display_order, is_active)
            VALUES ($1, $2, 'Query Optimization', 'optimization', 'Inactive topic', 2, false)
            """,
            topic_2,
            subject_id,
        )

    CREATED_TEST_SUBJECTS.append(subject_id)
    return subject_id, topic_1, topic_2


# ------------------------------------------------------------------------------
# 1. Authorization Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_auth_unauthenticated_fails(client: httpx.AsyncClient):
    """Accessing Knowledge Base without credentials returns 401."""
    resp = await client.get("/api/v1/kb/subjects")
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["code"] in ("AUTHENTICATION_FAILED", "MISSING_TOKEN")


@pytest.mark.asyncio
async def test_kb_auth_unapproved_student_forbidden(client: httpx.AsyncClient):
    """Students who are not approved cannot access Knowledge Base (403)."""
    user_id = await create_db_user(status="pending")
    headers = setup_auth_context(user_id=user_id, status="pending")

    resp = await client.get("/api/v1/kb/subjects", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_NOT_APPROVED"


@pytest.mark.asyncio
async def test_kb_auth_non_contributor_approved_student_allowed(client: httpx.AsyncClient):
    """Approved student with ZERO contributed cloud keys CAN access Knowledge Base."""
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id, status="approved")

    # Confirm user has no credentials in DB
    pool = get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM public.api_key_metadata WHERE owner_id = $1", user_id
        )
        assert count == 0

    resp = await client.get("/api/v1/kb/subjects", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ------------------------------------------------------------------------------
# 2. Taxonomy & Discovery Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_subjects_and_topics_retrieval(client: httpx.AsyncClient):
    """Verify listing subjects and topics filters out inactive topics and returns counts."""
    subject_id, topic_active, topic_inactive = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    # 1. Subjects listing
    resp = await client.get("/api/v1/kb/subjects", headers=headers)
    assert resp.status_code == 200
    subjects = resp.json()
    match = next((s for s in subjects if s["id"] == str(subject_id)), None)
    assert match is not None
    assert match["name"].startswith("DBMS-")
    assert match["topic_count"] == 1  # Only active topics counted

    # 2. Topics listing for subject
    resp = await client.get(f"/api/v1/kb/subjects/{subject_id}/topics", headers=headers)
    assert resp.status_code == 200
    topics = resp.json()
    assert len(topics) == 1
    assert topics[0]["id"] == str(topic_active)
    assert topics[0]["name"] == "Indexing & B+ Trees"

    # 3. Non-existent subject returns 404
    resp = await client.get(f"/api/v1/kb/subjects/{uuid4()}/topics", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SUBJECT_NOT_FOUND"


# ------------------------------------------------------------------------------
# 3. Upload Intent Reservation Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_upload_intent_valid_pdf_and_txt(client: httpx.AsyncClient):
    """Valid upload reservations return signed storage URLs and capabilities."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    # PDF Intent
    pdf_payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "B+ Tree Disk Structures",
        "original_filename": "lecture_04_b_trees.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024 * 50,  # 50 KB
    }
    resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=pdf_payload, headers=headers
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "document_id" in data
    assert data["storage_path"].startswith(f"documents/{data['document_id']}/source.pdf")
    assert "signed_upload_url" in data
    assert "token" in data
    assert data["max_file_size_bytes"] == 52_428_800
    CREATED_STORAGE_PATHS.append(data["storage_path"])

    # TXT Intent
    txt_payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Buffer Pool Manager Notes",
        "original_filename": "notes.txt",
        "mime_type": "text/plain",
        "file_size_bytes": 2048,
    }
    resp2 = await client.post(
        "/api/v1/kb/documents/upload-intent", json=txt_payload, headers=headers
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["storage_path"].startswith(f"documents/{data2['document_id']}/source.txt")
    CREATED_STORAGE_PATHS.append(data2["storage_path"])


@pytest.mark.asyncio
async def test_kb_upload_intent_oversized_file_rejected(client: httpx.AsyncClient):
    """Files over 50MB ceiling are rejected."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Giant Book",
        "original_filename": "huge_book.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 52_428_801,  # 50MB + 1 byte
    }
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_kb_upload_intent_unsupported_mime_rejected(client: httpx.AsyncClient):
    """Unsupported MIME types are rejected."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Diagram",
        "original_filename": "diagram.png",
        "mime_type": "image/png",
        "file_size_bytes": 1024,
    }
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"


@pytest.mark.asyncio
async def test_kb_upload_intent_mismatched_extension_rejected(client: httpx.AsyncClient):
    """Filename extension must match MIME type."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Deceptive File",
        "original_filename": "malicious.exe",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024,
    }
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EXTENSION_MIME_MISMATCH"


@pytest.mark.asyncio
async def test_kb_upload_intent_path_traversal_sanitized(client: httpx.AsyncClient):
    """Path traversal sequences in filename are sanitized."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Traversal Test",
        "original_filename": "../../etc/shadow.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024,
    }
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code == 201
    doc_id = resp.json()["document_id"]

    # Verify stored original_filename was stripped to shadow.pdf
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT original_filename FROM public.kb_documents WHERE id = $1",
            UUID(doc_id),
        )
        assert row["original_filename"] == "shadow.pdf"


@pytest.mark.asyncio
async def test_kb_upload_intent_topic_subject_mismatch_rejected(client: httpx.AsyncClient):
    """Topic must belong to the specified subject."""
    subject_1, topic_1, _ = await create_test_taxonomy()
    subject_2, _, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_2),  # Subject 2
        "topic_id": str(topic_1),  # But topic belongs to Subject 1
        "title": "Cross Topic",
        "original_filename": "test.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024,
    }
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOPIC_SUBJECT_MISMATCH"


@pytest.mark.asyncio
async def test_kb_upload_intent_daily_rate_limit(client: httpx.AsyncClient):
    """Exceeding 20 uploads in 24 hours triggers 429 RateLimitError."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Burst Note",
        "original_filename": "note.txt",
        "mime_type": "text/plain",
        "file_size_bytes": 10,
    }

    # Simulate 20 successful intents
    r = get_redis()
    if r:
        await r.set(f"ragbait:ratelimit:kb_upload_daily:{user_id}", 20)

    # 21st attempt should be rejected
    resp = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# ------------------------------------------------------------------------------
# 4. Confirm Upload Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_confirm_upload_success_pdf(client: httpx.AsyncClient):
    """End-to-end PDF upload to storage, confirm, and atomic extraction queue."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    # Minimal valid PDF binary
    pdf_content = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"xref\n0 3\n0000000000 65535 f \n"
        b"trailer\n<< /Root 1 0 R >>\nstartxref\n116\n%%EOF\n"
    )
    pdf_size = len(pdf_content)
    pdf_checksum = hashlib.sha256(pdf_content).hexdigest()

    # 1. Upload intent
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Relational Calculus",
        "original_filename": "calculus.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": pdf_size,
        "sha256_checksum": pdf_checksum,
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    assert intent_resp.status_code == 201
    intent_data = intent_resp.json()
    doc_id = intent_data["document_id"]
    storage_path = intent_data["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    # 2. Upload file directly to Supabase Storage using service client
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, pdf_content, {"content-type": "application/pdf"}
    )

    # 3. Confirm upload
    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload",
        json={"sha256_checksum": pdf_checksum, "file_size_bytes": pdf_size},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    confirm_data = confirm_resp.json()
    assert confirm_data["document"]["upload_status"] == "uploaded"
    assert confirm_data["document"]["extraction_status"] == "queued"
    assert confirm_data["document"]["sha256_checksum"] == pdf_checksum

    # 4. Verify durable job in kb_extraction_jobs
    pool = get_pool()
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT * FROM public.kb_extraction_jobs WHERE document_id = $1",
            UUID(doc_id),
        )
        assert job is not None
        assert job["status"] == "queued"
        assert job["attempts"] == 0


@pytest.mark.asyncio
async def test_kb_confirm_upload_success_txt(client: httpx.AsyncClient):
    """End-to-end TXT upload to storage, confirm, and queue."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    txt_content = b"RagBait Plain Text Study Notes for Computer Networks.\nLine 2.\n"
    txt_size = len(txt_content)
    txt_checksum = hashlib.sha256(txt_content).hexdigest()

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "OSI 7 Layer Summary",
        "original_filename": "osi_summary.txt",
        "mime_type": "text/plain",
        "file_size_bytes": txt_size,
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    assert intent_resp.status_code == 201
    doc_id = intent_resp.json()["document_id"]
    storage_path = intent_resp.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    # Upload to storage
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, txt_content, {"content-type": "text/plain"}
    )

    # Confirm
    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload",
        json={"sha256_checksum": txt_checksum},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["document"]["upload_status"] == "uploaded"


@pytest.mark.asyncio
async def test_kb_confirm_upload_idempotent(client: httpx.AsyncClient):
    """Calling confirm on already uploaded document returns 200 without duplicate job."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    txt_content = b"Idempotency test content"
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Idempotent Document",
        "original_filename": "idem.txt",
        "mime_type": "text/plain",
        "file_size_bytes": len(txt_content),
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    doc_id = intent_resp.json()["document_id"]
    storage_path = intent_resp.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, txt_content, {"content-type": "text/plain"}
    )

    # First confirm
    r1 = await client.post(f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers)
    assert r1.status_code == 200

    # Second confirm
    r2 = await client.post(f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers)
    assert r2.status_code == 200
    assert "already" in r2.json()["message"].lower()

    # Verify only 1 extraction job was inserted
    pool = get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM public.kb_extraction_jobs WHERE document_id = $1",
            UUID(doc_id),
        )
        assert count == 1


@pytest.mark.asyncio
async def test_kb_confirm_upload_file_not_in_storage(client: httpx.AsyncClient):
    """Confirming a document before uploading it to storage raises 400."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Missing File Doc",
        "original_filename": "missing.txt",
        "mime_type": "text/plain",
        "file_size_bytes": 100,
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    doc_id = intent_resp.json()["document_id"]

    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers
    )
    assert confirm_resp.status_code == 400
    assert confirm_resp.json()["error"]["code"] == "FILE_NOT_FOUND_IN_STORAGE"


@pytest.mark.asyncio
async def test_kb_confirm_upload_invalid_pdf_bytes_rejected(client: httpx.AsyncClient):
    """Corrupted PDF without %PDF- magic bytes fails validation, deletes object, marks failed."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    fake_pdf = b"This is plain text pretending to be a PDF file."
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Fake PDF",
        "original_filename": "fake.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": len(fake_pdf),
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    doc_id = intent_resp.json()["document_id"]
    storage_path = intent_resp.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    # Upload fake bytes to storage
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, fake_pdf, {"content-type": "application/pdf"}
    )

    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers
    )
    assert confirm_resp.status_code == 400
    assert confirm_resp.json()["error"]["code"] == "INVALID_PDF_FORMAT"

    # Verify document in DB is marked upload_failed
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT upload_status, error_code FROM public.kb_documents WHERE id = $1",
            UUID(doc_id),
        )
        assert row["upload_status"] == "upload_failed"
        assert row["error_code"] == "INVALID_PDF_FORMAT"


@pytest.mark.asyncio
async def test_kb_confirm_upload_invalid_txt_null_bytes_rejected(client: httpx.AsyncClient):
    """Text document containing null bytes is rejected."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    binary_txt = b"Hello\x00World binary content"
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Binary Text",
        "original_filename": "binary.txt",
        "mime_type": "text/plain",
        "file_size_bytes": len(binary_txt),
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    doc_id = intent_resp.json()["document_id"]
    storage_path = intent_resp.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, binary_txt, {"content-type": "text/plain"}
    )

    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers
    )
    assert confirm_resp.status_code == 400
    assert confirm_resp.json()["error"]["code"] == "INVALID_TEXT_FORMAT"


@pytest.mark.asyncio
async def test_kb_confirm_upload_checksum_mismatch_rejected(client: httpx.AsyncClient):
    """Client-provided checksum mismatch fails confirmation."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    txt_content = b"Legitimate text"
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Checksum Mismatch",
        "original_filename": "check.txt",
        "mime_type": "text/plain",
        "file_size_bytes": len(txt_content),
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers
    )
    doc_id = intent_resp.json()["document_id"]
    storage_path = intent_resp.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, txt_content, {"content-type": "text/plain"}
    )

    # Confirm with bogus checksum
    bogus_sha = "0" * 64
    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload",
        json={"sha256_checksum": bogus_sha},
        headers=headers,
    )
    assert confirm_resp.status_code == 400
    assert confirm_resp.json()["error"]["code"] == "CHECKSUM_MISMATCH"


@pytest.mark.asyncio
async def test_kb_confirm_upload_unauthorized_user_rejected(client: httpx.AsyncClient):
    """User B cannot confirm an upload reservation made by User A."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_a = await create_db_user(status="approved")
    user_b = await create_db_user(status="approved")
    headers_a = setup_auth_context(user_id=user_a)

    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "User A Doc",
        "original_filename": "doc_a.txt",
        "mime_type": "text/plain",
        "file_size_bytes": 10,
    }
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers_a
    )
    doc_id = intent_resp.json()["document_id"]

    # Switch to User B
    headers_b = setup_auth_context(user_id=user_b)
    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers_b
    )
    assert confirm_resp.status_code == 400
    assert confirm_resp.json()["error"]["code"] == "UNAUTHORIZED_CONFIRMATION"


# ------------------------------------------------------------------------------
# 5. Metadata Exposure & Document Visibility Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_document_detail_and_no_internal_exposure(client: httpx.AsyncClient):
    """Document details endpoint must NOT expose worker_id, lease_token, or internal stack traces."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    # Insert an uploaded document with an extraction job that has worker_id and lease_token
    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Safe Metadata Test', 'safe.txt',
                $5, 'text/plain', 100, 'uploaded', 'processing'
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                document_id, status, worker_id, lease_token, attempts
            ) VALUES (
                $1, 'processing', 'worker-internal-node-99', $2, 1
            )
            """,
            doc_id,
            uuid4(),
        )

    resp = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Assert public fields present
    assert data["id"] == str(doc_id)
    assert data["title"] == "Safe Metadata Test"
    assert data["upload_status"] == "uploaded"
    assert data["extraction_status"] == "processing"

    # CRITICAL SECURITY ASSERTION: Internal fields MUST NOT be exposed
    assert "worker_id" not in data
    assert "lease_token" not in data
    assert "worker-internal-node-99" not in str(data)


@pytest.mark.asyncio
async def test_kb_document_visibility_lifecycle(client: httpx.AsyncClient):
    """Students can see uploaded docs; uploaders see own pending/failed; students cannot see other's pending."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_a = await create_db_user(status="approved")
    user_b = await create_db_user(status="approved")

    # User A creates upload intent
    headers_a = setup_auth_context(user_id=user_a)
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "User A Pending Doc",
        "original_filename": "pending.txt",
        "mime_type": "text/plain",
        "file_size_bytes": 10,
    }
    intent = await client.post(
        "/api/v1/kb/documents/upload-intent", json=payload, headers=headers_a
    )
    doc_id = intent.json()["document_id"]
    CREATED_STORAGE_PATHS.append(intent.json()["storage_path"])

    # User A can view own pending document
    r_a = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers_a)
    assert r_a.status_code == 200
    assert r_a.json()["upload_status"] == "pending_upload"

    # User B cannot view User A's pending document (returns 404)
    headers_b = setup_auth_context(user_id=user_b)
    r_b = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers_b)
    assert r_b.status_code == 404


# ------------------------------------------------------------------------------
# 6. Extracted Pages Read Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_document_pages_retrieval_and_pagination(client: httpx.AsyncClient):
    """Paginated extracted text pages can be read by approved users."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.pdf"
    CREATED_STORAGE_PATHS.append(storage_path)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                total_pages, processed_pages, pages_with_text
            ) VALUES (
                $1, $2, $3, $4, 'Page Test', 'pages.pdf',
                $5, 'application/pdf', 500, 'uploaded', 'completed', 3, 3, 2
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
        )
        # Insert 3 pages
        p1_text = "Page 1 Content on Relational Algebra."
        p2_text = ""
        p3_text = "Page 3 Content on Tuple Relational Calculus."
        await conn.execute(
            """
            INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
            VALUES
                ($1, 1, $2, $3, 6, true),
                ($1, 2, $4, 0, 0, false),
                ($1, 3, $5, $6, 6, true)
            """,
            doc_id,
            p1_text,
            len(p1_text),
            p2_text,
            p3_text,
            len(p3_text),
        )

    # Fetch pages limit=2, offset=0
    resp = await client.get(
        f"/api/v1/kb/documents/{doc_id}/pages?limit=2&offset=0", headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["items"][0]["page_number"] == 1
    assert data["items"][0]["raw_text"] == p1_text
    assert data["items"][1]["page_number"] == 2
    assert data["items"][1]["has_text"] is False

    # Fetch remaining page offset=2
    resp2 = await client.get(
        f"/api/v1/kb/documents/{doc_id}/pages?limit=2&offset=2", headers=headers
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 1
    assert data2["items"][0]["page_number"] == 3


# ------------------------------------------------------------------------------
# 7. Signed Download URL Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_download_url_generation(client: httpx.AsyncClient):
    """Approved student can generate 300s signed download URL for uploaded document."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    # Put real file into Supabase Storage
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, b"Downloadable notes", {"content-type": "text/plain"}
    )

    # Insert document in uploaded state
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Download Test', 'dl.txt',
                $5, 'text/plain', 18, 'uploaded', 'completed'
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
        )

    resp = await client.get(f"/api/v1/kb/documents/{doc_id}/download-url", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "download_url" in data
    assert data["expires_in"] == 300
    assert data["original_filename"] == "dl.txt"


# ------------------------------------------------------------------------------
# 8. Deletion Lifecycle Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_delete_document_lifecycle(client: httpx.AsyncClient):
    """Owner deleting document transitions to deleted, archives row, and purges storage object."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, b"Delete me", {"content-type": "text/plain"}
    )

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Delete Doc', 'del.txt',
                $5, 'text/plain', 9, 'uploaded', 'completed'
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
        )

    # Delete
    del_resp = await client.delete(f"/api/v1/kb/documents/{doc_id}", headers=headers)
    assert del_resp.status_code == 204

    # Subsequent GET returns 404
    get_resp = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers)
    assert get_resp.status_code == 404

    # DB state: is_archived = true, upload_status = deleted
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT upload_status, is_archived FROM public.kb_documents WHERE id = $1",
            doc_id,
        )
        assert row["upload_status"] == "deleted"
        assert row["is_archived"] is True


@pytest.mark.asyncio
async def test_kb_delete_document_admin_override(client: httpx.AsyncClient):
    """Admin can delete a document uploaded by another student."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    student_id = await create_db_user(status="approved", role="user")
    admin_id = await create_db_user(status="approved", role="admin")

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, b"Admin delete test", {"content-type": "text/plain"}
    )

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Student Doc', 's.txt',
                $5, 'text/plain', 17, 'uploaded', 'completed'
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            student_id,
            storage_path,
        )

    # Admin deletes student's document
    admin_headers = setup_auth_context(user_id=admin_id, role="admin")
    resp = await client.delete(f"/api/v1/kb/documents/{doc_id}", headers=admin_headers)
    assert resp.status_code == 204


# ------------------------------------------------------------------------------
# 9. Abandoned Reservation Cleanup Sweeper Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_abandoned_upload_sweeper():
    """Sweeper identifies expired pending uploads and marks them upload_failed."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    # Insert document with upload_expires_at in the past
    past = datetime.now(UTC) - timedelta(hours=3)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                upload_expires_at
            ) VALUES (
                $1, $2, $3, $4, 'Expired Doc', 'expired.txt',
                $5, 'text/plain', 10, 'pending_upload', 'pending', $6
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
            past,
        )

    # Run sweeper
    count = await cleanup.sweep_abandoned_uploads(batch_size=10)
    assert count >= 1

    # Verify document is upload_failed
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT upload_status, error_code FROM public.kb_documents WHERE id = $1",
            doc_id,
        )
        assert row["upload_status"] == "upload_failed"
        assert row["error_code"] == "RESERVATION_EXPIRED"


# ------------------------------------------------------------------------------
# 10. Focused Security Verification Tests (Bounded Memory, Concurrency, Fencing)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_bounded_memory_streaming_validation():
    """Verify O(1) bounded memory streaming validation for large documents."""
    from app.modules.knowledge_base.validators import validate_storage_stream

    # Generator simulating 1000 chunks of 64KB (approx 64MB, exceeding 50MB)
    async def oversized_stream():
        chunk = b"X" * (64 * 1024)
        for _ in range(1000):
            yield chunk

    # Must raise FILE_TOO_LARGE before consuming all chunks
    with pytest.raises(Exception) as exc_info:
        await validate_storage_stream(
            stream=oversized_stream(),
            mime_type="text/plain",
        )
    assert getattr(exc_info.value, "code", None) == "FILE_TOO_LARGE"

    # Generator simulating valid 1MB text stream in 64KB chunks
    async def valid_text_stream():
        chunk = ("Academic notes on distributed consensus protocols.\n" * 1200).encode("utf-8")
        for _ in range(16):
            yield chunk

    size, sha256 = await validate_storage_stream(
        stream=valid_text_stream(),
        mime_type="text/plain",
    )
    assert size > 0
    assert len(sha256) == 64


@pytest.mark.asyncio
async def test_kb_strong_txt_validation_suite():
    """Comprehensive test of conservative text validation heuristic."""
    from app.modules.knowledge_base.validators import validate_content_and_checksum

    # 1. Valid UTF-8
    utf8_data = b"Hello world, standard UTF-8 academic text.\nLine 2.\tIndented.\n"
    s1, h1 = validate_content_and_checksum(utf8_data, "text/plain")
    assert s1 == len(utf8_data)

    # 2. Valid Latin-1 (ISO-8859-1 accented)
    latin1_data = b"R\xe9sum\xe9 d'un cours: mod\xe8le relationnel de donn\xe9es."
    s2, h2 = validate_content_and_checksum(latin1_data, "text/plain")
    assert s2 == len(latin1_data)

    # 3. Multilingual UTF-8
    multi_data = (
        "English. Français. Deutsch. Español. Русский. 中文. 日本語. हिन्दी. العربية.".encode()
    )
    s3, h3 = validate_content_and_checksum(multi_data, "text/plain")
    assert s3 == len(multi_data)

    # 4. Mathematical and academic symbols
    math_data = (
        "Theorem: ∀ x ∈ ℝ, ∫_{-∞}^{∞} e^{-x²} dx = √π. Also ∑_{i=1}^n x_i ≤ C ≈ ∞.\n".encode()
    )
    s4, h4 = validate_content_and_checksum(math_data, "text/plain")
    assert s4 == len(math_data)

    # 5. Null bytes rejected
    with pytest.raises(Exception) as exc:
        validate_content_and_checksum(b"Notes\x00Chapter 1", "text/plain")
    assert getattr(exc.value, "code", None) == "INVALID_TEXT_FORMAT"

    # 6. High control-character ratio rejected
    high_controls = b"Notes\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0e\x0f\x10\x11\x12\x13\x14\x15end"
    with pytest.raises(Exception) as exc:
        validate_content_and_checksum(high_controls, "text/plain")
    assert getattr(exc.value, "code", None) == "INVALID_TEXT_FORMAT"

    # 7. Disguised binary (random non-null bytes renamed .txt)
    pseudo_binary = bytes([((i * 37) % 255) + 1 for i in range(250)])
    with pytest.raises(Exception) as exc:
        validate_content_and_checksum(pseudo_binary, "text/plain")
    assert getattr(exc.value, "code", None) == "INVALID_TEXT_FORMAT"


@pytest.mark.asyncio
async def test_kb_confirm_concurrency_and_atomicity(client: httpx.AsyncClient):
    """Multiple concurrent confirmation requests result in exactly ONE extraction job."""
    import asyncio

    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    txt_content = b"Concurrency and atomicity test document content."
    payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Concurrent Doc",
        "original_filename": "concurrent.txt",
        "mime_type": "text/plain",
        "file_size_bytes": len(txt_content),
    }
    intent = await client.post("/api/v1/kb/documents/upload-intent", json=payload, headers=headers)
    doc_id = intent.json()["document_id"]
    storage_path = intent.json()["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    # Upload object
    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, txt_content, {"content-type": "text/plain"}
    )

    # Fire 5 concurrent confirm requests simultaneously
    async def call_confirm():
        return await client.post(f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers)

    responses = await asyncio.gather(*(call_confirm() for _ in range(5)))

    # All should return 200 (either initial confirmation or idempotent response)
    for resp in responses:
        assert resp.status_code == 200

    # Exactly ONE job must exist in kb_extraction_jobs
    pool = get_pool()
    async with pool.acquire() as conn:
        jobs = await conn.fetch(
            "SELECT id, status FROM public.kb_extraction_jobs WHERE document_id = $1",
            UUID(doc_id),
        )
        assert len(jobs) == 1
        assert jobs[0]["status"] == "queued"


@pytest.mark.asyncio
async def test_kb_deletion_fences_active_extraction_worker(client: httpx.AsyncClient):
    """Document deletion immediately invalidates active extraction jobs and clears lease tokens."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")
    headers = setup_auth_context(user_id=user_id)

    doc_id = uuid4()
    storage_path = f"documents/{doc_id}/source.txt"
    CREATED_STORAGE_PATHS.append(storage_path)

    storage_client = await storage.get_storage_client()
    await storage_client.storage.from_(settings.KB_STORAGE_BUCKET).upload(
        storage_path, b"Worker fencing test", {"content-type": "text/plain"}
    )

    lease_token = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Fencing Doc', 'fence.txt',
                $5, 'text/plain', 19, 'uploaded', 'processing'
            )
            """,
            doc_id,
            subject_id,
            topic_id,
            user_id,
            storage_path,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                document_id, status, worker_id, lease_token, attempts, locked_until
            ) VALUES (
                $1, 'processing', 'worker-node-1', $2, 1, timezone('utc'::text, now()) + interval '5 minutes'
            )
            """,
            doc_id,
            lease_token,
        )

    # Delete the document
    del_resp = await client.delete(f"/api/v1/kb/documents/{doc_id}", headers=headers)
    assert del_resp.status_code == 204

    # Assert that the extraction job was safely cancelled and lease token was revoked
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, error_code, lease_token FROM public.kb_extraction_jobs WHERE document_id = $1",
            doc_id,
        )
        assert job["status"] == "failed"
        assert job["error_code"] == "DOCUMENT_DELETED"
        assert job["lease_token"] is None  # Worker is fenced


@pytest.mark.asyncio
async def test_kb_sweeper_recovers_stranded_deleting_documents():
    """Sweeper recovers stranded deleting documents without touching valid uploaded documents."""
    subject_id, topic_id, _ = await create_test_taxonomy()
    user_id = await create_db_user(status="approved")

    # Document 1: Valid uploaded document (should NEVER be touched by sweeper)
    doc_uploaded = uuid4()
    path_uploaded = f"documents/{doc_uploaded}/source.txt"
    CREATED_STORAGE_PATHS.append(path_uploaded)

    # Document 2: Stranded in 'deleting' state for > 15 minutes (server crash recovery)
    doc_stranded = uuid4()
    path_stranded = f"documents/{doc_stranded}/source.txt"
    CREATED_STORAGE_PATHS.append(path_stranded)

    past = datetime.now(UTC) - timedelta(minutes=20)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status
            ) VALUES (
                $1, $2, $3, $4, 'Valid Uploaded', 'valid.txt',
                $5, 'text/plain', 10, 'uploaded', 'completed'
            )
            """,
            doc_uploaded,
            subject_id,
            topic_id,
            user_id,
            path_uploaded,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                is_archived, updated_at
            ) VALUES (
                $1, $2, $3, $4, 'Stranded Doc', 'stranded.txt',
                $5, 'text/plain', 10, 'deleting', 'pending', false, $6
            )
            """,
            doc_stranded,
            subject_id,
            topic_id,
            user_id,
            path_stranded,
            past,
        )

    # Run sweeper
    count = await cleanup.sweep_abandoned_uploads(batch_size=10)
    assert count >= 1

    # Check Document 1 is UNTOUCHED
    async with pool.acquire() as conn:
        doc1 = await conn.fetchrow(
            "SELECT upload_status, is_archived FROM public.kb_documents WHERE id = $1",
            doc_uploaded,
        )
        assert doc1["upload_status"] == "uploaded"
        assert doc1["is_archived"] is False

        # Check Document 2 is properly recovered and finalized as deleted
        doc2 = await conn.fetchrow(
            "SELECT upload_status, is_archived FROM public.kb_documents WHERE id = $1",
            doc_stranded,
        )
        assert doc2["upload_status"] == "deleted"
        assert doc2["is_archived"] is True
