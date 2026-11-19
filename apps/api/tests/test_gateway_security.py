"""Security and isolation tests for LLM Gateway."""

from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.credentials.repository import store_credential
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_CREDS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()
    pool = get_pool()
    if CREATED_TEST_CREDS:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM public.api_key_metadata WHERE id = ANY($1)", CREATED_TEST_CREDS
                )
        except Exception:
            pass
        CREATED_TEST_CREDS.clear()
    if CREATED_TEST_USERS:
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM auth.users WHERE id = ANY($1)", CREATED_TEST_USERS)
        except Exception:
            pass
        CREATED_TEST_USERS.clear()
    await close_vault_pool()
    await close_db_pool()
    await close_redis()


async def create_db_user(
    email: str | None = None, role: str = "user", status: str = "approved"
) -> UUID:
    pool = get_pool()
    user_id = uuid4()
    if not email:
        unique_email = f"student_{user_id.hex[:8]}@studyhub.edu"
    else:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Student"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
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
    email: str = "student@studyhub.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> dict[str, str]:
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


@pytest.mark.asyncio
async def test_unapproved_user_rejected(client: httpx.AsyncClient):
    user_id = await create_db_user(email="pending@studyhub.edu", status="pending")
    headers = setup_auth_context(user_id, email="pending@studyhub.edu", status="pending")

    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "provider_id": "gemini",
            "model_id": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "idempotency_key": f"sec_test_pending_{uuid4().hex[:8]}",
        },
        headers=headers,
    )

    assert resp.status_code == 403
    assert "not approved" in resp.text.lower() or "expected approved" in resp.text.lower()


@pytest.mark.asyncio
async def test_suspended_user_rejected(client: httpx.AsyncClient):
    user_id = await create_db_user(email="suspended@studyhub.edu", status="suspended")
    headers = setup_auth_context(user_id, email="suspended@studyhub.edu", status="suspended")

    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "provider_id": "gemini",
            "model_id": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "idempotency_key": f"sec_test_suspended_{uuid4().hex[:8]}",
        },
        headers=headers,
    )

    assert resp.status_code == 403
    assert "suspended" in resp.text.lower()


@pytest.mark.asyncio
async def test_cross_user_owner_only_credential_rejected(client: httpx.AsyncClient):
    user_a = await create_db_user(email="user_a@ragbait.app")
    user_b = await create_db_user(email="user_b@ragbait.app")

    unique_key = f"user-b-secret-gemini-key-{uuid4().hex}"
    # User B creates an owner-only credential
    cred_b = await store_credential(
        owner_id=user_b,
        provider_id="gemini",
        raw_key=unique_key,
        nickname="User B Private",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_b.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
            cred_b.id,
        )

    # User A tries to dispatch using User B's owner-only credential
    headers_a = setup_auth_context(user_a, email="user_a@ragbait.app")

    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "provider_id": "gemini",
            "model_id": "gemini-3.8-flash",
            "credential_id": str(cred_b.id),
            "messages": [{"role": "user", "content": "hi"}],
            "idempotency_key": f"sec_cross_{uuid4().hex[:8]}",
        },
        headers=headers_a,
    )

    assert resp.status_code == 403
    assert (
        "private" in resp.text.lower()
        or "owner" in resp.text.lower()
        or "denied" in resp.text.lower()
    )


@pytest.mark.asyncio
async def test_community_sharing_gated_by_provider_policy(client: httpx.AsyncClient):
    user_a = await create_db_user(email="user_a2@ragbait.app")
    user_b = await create_db_user(email="user_b2@ragbait.app")

    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            # Ensure groq has allows_community_sharing = false
            await conn.execute(
                """
                UPDATE public.llm_providers
                SET allows_community_sharing = false
                WHERE id = 'groq'
                """
            )

        unique_key = f"user-b-groq-key-{uuid4().hex}"
        # User B creates a credential and consents to community sharing
        cred_b = await store_credential(
            owner_id=user_b,
            provider_id="groq",
            raw_key=unique_key,
            nickname="User B Shared Groq",
            sharing_scope="community_shared",
            shared_consent=True,
            status="active",
        )
        CREATED_TEST_CREDS.append(cred_b.id)

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
                cred_b.id,
            )

        # User A tries to dispatch with it
        headers_a = setup_auth_context(user_a, email="user_a2@ragbait.app")

        resp = await client.post(
            "/api/v1/llm/chat/completions",
            json={
                "provider_id": "groq",
                "model_id": "openai/gpt-oss-120b",
                "credential_id": str(cred_b.id),
                "messages": [{"role": "user", "content": "hi"}],
                "idempotency_key": f"sec_comm_{uuid4().hex[:8]}",
            },
            headers=headers_a,
        )

        # Must be rejected because provider-level allows_community_sharing is False
        assert resp.status_code == 403
        assert "community" in resp.text.lower()
    finally:
        # Restore allows_community_sharing for groq
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public.llm_providers
                SET allows_community_sharing = true
                WHERE id = 'groq'
                """
            )
