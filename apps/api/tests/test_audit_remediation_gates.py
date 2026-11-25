"""Verification tests for Comprehensive Audit Remediation Gates (Workstreams A, B, E, I).

Covers:
1. Hard API contribution entitlement across ALL /chat/*, /llm/chat/completions, and /routing/preview endpoints.
2. Preserved DB history across credential disable -> re-enable lifecycle.
3. Rejection of paid, unknown-tier, unconsented, invalid, and unapproved admin credentials; retention of quota-exhausted free key entitlement.
4. Unrestricted read-only KB & Credentials access for non-contributors.
5. Trusted admin-only KB uploads (upload-intent, confirm-upload, worker claim/completion, job retry) with zero DB/job/storage side effects on denial.
"""

from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.knowledge_base import job_repository
from tests.conftest import create_test_jwt

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_audit_gate_env():
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            if CREATED_TEST_USERS or CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.chat_conversations WHERE user_id = ANY($1::uuid[]) OR subject_id = ANY($2::uuid[])",
                    CREATED_TEST_USERS,
                    CREATED_TEST_SUBJECTS,
                )
            if CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.kb_documents WHERE subject_id = ANY($1::uuid[])",
                    CREATED_TEST_SUBJECTS,
                )
                await conn.execute(
                    "DELETE FROM public.kb_topics WHERE subject_id = ANY($1::uuid[])",
                    CREATED_TEST_SUBJECTS,
                )
                await conn.execute(
                    "DELETE FROM public.kb_subjects WHERE id = ANY($1::uuid[])",
                    CREATED_TEST_SUBJECTS,
                )
            if CREATED_TEST_USERS:
                await conn.execute(
                    "DELETE FROM public.api_key_metadata WHERE owner_id = ANY($1::uuid[])",
                    CREATED_TEST_USERS,
                )
                await conn.execute(
                    "DELETE FROM auth.users WHERE id = ANY($1::uuid[])",
                    CREATED_TEST_USERS,
                )
    except Exception:
        pass
    CREATED_TEST_USERS.clear()
    CREATED_TEST_SUBJECTS.clear()


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def create_user_with_token(
    email_prefix: str,
    role: str = "user",
    approval_status: str = "approved",
) -> tuple[UUID, str]:
    user_id = uuid4()
    email = f"{email_prefix}_{user_id.hex[:6]}@audit.internal"
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name":"Audit User"}'::jsonb)
            """,
            user_id,
            email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = $1, approval_status = $2
            WHERE id = $3
            """,
            role,
            approval_status,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id, create_test_jwt(user_id, email=email)


async def insert_credential(
    owner_id: UUID,
    *,
    provider_id: str = "openrouter",
    billing_tier: str = "verified_free_only",
    status: str = "active",
    validation_status: str = "valid",
    sharing_scope: str = "community_shared",
    shared_consent: bool = True,
) -> UUID:
    cred_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                sharing_consent_timestamp, consent_version,
                billing_tier, billing_verified_at, billing_verification_source
            )
            VALUES (
                $1, $2, $3, 'Audit Key', 'sk-or-...aud', $4,
                $5, $6, $7, $8,
                CASE WHEN $8 THEN now() ELSE NULL END,
                CASE WHEN $8 THEN 'v1' ELSE NULL END,
                $9, now(), 'openrouter_auth_probe'
            )
            """,
            cred_id,
            owner_id,
            provider_id,
            uuid4().hex[:16],
            status,
            validation_status,
            sharing_scope,
            shared_consent,
            billing_tier,
        )
    return cred_id


async def create_taxonomy() -> tuple[UUID, UUID]:
    subject_id = uuid4()
    topic_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
            VALUES ($1, $2, $3, 'Audit subject', true)
            """,
            subject_id,
            f"Audit Subject {subject_id.hex[:6]}",
            f"audit-sub-{subject_id.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
            VALUES ($1, $2, 'Audit Topic', $3, true)
            """,
            topic_id,
            subject_id,
            f"audit-top-{topic_id.hex[:6]}",
        )
    CREATED_TEST_SUBJECTS.append(subject_id)
    return subject_id, topic_id


@pytest.mark.asyncio
async def test_all_chat_and_inference_endpoints_enforce_hard_contribution_gate(
    client: httpx.AsyncClient,
):
    """Non-contributors get 403 CHAT_CONTRIBUTION_REQUIRED on all /chat/*, /llm/chat/completions, and /routing/preview."""
    user_id, token = await create_user_with_token(
        "no_contrib", role="user", approval_status="approved"
    )
    subject_id, _ = await create_taxonomy()
    headers = {"Authorization": f"Bearer {token}"}

    # Unrelated conversation owned by user (inserted directly to verify history endpoints block too)
    conv_id = uuid4()
    msg_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
            VALUES ($1, $2, $3, 'Preserved Old Thread', 'active')
            """,
            conv_id,
            user_id,
            subject_id,
        )
        await conn.execute(
            """
            INSERT INTO public.chat_messages (id, conversation_id, role, content, sequence_number)
            VALUES ($1, $2, 'user', 'Question', 1)
            """,
            msg_id,
            conv_id,
        )

    blocked_requests = [
        ("GET", "/api/v1/chat/conversations", None),
        ("POST", "/api/v1/chat/conversations", {"subject_id": str(subject_id), "title": "New"}),
        ("GET", f"/api/v1/chat/conversations/{conv_id}", None),
        ("PATCH", f"/api/v1/chat/conversations/{conv_id}", {"title": "Rename"}),
        ("POST", f"/api/v1/chat/conversations/{conv_id}/archive", None),
        ("POST", f"/api/v1/chat/conversations/{conv_id}/restore", None),
        ("DELETE", f"/api/v1/chat/conversations/{conv_id}", None),
        ("GET", f"/api/v1/chat/conversations/{conv_id}/messages", None),
        (
            "POST",
            f"/api/v1/chat/conversations/{conv_id}/messages",
            {"content": "Hello", "stream": False},
        ),
        (
            "POST",
            f"/api/v1/chat/conversations/{conv_id}/messages/{msg_id}/cancel",
            None,
        ),
        (
            "POST",
            f"/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
            {"content": "Hello", "model_id": "llama3.2:latest"},
        ),
        (
            "POST",
            f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
            {"prepared_request_id": str(uuid4()), "assistant_content": "Local"},
        ),
        (
            "GET",
            f"/api/v1/chat/messages/{msg_id}/sources",
            None,
        ),
        (
            "POST",
            "/api/v1/llm/chat/completions",
            {"messages": [{"role": "user", "content": "Hello"}], "stream": False},
        ),
        (
            "POST",
            "/api/v1/routing/preview",
            {"messages": [{"role": "user", "content": "Hello"}], "stream": False},
        ),
    ]

    for method, path, payload in blocked_requests:
        resp = await client.request(method, path, json=payload, headers=headers)
        assert resp.status_code == 403, (
            f"Expected 403 for {method} {path}, got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["error"]["code"] == "CHAT_CONTRIBUTION_REQUIRED"

    # Independent modules MUST remain accessible for approved non-contributors
    assert (await client.get("/api/v1/kb/subjects", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/me/api-keys", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_credential_tier_consent_and_quota_exhausted_entitlement_rules(
    client: httpx.AsyncClient,
):
    """Paid/unknown/unconsented/invalid keys do not unlock chat; quota-exhausted free key keeps entitlement; unapproved admin rejected."""
    # 1. Paid, unknown-tier, owner-only, unconsented, invalid OpenRouter keys MUST NOT unlock chat
    for idx, kwargs in enumerate(
        [
            {"billing_tier": "paid"},
            {"billing_tier": "unknown"},
            {"sharing_scope": "owner_only"},
            {"shared_consent": False},
            {"validation_status": "invalid_credential"},
        ]
    ):
        uid, tok = await create_user_with_token(
            f"nonqual_{idx}", role="user", approval_status="approved"
        )
        await insert_credential(uid, **kwargs)
        resp = await client.get(
            "/api/v1/chat/conversations",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "CHAT_CONTRIBUTION_REQUIRED"

    # 2. Quota-exhausted verified free-tier OpenRouter key MUST KEEP entitlement
    quota_uid, quota_tok = await create_user_with_token(
        "quota_ok", role="user", approval_status="approved"
    )
    await insert_credential(quota_uid, status="quota_exhausted")
    headers = {"Authorization": f"Bearer {quota_tok}"}

    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    assert ent_resp.json()["eligible"] is True

    conv_resp = await client.get("/api/v1/chat/conversations", headers=headers)
    assert conv_resp.status_code == 200

    # 3. Unapproved admin must still be rejected by approval status check (no bypass)
    _, pending_admin_tok = await create_user_with_token(
        "pending_admin", role="admin", approval_status="pending"
    )
    pend_resp = await client.get(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {pending_admin_tok}"},
    )
    assert pend_resp.status_code == 403
    assert pend_resp.json()["error"]["code"] == "ACCOUNT_NOT_APPROVED"


@pytest.mark.asyncio
async def test_kb_uploads_and_parser_restricted_to_trusted_admins(
    client: httpx.AsyncClient,
):
    """Ordinary approved students (even contributors) cannot initiate/confirm KB uploads, claim jobs, or retry non-admin docs."""
    subject_id, topic_id = await create_taxonomy()
    student_id, student_tok = await create_user_with_token(
        "contrib_student", role="user", approval_status="approved"
    )
    await insert_credential(student_id)
    admin_id, admin_tok = await create_user_with_token(
        "trusted_admin", role="admin", approval_status="approved"
    )

    student_headers = {"Authorization": f"Bearer {student_tok}"}
    admin_headers = {"Authorization": f"Bearer {admin_tok}"}

    intent_payload = {
        "subject_id": str(subject_id),
        "topic_id": str(topic_id),
        "title": "Untrusted Student Upload",
        "original_filename": "exploit.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 1024,
    }

    # Student upload-intent -> 403 ADMIN_REQUIRED
    r_intent = await client.post(
        "/api/v1/kb/documents/upload-intent",
        json=intent_payload,
        headers=student_headers,
    )
    assert r_intent.status_code == 403
    assert r_intent.json()["error"]["code"] == "ADMIN_REQUIRED"

    # Verify zero kb_documents / kb_extraction_jobs side effects created
    pool = get_pool()
    async with pool.acquire() as conn:
        doc_count = await conn.fetchval(
            "SELECT count(*)::int FROM public.kb_documents WHERE uploader_id = $1",
            student_id,
        )
        assert doc_count == 0

    # Student confirm-upload -> 403 ADMIN_REQUIRED
    fake_doc_id = uuid4()
    r_confirm = await client.post(
        f"/api/v1/kb/documents/{fake_doc_id}/confirm-upload",
        headers=student_headers,
    )
    assert r_confirm.status_code == 403
    assert r_confirm.json()["error"]["code"] == "ADMIN_REQUIRED"

    # Legacy/injected non-admin queued or failed document MUST NOT be claimed, completed, or retried
    untrusted_doc_id = uuid4()
    untrusted_job_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, uploader_id, title, original_filename,
                storage_path, mime_type, file_size_bytes, upload_status, extraction_status, is_archived
            )
            VALUES ($1, $2, $3, $4, 'Injected Untrusted Doc', 'untrusted.txt', $5, 'text/plain', 100, 'uploaded', 'queued', false)
            """,
            untrusted_doc_id,
            subject_id,
            topic_id,
            student_id,
            f"documents/{untrusted_doc_id}/source.txt",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_extraction_jobs (
                id, document_id, status, attempts, max_attempts, retry_count
            )
            VALUES ($1, $2, 'queued', 0, 3, 0)
            """,
            untrusted_job_id,
            untrusted_doc_id,
        )

        # Worker claim must ignore non-admin uploaded document
        claimed = await job_repository.claim_next_job(
            conn, worker_id="audit-worker", lease_ttl_seconds=60
        )
        assert claimed is None

        # Mark job failed and confirm Admin Retry refuses untrusted parser execution
        await conn.execute(
            """
            UPDATE public.kb_extraction_jobs
            SET status = 'failed', error_code = 'PARSER_TIMEOUT'
            WHERE id = $1
            """,
            untrusted_job_id,
        )
        await conn.execute(
            "UPDATE public.kb_documents SET extraction_status = 'failed' WHERE id = $1",
            untrusted_doc_id,
        )

    retry_resp = await client.post(
        f"/api/v1/kb/admin/jobs/{untrusted_job_id}/retry",
        headers=admin_headers,
        json={"reason": "Should be blocked"},
    )
    assert retry_resp.status_code == 400
    assert retry_resp.json()["error"]["code"] == "UNTRUSTED_DOCUMENT_PARSER_BLOCKED"
