"""Comprehensive tests for Mandatory AI Chat Contribution Gate and Credential Scope."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.credentials.repository import delete_credential_secret, store_credential
from app.modules.credentials.service import check_chat_contribution_entitlement
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.interfaces import NormalizedStreamChunk, NormalizedUsage
from app.modules.routing.discovery import discover_cloud_candidates
from app.modules.routing.policy import filter_candidates
from app.modules.routing.schemas import RoutingCandidate
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


async def _mock_stream_generator(*args, **kwargs) -> AsyncIterator[NormalizedStreamChunk]:
    yield NormalizedStreamChunk(delta="Hello ")
    yield NormalizedStreamChunk(delta="from chat!")
    yield NormalizedStreamChunk(
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            usage_source="authoritative",
        ),
    )


@pytest.mark.asyncio
async def test_user_without_credential_blocked_from_chat(client: httpx.AsyncClient):
    """User without any contributed credentials must be blocked with HTTP 403 CONTRIBUTION_REQUIRED."""
    user_id = await create_db_user(email="no_key@studyhub.edu")
    headers = setup_auth_context(user_id, email="no_key@studyhub.edu")

    # 1. Chat completions endpoint blocked
    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-idem-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"

    # 2. Routing preview endpoint blocked
    prev_resp = await client.post(
        "/api/v1/routing/preview",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-idem-prev-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert prev_resp.status_code == 403
    assert prev_resp.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"

    # 3. Entitlement status check returns NO_CREDENTIAL_CONTRIBUTED
    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    ent_data = ent_resp.json()
    assert ent_data["eligible"] is False
    assert ent_data["reason_code"] == "NO_CREDENTIAL_CONTRIBUTED"
    assert ent_data["eligible_credential_count"] == 0


@pytest.mark.asyncio
async def test_user_with_owner_only_credential_blocked(client: httpx.AsyncClient):
    """Owner-only keys do NOT satisfy the shared contribution requirement."""
    user_id = await create_db_user(email="owner_only@studyhub.edu")
    headers = setup_auth_context(user_id, email="owner_only@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        raw_key=f"gsk-test-{uuid4().hex}",
        nickname="My Private Key",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    # Chat completion is blocked
    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-idem-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"

    # Entitlement status reflects consent required
    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    assert ent_resp.json()["eligible"] is False
    assert ent_resp.json()["reason_code"] == "SHARING_CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_unvalidated_credential_blocked(client: httpx.AsyncClient):
    """A contributed key that has not passed validation does not satisfy the requirement."""
    user_id = await create_db_user(email="unvalidated@studyhub.edu")
    headers = setup_auth_context(user_id, email="unvalidated@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        raw_key=f"gsk-test-{uuid4().hex}",
        nickname="Unvalidated Shared Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    # validation_status is 'unvalidated' by default
    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    assert ent_resp.json()["eligible"] is False
    assert ent_resp.json()["reason_code"] == "VALIDATION_REQUIRED"


@pytest.mark.asyncio
async def test_disabled_credential_blocked(client: httpx.AsyncClient):
    """A valid shared key that is disabled does not satisfy the requirement."""
    user_id = await create_db_user(email="disabled_cred@studyhub.edu")
    headers = setup_auth_context(user_id, email="disabled_cred@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        raw_key=f"gsk-test-{uuid4().hex}",
        nickname="Disabled Shared Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="disabled",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    assert ent_resp.json()["eligible"] is False
    assert ent_resp.json()["reason_code"] == "CREDENTIAL_INACTIVE"


@pytest.mark.asyncio
async def test_valid_shared_free_tier_credential_entitled(client: httpx.AsyncClient):
    """A valid, active, provider-approved free-tier shared key unlocks AI Chat."""
    user_id = await create_db_user(email="entitled_user@studyhub.edu")
    headers = setup_auth_context(user_id, email="entitled_user@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-v1-test-{uuid4().hex}",
        nickname="Active Shared OpenRouter Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    # Entitlement status check
    ent_resp = await client.get("/api/v1/me/chat-entitlement", headers=headers)
    assert ent_resp.status_code == 200
    ent_data = ent_resp.json()
    assert ent_data["eligible"] is True
    assert ent_data["reason_code"] == "QUALIFYING_CONTRIBUTION_ACTIVE"
    assert ent_data["eligible_credential_count"] == 1

    # Effective access state check
    with patch(
        "app.modules.access.service.fetch_authoritative_auth_user",
        new_callable=AsyncMock,
        return_value=(True, "entitled_user@studyhub.edu"),
    ):
        access_resp = await client.get("/api/v1/me/access", headers=headers)
        assert access_resp.status_code == 200
        assert access_resp.json()["chat_entitled"] is True
        assert access_resp.json()["effective_state"] == "active"

    # Chat execution succeeds
    with patch(
        "app.modules.llm_gateway.providers.openrouter.OpenRouterAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
        chat_resp = await client.post(
            "/api/v1/llm/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "routing_mode": "auto",
                "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                "idempotency_key": f"test-idem-ok-{uuid4().hex[:8]}",
            },
            headers=headers,
        )
        assert chat_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_without_contribution_blocked(client: httpx.AsyncClient):
    """Administrator privileges do NOT bypass the mandatory chat contribution requirement."""
    admin_id = await create_db_user(email="admin_no_key@studyhub.edu", role="admin")
    headers = setup_auth_context(admin_id, email="admin_no_key@studyhub.edu", role="admin")

    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello admin"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-idem-admin-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"


@pytest.mark.asyncio
async def test_revocation_immediately_blocks_chat(client: httpx.AsyncClient):
    """Deleting or revoking the only qualifying key immediately revokes chat entitlement."""
    user_id = await create_db_user(email="revocation_user@studyhub.edu")
    headers = setup_auth_context(user_id, email="revocation_user@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-test-{uuid4().hex}",
        nickname="Key To Delete",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    # Initially entitled
    is_entitled, _, count = await check_chat_contribution_entitlement(user_id)
    assert is_entitled is True
    assert count == 1

    # Delete credential
    await delete_credential_secret(credential_id=stored.id, caller_id=user_id)

    # Entitlement immediately revoked
    is_entitled_after, reason, count_after = await check_chat_contribution_entitlement(user_id)
    assert is_entitled_after is False
    assert reason == "NO_CREDENTIAL_CONTRIBUTED"
    assert count_after == 0

    # Chat endpoint immediately rejects
    resp = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-idem-revoked-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"


@pytest.mark.asyncio
async def test_temporary_quota_exhaustion_preserves_chat_entitlement():
    """Temporary quota exhaustion on contributed key preserves the contributor's chat entitlement."""
    user_id = await create_db_user(email="quota_user@studyhub.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-test-{uuid4().hex}",
        nickname="Exhausted Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="quota_exhausted",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    # Contributor entitlement remains intact
    is_entitled, reason, count = await check_chat_contribution_entitlement(user_id)
    assert is_entitled is True
    assert reason == "QUALIFYING_CONTRIBUTION_ACTIVE"
    assert count == 1


@pytest.mark.asyncio
async def test_credential_scope_owner_only_excludes_shared_keys():
    """credential_scope='owner_only' strictly excludes foreign shared credentials from discovery."""
    owner_user = await create_db_user(email="owner_a@studyhub.edu")
    foreign_user = await create_db_user(email="foreign_b@studyhub.edu")

    # foreign_user contributes a shared key
    foreign_key = await store_credential(
        owner_id=foreign_user,
        provider_id="openrouter",
        raw_key=f"sk-or-foreign-{uuid4().hex}",
        nickname="Foreign Shared Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(foreign_key.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'verified_free_only', billing_verified_at = now() WHERE id = $1",
            foreign_key.id,
        )

    # 1. With eligible_pool, foreign shared key IS discovered
    pool_candidates = await discover_cloud_candidates(
        user_id=owner_user,
        requested_provider_id="openrouter",
        credential_scope="eligible_pool",
    )
    shared_found = any(c.is_shared_credential for c in pool_candidates)
    assert shared_found is True

    # 2. With owner_only, foreign shared key is NEVER discovered
    owner_candidates = await discover_cloud_candidates(
        user_id=owner_user,
        requested_provider_id="openrouter",
        credential_scope="owner_only",
    )
    assert all(not c.is_shared_credential for c in owner_candidates)
    assert all(c.credential_id is None for c in owner_candidates)


@pytest.mark.asyncio
async def test_shared_pool_free_tier_only_filter():
    """Shared pool credentials can only be used for free-tier eligible models with zero metered charges."""
    candidate_shared_paid = RoutingCandidate(
        source_type="cloud",
        provider_id="groq",
        model_id="paid-model-sample",
        display_name="Paid Model",
        family="sample",
        context_window=8192,
        max_output_tokens=2048,
        capabilities=["streaming"],
        credential_id=uuid4(),
        is_shared_credential=True,
        free_tier_eligible=False,  # NOT free-tier eligible
        pricing_input_cents_per_million=100,
        pricing_output_cents_per_million=200,
    )

    eligible, filter_results = filter_candidates(
        candidates=[candidate_shared_paid],
        routing_mode="auto",
        community_opt_in=False,
        estimated_input_tokens=100,
        requested_max_output=100,
        stream=True,
    )

    assert len(eligible) == 0
    assert len(filter_results) == 1
    assert filter_results[0].eligible is False
    assert filter_results[0].rejection_code == "SHARED_POOL_FREE_TIER_ONLY"
