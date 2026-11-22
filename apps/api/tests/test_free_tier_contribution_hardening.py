"""Adversarial and regression tests for Free-Tier Contribution Integrity Hardening."""

from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from app.core.config import settings
from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.credentials.repository import store_credential
from app.modules.credentials.service import check_chat_contribution_entitlement
from app.modules.credentials.validator import probe_openrouter
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.interfaces import NormalizedStreamChunk, NormalizedUsage
from app.modules.routing.discovery import discover_cloud_candidates
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


async def create_test_user(email: str = "tester@studyhub.edu") -> UUID:
    pool = get_pool()
    user_id = uuid4()
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
            SET role = 'user', approval_status = 'approved'
            WHERE id = $1
            """,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id


def setup_auth(user_id: UUID, email: str = "tester@studyhub.edu") -> dict[str, str]:
    profile = make_test_profile(user_id=user_id, email=email, role="user", approval_status="approved")
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


# ==============================================================================
# 1. Database Tampering Protection Tests (Finding A)
# ==============================================================================


@pytest.mark.asyncio
async def test_direct_authenticated_role_cannot_modify_server_managed_fields():
    """An authenticated database connection cannot tamper with validation_status, status, sharing_scope, or billing_tier."""
    user_id = await create_test_user("tamper_test@studyhub.edu")

    # Store a legitimate disabled/unvalidated credential
    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-test-{uuid4().hex}",
        nickname="Legit Key",
        sharing_scope="owner_only",
        shared_consent=False,
        status="disabled",
    )
    CREATED_TEST_CREDS.append(stored.id)

    # Connect to PostgreSQL directly and impersonate authenticated role
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        # Set role to authenticated
        await conn.execute("SET ROLE authenticated;")
        await conn.execute(f"SET request.jwt.claim.sub = '{user_id}';")

        # 1. Attempt to forge validation_status
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                f"UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = '{stored.id}';"
            )

        # 2. Attempt to forge status
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                f"UPDATE public.api_key_metadata SET status = 'active' WHERE id = '{stored.id}';"
            )

        # 3. Attempt to forge sharing_scope & shared_consent
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                f"UPDATE public.api_key_metadata SET sharing_scope = 'community_shared', shared_consent = true WHERE id = '{stored.id}';"
            )

        # 4. Attempt to forge billing_tier
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                f"UPDATE public.api_key_metadata SET billing_tier = 'verified_free_only' WHERE id = '{stored.id}';"
            )

        # 5. Attempt to forge account_scope_id
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                f"UPDATE public.api_key_metadata SET account_scope_id = 'attacker_scope' WHERE id = '{stored.id}';"
            )

        # 6. Authenticated user CAN update nickname
        await conn.execute(
            f"UPDATE public.api_key_metadata SET nickname = 'Updated Nickname' WHERE id = '{stored.id}';"
        )
    finally:
        await conn.execute("RESET ROLE;")
        await conn.close()

    # Verify nickname was updated, but security fields remained intact
    pool = get_pool()
    async with pool.acquire() as db_conn:
        row = await db_conn.fetchrow(
            "SELECT nickname, validation_status, status, sharing_scope, billing_tier FROM public.api_key_metadata WHERE id = $1",
            stored.id,
        )
        assert row["nickname"] == "Updated Nickname"
        assert row["validation_status"] == "unvalidated"
        assert row["status"] == "disabled"
        assert row["sharing_scope"] == "owner_only"
        assert row["billing_tier"] == "unknown"


# ==============================================================================
# 2. OpenRouter Probe & Credential Billing Verification Tests (Finding B)
# ==============================================================================


@pytest.mark.asyncio
async def test_probe_openrouter_free_tier_extracted():
    """When OpenRouter returns is_free_tier: true, probe sets billing_tier = 'verified_free_only'."""
    mock_resp = httpx.Response(
        status_code=200,
        json={
            "data": {
                "label": "My Free Key",
                "usage": 0,
                "limit": None,
                "is_free_tier": True,
            }
        },
    )
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = await probe_openrouter("sk-or-free-test-key")
        assert res.outcome.value == "valid"
        assert res.billing_tier == "verified_free_only"
        assert res.billing_error is None


@pytest.mark.asyncio
async def test_probe_openrouter_paid_tier_detected():
    """When OpenRouter returns is_free_tier: false, probe sets billing_tier = 'paid'."""
    mock_resp = httpx.Response(
        status_code=200,
        json={
            "data": {
                "label": "My Paid Key",
                "usage": 15.5,
                "limit": 100,
                "is_free_tier": False,
            }
        },
    )
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = await probe_openrouter("sk-or-paid-test-key")
        assert res.outcome.value == "valid"
        assert res.billing_tier == "paid"


# ==============================================================================
# 3. Candidate Discovery Hardening (Finding B & C)
# ==============================================================================


@pytest.mark.asyncio
async def test_shared_candidate_requires_verified_free_only_and_fresh_verification():
    """Candidate discovery for shared pool excludes credentials with unknown billing or stale verification."""
    user_owner = await create_test_user("owner@studyhub.edu")
    user_consumer = await create_test_user("consumer@studyhub.edu")

    # Credential 1: billing_tier = 'unknown' (e.g. Groq or unverified key)
    cred_unknown = await store_credential(
        owner_id=user_owner,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Unknown Billing Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_unknown.id)

    # Credential 2: billing_tier = 'paid'
    cred_paid = await store_credential(
        owner_id=user_owner,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Paid Billing Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_paid.id)

    # Credential 3: billing_tier = 'verified_free_only' but STALE (> 30 days)
    cred_stale = await store_credential(
        owner_id=user_owner,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Stale Verified Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_stale.id)

    # Credential 4: billing_tier = 'verified_free_only' and FRESH
    cred_valid_free = await store_credential(
        owner_id=user_owner,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Fresh Verified Free Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_valid_free.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        # Mark all valid in validation_status
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'unknown',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_unknown.id,
        )
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'paid',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_paid.id,
        )
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'verified_free_only',
                billing_verified_at = now() - interval '35 days'
            WHERE id = $1
            """,
            cred_stale.id,
        )
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'verified_free_only',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_valid_free.id,
        )

    # 1. Discover with explicit valid free key succeeds
    candidates = await discover_cloud_candidates(
        user_id=user_consumer,
        requested_provider_id="openrouter",
        requested_model_id="openrouter/free",
        explicit_credential_id=cred_valid_free.id,
        credential_scope="eligible_pool",
    )
    assert len(candidates) > 0
    assert candidates[0].credential_id == cred_valid_free.id

    # 2. Explicit key with unknown billing fails with AuthorizationError
    from app.core.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        await discover_cloud_candidates(
            user_id=user_consumer,
            requested_provider_id="openrouter",
            requested_model_id="openrouter/free",
            explicit_credential_id=cred_unknown.id,
            credential_scope="eligible_pool",
        )

    # 3. Explicit key with paid billing fails with AuthorizationError
    with pytest.raises(AuthorizationError):
        await discover_cloud_candidates(
            user_id=user_consumer,
            requested_provider_id="openrouter",
            requested_model_id="openrouter/free",
            explicit_credential_id=cred_paid.id,
            credential_scope="eligible_pool",
        )

    # 4. Explicit key with stale verification (>30d) fails with AuthorizationError
    with pytest.raises(AuthorizationError):
        await discover_cloud_candidates(
            user_id=user_consumer,
            requested_provider_id="openrouter",
            requested_model_id="openrouter/free",
            explicit_credential_id=cred_stale.id,
            credential_scope="eligible_pool",
        )


# ==============================================================================
# 4. OpenRouter Dynamic Alias & Unexpected Billable Usage (Finding C & Section 6)
# ==============================================================================


@pytest.mark.asyncio
async def test_unexpected_billable_usage_on_shared_credential_triggers_incident(client: httpx.AsyncClient):
    """If a shared execution resolves to a paid model or reports non-zero cost, the credential is immediately revoked/disabled."""
    owner_id = await create_test_user("owner_billable@studyhub.edu")
    consumer_id = await create_test_user("consumer_billable@studyhub.edu")

    # Contributor key
    cred = await store_credential(
        owner_id=owner_id,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Free Contributed Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'verified_free_only',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred.id,
        )

    # Consumer also needs a qualifying key to pass gate
    c_cred = await store_credential(
        owner_id=consumer_id,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Consumer Own Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(c_cred.id)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'verified_free_only',
                billing_verified_at = now()
            WHERE id = $1
            """,
            c_cred.id,
        )

    headers = setup_auth(consumer_id, email="consumer_billable@studyhub.edu")

    # Mock OpenRouter adapter stream returning a PAID model upstream substitution (e.g. gpt-4o)
    async def mock_bad_stream(*args, **kwargs) -> AsyncIterator[NormalizedStreamChunk]:
        yield NormalizedStreamChunk(
            delta="Incurring charges",
            resolved_model="openai/gpt-4o",  # Non-free model!
        )
        yield NormalizedStreamChunk(
            finish_reason="stop",
            usage=NormalizedUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                usage_source="authoritative",
            ),
            resolved_model="openai/gpt-4o",
        )

    with patch("app.modules.llm_gateway.providers.openrouter.OpenRouterAdapter.stream", side_effect=mock_bad_stream):
        resp = await client.post(
            "/api/v1/llm/chat/completions",
            json={
                "provider_id": "openrouter",
                "model_id": "openrouter/free",
                "credential_id": str(cred.id),
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "idempotency_key": f"test-sec-alias-{uuid4().hex[:8]}",
            },
            headers=headers,
        )
        # Should stream generation_error SSE event
        body_text = resp.text
        assert "generation_error" in body_text

    # Verify that the shared credential was immediately disabled
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM public.api_key_metadata WHERE id = $1",
            cred.id,
        )
        assert status == "disabled"

        # Verify audit log entry
        audit_row = await conn.fetchrow(
            """
            SELECT action, payload FROM public.audit_logs
            WHERE target_id = $1 AND action = 'shared_pool.unexpected_billable_usage'
            """,
            cred.id,
        )
        assert audit_row is not None
        assert audit_row["action"] == "shared_pool.unexpected_billable_usage"


# ==============================================================================
# 5. Chat Contribution Gate Comprehensive Coverage (Finding D & Section 7)
# ==============================================================================


@pytest.mark.asyncio
async def test_contribution_gate_distinguishes_paid_and_unverified_credentials():
    """Contribution gate rejects paid or unverified credentials with actionable reason codes."""
    user_id = await create_test_user("gate_tester@studyhub.edu")

    # 1. No credentials
    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is False
    assert reason == "NO_CREDENTIAL_CONTRIBUTED"

    # 2. Add credential with billing_tier = 'paid'
    cred_paid = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="My Paid OpenRouter Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_paid.id)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'paid',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_paid.id,
        )

    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is False
    assert reason == "PAID_CREDENTIAL_INELIGIBLE"

    # 3. Add credential with billing_tier = 'unknown'
    cred_unknown = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="Unknown Tier Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_unknown.id)
    async with pool.acquire() as conn:
        # Delete paid key so unknown is evaluated
        await conn.execute("DELETE FROM public.api_key_metadata WHERE id = $1", cred_paid.id)
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET validation_status = 'valid',
                billing_tier = 'unknown',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_unknown.id,
        )

    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is False
    assert reason == "BILLING_VERIFICATION_REQUIRED"

    # 4. Add credential with billing_tier = 'verified_free_only'
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.api_key_metadata
            SET billing_tier = 'verified_free_only',
                billing_verified_at = now()
            WHERE id = $1
            """,
            cred_unknown.id,
        )

    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is True
    assert reason == "QUALIFYING_CONTRIBUTION_ACTIVE"
    assert count == 1

    # 5. Temporary quota exhaustion preserves entitlement
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET status = 'quota_exhausted' WHERE id = $1",
            cred_unknown.id,
        )

    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is True
    assert reason == "QUALIFYING_CONTRIBUTION_ACTIVE"
    assert count == 1

    # 6. Revocation/Disabling removes entitlement
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET status = 'disabled' WHERE id = $1",
            cred_unknown.id,
        )

    eligible, reason, count = await check_chat_contribution_entitlement(user_id)
    assert eligible is False
    assert reason == "CREDENTIAL_INACTIVE"
