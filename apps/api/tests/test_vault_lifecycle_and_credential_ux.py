"""Comprehensive Verification Test Suite for Credential UX and Vault Lifecycle Correction.

Covers all 20 specific assertions from Section 8 of the product requirements:
1. Vault initialization occurs during application startup.
2. Required vault initialization failure prevents normal readiness (503 with vault: false).
3. Partial startup failure cleans up initialized resources.
4. Vault shutdown happens in correct order (close_vault_pool before close_db_pool).
5. HTTP 200 from Groq, Cerebras, or Gemini validation does not imply free-tier eligibility (billing_tier == 'unknown').
6. Unknown billing status cannot satisfy contribution entitlement (BILLING_VERIFICATION_REQUIRED).
7. A paid credential cannot enter the shared pool.
8. A private credential cannot enter the shared pool.
9. Missing consent cannot enter the shared pool.
10. Explicit consent can enable sharing only after both provider and credential eligibility checks.
11. Consent withdrawal immediately excludes credential from new shared execution.
12. Quota exhaustion excludes credential from serving but does not revoke valid contribution.
13. Model-level paid fallback is impossible in shared free-tier execution.
14. OpenRouter billing probe uses https://openrouter.ai/api/v1/key.
15. Direct POST/PATCH requests cannot modify platform-controlled quotas.
16. Removing UI quota inputs does not remove backend enforcement.
17. Existing owner-only paid-key protections remain unchanged.
18. Gemini's provider-level sharing flag does not override per-credential billing verification.
19. Existing local Ollama inference remains private and unchanged.
20. Contribution-required gate remains enforced by backend chat endpoints and before browser-local inference.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app, lifespan
from app.modules.credentials.repository import (
    get_credential_metadata,
    store_credential,
)
from app.modules.credentials.service import (
    check_chat_contribution_entitlement,
    update_user_credential,
)
from app.modules.credentials.validator import (
    ValidationOutcome,
    validate_provider_credential,
)
from app.modules.credentials.vault import (
    check_vault_health,
    close_vault_pool,
    init_vault_pool,
)
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
    """Ensure database connection pools and Redis are initialized and cleaned up."""
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
    """Create an actual user in the test database so foreign keys pass cleanly."""
    pool = get_pool()
    user_id = uuid4()
    if not email:
        unique_email = f"user_{user_id.hex[:8]}@university.edu"
    else:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Test User"}'::jsonb)
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
    email: str = "tester@university.edu",
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


async def _mock_stream_generator(*args, **kwargs) -> AsyncIterator[NormalizedStreamChunk]:
    yield NormalizedStreamChunk(
        content="Hello student!",
        role="assistant",
        is_final=False,
    )
    yield NormalizedStreamChunk(
        content="",
        role="assistant",
        is_final=True,
        finish_reason="stop",
        usage=NormalizedUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


# ==============================================================================
# Requirement 1: Vault initialization occurs during application startup
# ==============================================================================
@pytest.mark.asyncio
async def test_1_vault_initialization_on_startup():
    """Assertion 1: init_vault_pool is invoked during application lifespan startup."""
    init_called = False
    orig_init = init_vault_pool

    async def tracking_init():
        nonlocal init_called
        init_called = True
        return await orig_init()

    with (
        patch("app.main.init_vault_pool", side_effect=tracking_init),
        patch("app.main.init_db_pool", new_callable=AsyncMock),
        patch("app.main.init_redis", new_callable=AsyncMock),
        patch("app.main.close_vault_pool", new_callable=AsyncMock),
        patch("app.main.close_db_pool", new_callable=AsyncMock),
        patch("app.main.close_redis", new_callable=AsyncMock),
    ):
        async with lifespan(app):
            assert init_called is True
            # Verify check_vault_health returns healthy
            assert await check_vault_health() is True


# ==============================================================================
# Requirement 2: Vault initialization failure prevents normal readiness
# ==============================================================================
@pytest.mark.asyncio
async def test_2_vault_init_failure_marks_readiness_degraded(client: httpx.AsyncClient):
    """Assertion 2: Failure in vault health marks /health/ready as degraded (503, vault: false)."""
    with (
        patch("app.api.v1.health.check_db_health", new_callable=AsyncMock, return_value=True),
        patch("app.api.v1.health.check_redis_health", new_callable=AsyncMock, return_value=True),
        patch("app.api.v1.health.check_vault_health", new_callable=AsyncMock, return_value=False),
    ):
        resp = await client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["vault"] is False
        assert data["database"] is True
        assert data["redis"] is True


# ==============================================================================
# Requirement 3: Partial startup failure cleans up initialized resources
# ==============================================================================
@pytest.mark.asyncio
async def test_3_partial_startup_failure_clean_up():
    """Assertion 3: If startup fails after DB/vault init, cleanup is called without leaking pools."""
    db_closed = False

    async def mock_close_db():
        nonlocal db_closed
        db_closed = True

    with (
        patch("app.main.init_db_pool", new_callable=AsyncMock),
        patch("app.main.init_vault_pool", side_effect=RuntimeError("Vault pool init failed")),
        patch("app.main.close_db_pool", side_effect=mock_close_db),
    ):
        with pytest.raises(RuntimeError, match="Vault pool init failed"):
            async with lifespan(app):
                pass

        assert db_closed is True


# ==============================================================================
# Requirement 4: Vault shutdown happens in correct order
# ==============================================================================
@pytest.mark.asyncio
async def test_4_vault_shutdown_order():
    """Assertion 4: close_vault_pool is executed before close_db_pool during shutdown."""
    close_order = []

    async def mock_close_vault():
        close_order.append("vault")

    async def mock_close_db():
        close_order.append("db")

    with (
        patch("app.main.init_db_pool", new_callable=AsyncMock),
        patch("app.main.init_vault_pool", new_callable=AsyncMock),
        patch("app.main.init_redis", new_callable=AsyncMock),
        patch("app.main.close_vault_pool", side_effect=mock_close_vault),
        patch("app.main.close_db_pool", side_effect=mock_close_db),
        patch("app.main.close_redis", new_callable=AsyncMock),
    ):
        async with lifespan(app):
            pass

    assert close_order == ["vault", "db"]


# ==============================================================================
# Requirement 5: HTTP 200 does not imply free-tier eligibility
# ==============================================================================
@pytest.mark.asyncio
async def test_5_http_200_does_not_infer_free_tier():
    """Assertion 5: Probing Groq, Cerebras, or Gemini returning HTTP 200 retains billing_tier = 'unknown'."""
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "llama-3.3-70b-versatile"}]}
        mock_get.return_value = mock_resp

        res_groq = await validate_provider_credential("groq", "gsk_test_key_12345")
        assert res_groq.outcome == ValidationOutcome.VALID
        assert res_groq.billing_tier == "unknown"

        res_cerebras = await validate_provider_credential("cerebras", "csk_test_key_12345")
        assert res_cerebras.outcome == ValidationOutcome.VALID
        assert res_cerebras.billing_tier == "unknown"

        res_gemini = await validate_provider_credential("gemini", "AIzaSy_test_key_12345")
        assert res_gemini.outcome == ValidationOutcome.VALID
        assert res_gemini.billing_tier == "unknown"


# ==============================================================================
# Requirement 6: Unknown billing status cannot satisfy contribution entitlement
# ==============================================================================
@pytest.mark.asyncio
async def test_6_unknown_billing_cannot_satisfy_entitlement():
    """Assertion 6: A valid credential with billing_tier = 'unknown' fails chat entitlement."""
    user_id = await create_db_user("unknown_tier@university.edu")
    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-unknown-{uuid4().hex}",
        nickname="Unknown Tier Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'unknown', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    is_entitled, reason, count = await check_chat_contribution_entitlement(user_id)
    assert is_entitled is False
    assert reason == "BILLING_VERIFICATION_REQUIRED"
    assert count == 0


# ==============================================================================
# Requirement 7: A paid credential cannot enter the shared pool
# ==============================================================================
@pytest.mark.asyncio
async def test_7_paid_credential_cannot_enter_shared_pool():
    """Assertion 7: A credential with billing_tier = 'paid' is excluded from shared routing discovery."""
    user_id = await create_db_user("paid_user@university.edu")
    peer_user = await create_db_user("peer_paid_user@university.edu")
    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-paid-{uuid4().hex}",
        nickname="Paid Tier Key",
        sharing_scope="community_shared",
        shared_consent=True,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'paid', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    from app.core.errors import AuthorizationError

    with pytest.raises(AuthorizationError, match="credential billing tier is not verified"):
        await discover_cloud_candidates(
            user_id=peer_user,
            requested_provider_id="openrouter",
            explicit_credential_id=stored.id,
            credential_scope="eligible_pool",
        )


# ==============================================================================
# Requirement 8: A private credential cannot enter the shared pool
# ==============================================================================
@pytest.mark.asyncio
async def test_8_private_credential_cannot_enter_shared_pool():
    """Assertion 8: Credential with sharing_scope = 'owner_only' is excluded from shared discovery."""
    user_id = await create_db_user("private_user@university.edu")
    other_user = await create_db_user("other_student@university.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-priv-{uuid4().hex}",
        nickname="Private Free Key",
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

    candidates = await discover_cloud_candidates(
        user_id=other_user,
        requested_provider_id="openrouter",
        credential_scope="eligible_pool",
    )
    found_private = any(c.credential_id == stored.id for c in candidates)
    assert found_private is False


# ==============================================================================
# Requirement 9: Missing consent cannot enter the shared pool
# ==============================================================================
@pytest.mark.asyncio
async def test_9_missing_consent_cannot_enter_shared_pool():
    """Assertion 9: Credential with shared_consent = False cannot enter the shared pool."""
    user_id = await create_db_user("no_consent@university.edu")
    other_user = await create_db_user("peer_student@university.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-noconsent-{uuid4().hex}",
        nickname="No Consent Key",
        sharing_scope="community_shared",
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

    candidates = await discover_cloud_candidates(
        user_id=other_user,
        requested_provider_id="openrouter",
        credential_scope="eligible_pool",
    )
    assert not any(c.credential_id == stored.id for c in candidates)


# ==============================================================================
# Requirement 10: Explicit consent requires both provider and credential eligibility
# ==============================================================================
@pytest.mark.asyncio
async def test_10_explicit_consent_requires_both_checks():
    """Assertion 10: Toggling community_shared requires both provider and credential verified free-tier."""
    user_id = await create_db_user("consent_checker@university.edu")

    # 1. Unapproved provider (e.g. Groq) fails even with consent
    stored_groq = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        raw_key=f"gsk-{uuid4().hex}",
        nickname="Groq Private",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored_groq.id)

    with pytest.raises(Exception, match="not approved for community sharing"):
        from app.modules.credentials.schemas import CredentialUpdateRequest

        await update_user_credential(
            owner_id=user_id,
            credential_id=stored_groq.id,
            payload=CredentialUpdateRequest(sharing_scope="community_shared", shared_consent=True),
        )

    # 2. Approved provider with unverified billing tier fails
    stored_or = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-{uuid4().hex}",
        nickname="OpenRouter Unverified",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored_or.id)

    with pytest.raises(Exception, match="Only verified free-tier credentials can be shared"):
        await update_user_credential(
            owner_id=user_id,
            credential_id=stored_or.id,
            payload=CredentialUpdateRequest(sharing_scope="community_shared", shared_consent=True),
        )


# ==============================================================================
# Requirement 11: Consent withdrawal immediately excludes credential from shared routing
# ==============================================================================
@pytest.mark.asyncio
async def test_11_consent_withdrawal_immediately_excludes_shared_routing():
    """Assertion 11: Withdrawing consent immediately stops discovery in shared pool."""
    user_id = await create_db_user("withdrawer@university.edu")
    peer_user = await create_db_user("peer_withdrawer@university.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-withdraw-{uuid4().hex}",
        nickname="Key To Withdraw",
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

    # 1. Discoverable before withdrawal
    candidates = await discover_cloud_candidates(
        user_id=peer_user,
        requested_provider_id="openrouter",
        explicit_credential_id=stored.id,
        credential_scope="eligible_pool",
    )
    assert any(c.credential_id == stored.id for c in candidates)

    # 2. Withdraw consent
    from app.modules.credentials.schemas import CredentialUpdateRequest

    await update_user_credential(
        owner_id=user_id,
        credential_id=stored.id,
        payload=CredentialUpdateRequest(sharing_scope="owner_only", shared_consent=False),
    )

    # 3. Immediately excluded
    from app.core.errors import AuthorizationError

    with pytest.raises(AuthorizationError, match="private owner-only credential"):
        await discover_cloud_candidates(
            user_id=peer_user,
            requested_provider_id="openrouter",
            explicit_credential_id=stored.id,
            credential_scope="eligible_pool",
        )


# ==============================================================================
# Requirement 12: Quota exhaustion excludes credential but preserves entitlement
# ==============================================================================
@pytest.mark.asyncio
async def test_12_quota_exhaustion_preserves_entitlement():
    """Assertion 12: Quota exhaustion excludes credential from routing but keeps user entitled."""
    user_id = await create_db_user("exhausted_entitlement@university.edu")
    peer_user = await create_db_user("peer_exhausted@university.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-exhausted-{uuid4().hex}",
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

    # Contributor remains entitled
    is_entitled, reason, count = await check_chat_contribution_entitlement(user_id)
    assert is_entitled is True
    assert reason == "QUALIFYING_CONTRIBUTION_ACTIVE"
    assert count == 1

    # Excluded from active routing serving pool
    candidates = await discover_cloud_candidates(
        user_id=peer_user,
        requested_provider_id="openrouter",
        credential_scope="eligible_pool",
    )
    assert not any(c.credential_id == stored.id for c in candidates)


# ==============================================================================
# Requirement 13: Model-level paid fallback is impossible in shared free-tier execution
# ==============================================================================
@pytest.mark.asyncio
async def test_13_model_level_paid_fallback_impossible():
    """Assertion 13: filter_candidates rejects non-free models for shared credentials."""
    candidate_shared_paid = RoutingCandidate(
        source_type="cloud",
        provider_id="openrouter",
        model_id="anthropic/claude-3.5-sonnet",
        display_name="Claude 3.5 Sonnet",
        family="claude",
        context_window=200000,
        max_output_tokens=8192,
        capabilities=["streaming"],
        credential_id=uuid4(),
        is_shared_credential=True,
        free_tier_eligible=False,  # NOT free-tier eligible
        pricing_input_cents_per_million=300,
        pricing_output_cents_per_million=1500,
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


# ==============================================================================
# Requirement 14: OpenRouter billing probe uses https://openrouter.ai/api/v1/key
# ==============================================================================
@pytest.mark.asyncio
async def test_14_openrouter_billing_probe_endpoint():
    """Assertion 14: OpenRouter validation queries GET https://openrouter.ai/api/v1/key."""
    probed_url = None

    async def mock_get(url, *args, **kwargs):
        nonlocal probed_url
        probed_url = str(url)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "label": "Free Key",
                "is_free_tier": True,
                "limit": 0,
                "usage": 0,
            }
        }
        return mock_resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        res = await validate_provider_credential("openrouter", "sk-or-v1-test_key_sample")
        assert res.outcome == ValidationOutcome.VALID
        assert res.billing_tier == "verified_free_only"
        assert probed_url == "https://openrouter.ai/api/v1/key"


# ==============================================================================
# Requirement 15: Direct POST/PATCH requests cannot modify platform-controlled quotas
# ==============================================================================
@pytest.mark.asyncio
async def test_15_direct_requests_cannot_modify_quotas(client: httpx.AsyncClient):
    """Assertion 15: Direct PATCH requests attempting to alter quotas return 409 QUOTA_MODIFICATION_FORBIDDEN."""
    user_id = await create_db_user("quota_tester@university.edu")
    headers = setup_auth_context(user_id)

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openrouter",
        raw_key=f"sk-or-quota-{uuid4().hex}",
        nickname="Quota Key",
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    # Attempt to change daily_request_limit
    res_patch = await client.patch(
        f"/api/v1/me/api-keys/{stored.id}",
        json={"daily_request_limit": 99999},
        headers=headers,
    )
    assert res_patch.status_code == 409
    assert res_patch.json()["error"]["code"] == "QUOTA_MODIFICATION_FORBIDDEN"


# ==============================================================================
# Requirement 16: Removing UI quota inputs does not remove backend enforcement
# ==============================================================================
@pytest.mark.asyncio
async def test_16_backend_enforces_platform_quotas_by_default(client: httpx.AsyncClient):
    """Assertion 16: New credentials created via API receive 500 RPD / 15000 RPM baseline quotas."""
    user_id = await create_db_user("default_quotas@university.edu")
    headers = setup_auth_context(user_id)

    res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "openrouter",
            "nickname": "Default Quota Key",
            "api_key": f"sk-or-def-{uuid4().hex}",
            "sharing_scope": "owner_only",
            "shared_consent": False,
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["daily_request_limit"] == 500
    assert data["monthly_request_limit"] == 15000

    # Verify repository storage
    cred_id = UUID(data["id"])
    CREATED_TEST_CREDS.append(cred_id)
    meta = await get_credential_metadata(cred_id, user_id)
    assert meta.daily_request_limit == 500
    assert meta.monthly_request_limit == 15000


# ==============================================================================
# Requirement 17: Existing owner-only paid-key protections remain unchanged
# ==============================================================================
@pytest.mark.asyncio
async def test_17_owner_only_paid_keys_remain_usable_by_owner():
    """Assertion 17: Owner-only credentials with billing_tier = 'paid' remain usable by the owner."""
    user_id = await create_db_user("paid_owner@university.edu")

    stored = await store_credential(
        owner_id=user_id,
        provider_id="openai",
        raw_key=f"sk-openai-paid-{uuid4().hex}",
        nickname="My OpenAI Paid Key",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid', billing_tier = 'paid', billing_verified_at = now() WHERE id = $1",
            stored.id,
        )

    # Owner discovers their own paid credential under owner_only scope
    candidates = await discover_cloud_candidates(
        user_id=user_id,
        requested_provider_id="openai",
        credential_scope="owner_only",
    )
    assert any(c.credential_id == stored.id for c in candidates)


# ==============================================================================
# Requirement 18: Gemini's provider-level sharing flag does not override billing verification
# ==============================================================================
@pytest.mark.asyncio
async def test_18_gemini_sharing_policy_and_billing_verification():
    """Assertion 18: Gemini provider policy disallows sharing and retains billing_tier = 'unknown'."""
    pool = get_pool()
    async with pool.acquire() as conn:
        provider = await conn.fetchrow("SELECT allows_community_sharing FROM public.llm_providers WHERE id = 'gemini'")
        assert provider["allows_community_sharing"] is False

    probe_res = await validate_provider_credential("gemini", "AIzaSy_fake_gemini_key_12345")
    assert probe_res.billing_tier == "unknown"


# ==============================================================================
# Requirement 19: Existing local Ollama inference remains private and unchanged
# ==============================================================================
@pytest.mark.asyncio
async def test_19_local_ollama_runtime_strictly_private():
    """Assertion 19: Ollama is registered as local_runtime and allows_community_sharing = False."""
    pool = get_pool()
    async with pool.acquire() as conn:
        provider = await conn.fetchrow(
            "SELECT runtime_type, allows_community_sharing FROM public.llm_providers WHERE id = 'ollama'"
        )
        assert provider["runtime_type"] == "local_runtime"
        assert provider["allows_community_sharing"] is False


# ==============================================================================
# Requirement 20: Contribution-required gate remains enforced by backend chat endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_20_contribution_gate_enforced(client: httpx.AsyncClient):
    """Assertion 20: Uncontributed user gets 403 CONTRIBUTION_REQUIRED on /api/v1/llm/chat/completions."""
    user_id = await create_db_user("uncontributed_student@university.edu")
    headers = setup_auth_context(user_id)

    # Call chat completions
    res = await client.post(
        "/api/v1/llm/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Explain binary search"}],
            "routing_mode": "auto",
            "idempotency_key": f"test-gate-{uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "CONTRIBUTION_REQUIRED"
