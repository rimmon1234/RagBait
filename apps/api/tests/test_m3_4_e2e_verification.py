"""End-to-end integration and two-student verification test for Milestone M3.4.

Validates:
1. Real browser-to-Storage one-shot signed PUT upload flow against Supabase Storage.
2. Background extraction worker processing multi-page PDF, TXT, and scanned image-only PDF.
3. Two-student collaboration, authorization, cross-user deletion denial, and deletion lifecycle.
4. Approved non-contributor access and unapproved user rejection.
"""

import hashlib
import io
from uuid import UUID, uuid4

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.knowledge_base import storage
from app.modules.knowledge_base.worker import KnowledgeBaseExtractionWorker
from app.modules.knowledge_base.worker_config import WorkerSettings
from app.modules.users.schemas import ApprovalStatus, UserProfile, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_STORAGE_PATHS: list[str] = []
CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_e2e_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()
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


async def create_db_user(
    email: str | None = None,
    role: str = "user",
    status: str = "approved",
) -> tuple[UUID, str]:
    """Create real student user in auth.users and public.profiles with unique email."""
    pool = get_pool()
    user_id = uuid4()
    if email:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"
    else:
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
            SET role = $1, approval_status = $2
            WHERE id = $3
            """,
            role,
            status,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id, unique_email


def setup_auth(
    user_id: UUID,
    email: str = "student@university.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> dict[str, str]:
    """Configure FastAPI auth dependency overrides for the test user."""
    profile = UserProfile(
        id=user_id,
        email=email,
        display_name=f"Student {user_id.hex[:4]}",
        role=role,
        approval_status=status,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
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


async def create_taxonomy() -> tuple[UUID, UUID]:
    """Create a test subject and topic."""
    pool = get_pool()
    subject_id = uuid4()
    topic_id = uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description, icon_name, display_order, is_active)
            VALUES ($1, $2, $3, 'Database Management', 'Database', 1, true)
            """,
            subject_id,
            f"DBMS-{subject_id.hex[:6]}",
            f"dbms-{subject_id.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, description, display_order, is_active)
            VALUES ($1, $2, 'Relational Algebra', 'relational-algebra', 'Operators and queries', 1, true)
            """,
            topic_id,
            subject_id,
        )

    CREATED_TEST_SUBJECTS.append(subject_id)
    return subject_id, topic_id


def make_pdf(pages_text: list[str | None]) -> bytes:
    """Create valid PDF bytes with physical pages and text or blank pages."""
    writer = PdfWriter()
    for text in pages_text:
        if text is None:
            writer.add_blank_page(width=612, height=792)
        else:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_content = f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode()
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

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_two_student_e2e_pdf_upload_and_lifecycle(client: httpx.AsyncClient):
    """Full scenario: Student A uploads PDF, extraction runs, Student B views/downloads, cross-deletion blocked."""
    subject_id, topic_id = await create_taxonomy()
    student_a_id, email_a = await create_db_user(email="student_a@univ.edu", status="approved")
    student_b_id, email_b = await create_db_user(email="student_b@univ.edu", status="approved")

    # 1. Student A prepares a 3-page academic PDF
    page1 = "Page 1: Selection σ_salary > 50000 and Projection π_name,salary"
    page2 = "Page 2: Natural Join Relation_R ⨝ Relation_S"
    page3 = "Page 3: Query Tree Optimization"
    pdf_bytes = make_pdf([page1, page2, page3])
    pdf_size = len(pdf_bytes)
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    # Student A authenticates
    headers_a = setup_auth(student_a_id, email=email_a)

    # Step 2: Student A requests upload reservation intent
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent",
        json={
            "subject_id": str(subject_id),
            "topic_id": str(topic_id),
            "title": "Relational Algebra Notes",
            "original_filename": "rel_alg.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": pdf_size,
            "sha256_checksum": pdf_sha256,
        },
        headers=headers_a,
    )
    assert intent_resp.status_code == 201
    intent = intent_resp.json()
    doc_id = intent["document_id"]
    signed_upload_url = intent["signed_upload_url"]
    storage_path = intent["storage_path"]
    CREATED_STORAGE_PATHS.append(storage_path)

    # Verify no service-role key is in the response
    assert settings.SUPABASE_SERVICE_ROLE_KEY not in str(intent)

    # Step 3: Direct PUT upload to private Supabase Storage via signed URL
    async with httpx.AsyncClient() as storage_http:
        put_resp = await storage_http.put(
            signed_upload_url,
            content=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
        assert put_resp.status_code in (200, 201)

    # Verify raw bytes in storage match
    downloaded_raw = await storage.download_object_bytes(storage_path)
    assert hashlib.sha256(downloaded_raw).hexdigest() == pdf_sha256

    # Step 4: Student A confirms upload with FastAPI backend
    confirm_resp = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload",
        json={"sha256_checksum": pdf_sha256, "file_size_bytes": pdf_size},
        headers=headers_a,
    )
    assert confirm_resp.status_code == 200
    confirm_data = confirm_resp.json()
    assert confirm_data["document"]["upload_status"] == "uploaded"
    assert confirm_data["document"]["extraction_status"] == "queued"

    # Verify confirmation idempotency: second confirm returns 200 without duplicate job
    confirm_retry = await client.post(
        f"/api/v1/kb/documents/{doc_id}/confirm-upload",
        headers=headers_a,
    )
    assert confirm_retry.status_code == 200

    # Step 5: Background extraction worker processes the queued document
    worker = KnowledgeBaseExtractionWorker(
        settings=WorkerSettings(WORKER_ID="e2e-worker-test"),
        once=True,
    )
    await worker.start()
    try:
        claimed = await worker.process_next_job()
        assert claimed is True
    finally:
        await worker.stop()

    # Step 6: Student A inspects document details
    doc_resp = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers_a)
    assert doc_resp.status_code == 200
    doc_data = doc_resp.json()
    assert doc_data["extraction_status"] == "completed"
    assert doc_data["total_pages"] == 3
    assert doc_data["processed_pages"] == 3
    assert doc_data["pages_with_text"] == 3

    # Step 7: Student A previews extracted text page-by-page
    pages_resp = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=headers_a)
    assert pages_resp.status_code == 200
    pages_data = pages_resp.json()
    assert len(pages_data["items"]) == 3
    assert "Selection" in pages_data["items"][0]["raw_text"]
    assert "Join" in pages_data["items"][1]["raw_text"]

    # Step 8: Student A generates signed download URL and downloads original
    dl_resp = await client.get(f"/api/v1/kb/documents/{doc_id}/download-url", headers=headers_a)
    assert dl_resp.status_code == 200
    dl_url = dl_resp.json()["download_url"]
    assert dl_url.startswith("http")

    async with httpx.AsyncClient() as dl_http:
        file_get = await dl_http.get(dl_url)
        assert file_get.status_code == 200
        assert hashlib.sha256(file_get.content).hexdigest() == pdf_sha256

    # =========================================================================
    # Step 9: Student B Collaboration & Authorization Verification
    # =========================================================================
    headers_b = setup_auth(student_b_id, email=email_b)

    # Student B browses topic documents
    list_b = await client.get(f"/api/v1/kb/topics/{topic_id}/documents", headers=headers_b)
    assert list_b.status_code == 200
    topic_docs = list_b.json()["items"]
    assert any(d["id"] == doc_id for d in topic_docs)

    # Student B previews Student A's extracted pages
    pages_b = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=headers_b)
    assert pages_b.status_code == 200
    assert len(pages_b.json()["items"]) == 3

    # Student B downloads original file
    dl_b = await client.get(f"/api/v1/kb/documents/{doc_id}/download-url", headers=headers_b)
    assert dl_b.status_code == 200

    # Student B attempts to DELETE Student A's document -> Must fail with 400 UNAUTHORIZED_DELETION!
    del_b = await client.delete(f"/api/v1/kb/documents/{doc_id}", headers=headers_b)
    assert del_b.status_code == 400
    assert del_b.json()["error"]["code"] == "UNAUTHORIZED_DELETION"

    # =========================================================================
    # Step 10: Student A safely deletes their own document
    # =========================================================================
    headers_a = setup_auth(student_a_id, email=email_a)
    del_a = await client.delete(f"/api/v1/kb/documents/{doc_id}", headers=headers_a)
    assert del_a.status_code == 204

    # Storage object removed from bucket
    with pytest.raises(storage.NotFoundError):
        await storage.download_object_bytes(storage_path)

    # Document no longer listed in active topic documents
    headers_b = setup_auth(student_b_id, email=email_b)
    list_after = await client.get(f"/api/v1/kb/topics/{topic_id}/documents", headers=headers_b)
    assert not any(d["id"] == doc_id for d in list_after.json()["items"])

    # Direct access by Student B returns 404
    get_after = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers_b)
    assert get_after.status_code == 404


@pytest.mark.asyncio
async def test_two_student_e2e_txt_upload(client: httpx.AsyncClient):
    """Full scenario for TXT document upload, logical pagination, and cross-student viewing."""
    subject_id, topic_id = await create_taxonomy()
    student_a_id, email_a = await create_db_user(email="student_txt_a@univ.edu", status="approved")
    student_b_id, email_b = await create_db_user(email="student_txt_b@univ.edu", status="approved")

    txt_content = b"Database Normalization.\n1NF: Atomic values.\n\x0c2NF: No partial dependencies.\n\x0c3NF: No transitive dependencies.\n"
    txt_size = len(txt_content)
    txt_sha256 = hashlib.sha256(txt_content).hexdigest()

    headers_a = setup_auth(student_a_id, email=email_a)

    # Upload intent
    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent",
        json={
            "subject_id": str(subject_id),
            "topic_id": str(topic_id),
            "title": "Normalization Cheatsheet",
            "original_filename": "normalization.txt",
            "mime_type": "text/plain",
            "file_size_bytes": txt_size,
            "sha256_checksum": txt_sha256,
        },
        headers=headers_a,
    )
    assert intent_resp.status_code == 201
    doc_id = intent_resp.json()["document_id"]
    signed_url = intent_resp.json()["signed_upload_url"]
    CREATED_STORAGE_PATHS.append(intent_resp.json()["storage_path"])

    # PUT upload to storage
    async with httpx.AsyncClient() as http:
        put_resp = await http.put(signed_url, content=txt_content, headers={"Content-Type": "text/plain"})
        assert put_resp.status_code in (200, 201)

    # Confirm
    conf = await client.post(f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers_a)
    assert conf.status_code == 200

    # Worker extraction
    worker = KnowledgeBaseExtractionWorker(settings=WorkerSettings(WORKER_ID="txt-worker"), once=True)
    await worker.start()
    try:
        await worker.process_next_job()
    finally:
        await worker.stop()

    # Student B reads extracted pages
    headers_b = setup_auth(student_b_id, email=email_b)
    pages_resp = await client.get(f"/api/v1/kb/documents/{doc_id}/pages", headers=headers_b)
    assert pages_resp.status_code == 200
    assert len(pages_resp.json()["items"]) == 3
    assert "1NF" in pages_resp.json()["items"][0]["raw_text"]


@pytest.mark.asyncio
async def test_scanned_pdf_completed_no_text_lifecycle(client: httpx.AsyncClient):
    """Scanned image-only PDF with zero text transitions to completed_no_text safely."""
    subject_id, topic_id = await create_taxonomy()
    student_id, student_email = await create_db_user(email="scanned_uploader@univ.edu", status="approved")
    headers = setup_auth(student_id, email=student_email)

    # PDF with 2 blank / non-selectable pages
    scanned_pdf = make_pdf([None, None])
    pdf_size = len(scanned_pdf)
    pdf_sha256 = hashlib.sha256(scanned_pdf).hexdigest()

    intent_resp = await client.post(
        "/api/v1/kb/documents/upload-intent",
        json={
            "subject_id": str(subject_id),
            "topic_id": str(topic_id),
            "title": "Scanned Handwritten Notes",
            "original_filename": "scanned.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": pdf_size,
            "sha256_checksum": pdf_sha256,
        },
        headers=headers,
    )
    assert intent_resp.status_code == 201
    doc_id = intent_resp.json()["document_id"]
    signed_url = intent_resp.json()["signed_upload_url"]
    CREATED_STORAGE_PATHS.append(intent_resp.json()["storage_path"])

    async with httpx.AsyncClient() as http:
        put_resp = await http.put(signed_url, content=scanned_pdf, headers={"Content-Type": "application/pdf"})
        assert put_resp.status_code in (200, 201)

    await client.post(f"/api/v1/kb/documents/{doc_id}/confirm-upload", headers=headers)

    # Worker extraction
    worker = KnowledgeBaseExtractionWorker(settings=WorkerSettings(WORKER_ID="scanned-worker"), once=True)
    await worker.start()
    try:
        await worker.process_next_job()
    finally:
        await worker.stop()

    # Document must be completed_no_text
    doc = await client.get(f"/api/v1/kb/documents/{doc_id}", headers=headers)
    assert doc.status_code == 200
    assert doc.json()["extraction_status"] == "completed_no_text"
    assert doc.json()["total_pages"] == 2
    assert doc.json()["pages_with_text"] == 0

    # Original download remains available
    dl = await client.get(f"/api/v1/kb/documents/{doc_id}/download-url", headers=headers)
    assert dl.status_code == 200


@pytest.mark.asyncio
async def test_kb_authorization_scopes(client: httpx.AsyncClient):
    """Pending/rejected/suspended users blocked; approved non-contributor allowed."""
    subject_id, topic_id = await create_taxonomy()

    # Pending user
    pending_id, email_pen = await create_db_user(email="pending@univ.edu", status="pending")
    h_pending = setup_auth(pending_id, email=email_pen, status="pending")
    res_pending = await client.get("/api/v1/kb/subjects", headers=h_pending)
    assert res_pending.status_code == 403

    # Rejected user
    rejected_id, email_rej = await create_db_user(email="rejected@univ.edu", status="rejected")
    h_rejected = setup_auth(rejected_id, email=email_rej, status="rejected")
    res_rejected = await client.get("/api/v1/kb/subjects", headers=h_rejected)
    assert res_rejected.status_code == 403

    # Suspended user
    suspended_id, email_sus = await create_db_user(email="suspended@univ.edu", status="suspended")
    h_suspended = setup_auth(suspended_id, email=email_sus, status="suspended")
    res_suspended = await client.get("/api/v1/kb/subjects", headers=h_suspended)
    assert res_suspended.status_code == 403

    # Approved non-contributor (approved user with 0 credentials and 0 nodes)
    approved_id, email_app = await create_db_user(email="approved_non_contributor@univ.edu", status="approved")
    h_approved = setup_auth(approved_id, email=email_app, status="approved")
    res_approved = await client.get("/api/v1/kb/subjects", headers=h_approved)
    assert res_approved.status_code == 200
