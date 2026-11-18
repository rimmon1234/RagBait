"""Comprehensive distributed rate limiting tests for admission control (M2.4)."""

from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.errors import AdmissionRejectionError
from app.modules.admission.rate_limiter import (
    RateLimitScope,
    release_rate_slots,
    reserve_rate_slots,
)
from app.modules.admission.schemas import AdmissionRequest
from app.modules.admission.service import (
    mark_dispatched,
    reserve_generation,
    settle_reservation,
)
from app.modules.credentials.repository import store_credential, update_validation_status
from app.modules.credentials.vault import close_vault_pool, init_vault_pool

CREATED_TEST_USERS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_vault_pool()
    await init_redis()

    # Clear test rate limit keys in Redis before each test
    redis_conn = get_redis()
    if redis_conn:
        keys = await redis_conn.keys("ragbait:rate:*")
        keys.extend(await redis_conn.keys("ragbait:concurrency:*"))
        keys.extend(await redis_conn.keys("ragbait:cooldown:*"))
        if keys:
            await redis_conn.delete(*keys)

    yield

    if CREATED_TEST_USERS:
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM auth.users WHERE id = ANY($1)", CREATED_TEST_USERS)
        except Exception:
            pass
        CREATED_TEST_USERS.clear()

    await close_vault_pool()
    await close_db_pool()
    await close_redis()


async def create_test_contributor() -> tuple[UUID, UUID]:
    """Helper to provision a user and an active, validated credential."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"contributor_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Test Contributor"}'::jsonb)
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

    raw_key = f"gsk_test_{uuid4().hex}"
    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Rate Test Groq Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=1000,
        monthly_request_limit=30000,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


# ==============================================================================
# 1. User & Credential RPM and Burst Tests
# ==============================================================================


@pytest.mark.anyio
async def test_user_rate_limit_and_burst_enforcement():
    """User can make requests up to limit + burst; subsequent requests rejected with 429."""
    user_id, cred_id = await create_test_contributor()

    # User RPM limit is 10, burst is 1 -> total allowed is 11
    # We use different credentials or rate slots to test strictly user scope
    scopes = [
        RateLimitScope("user", f"ragbait:rate:user:{user_id}", limit=10, burst=1),
    ]

    # First 11 reservations succeed
    for i in range(11):
        await reserve_rate_slots(scopes, member_id=f"req_{i}", window_seconds=60)

    # 12th reservation exceeds capacity (10 + 1) -> rejected with 429
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_rate_slots(scopes, member_id="req_12", window_seconds=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after >= 1
    assert "user" in exc_info.value.message


@pytest.mark.anyio
async def test_credential_rate_limit_and_burst_enforcement():
    """Credential limit is 5 RPM + 1 burst -> 7th request is rejected."""
    user_id, cred_id = await create_test_contributor()

    scopes = [
        RateLimitScope("credential", f"ragbait:rate:credential:{cred_id}", limit=5, burst=1),
    ]

    # First 6 reservations succeed
    for i in range(6):
        await reserve_rate_slots(scopes, member_id=f"cred_req_{i}", window_seconds=60)

    # 7th reservation exceeds capacity (5 + 1) -> rejected
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_rate_slots(scopes, member_id="cred_req_7", window_seconds=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after >= 1
    assert "credential" in exc_info.value.message


@pytest.mark.anyio
async def test_strictest_applicable_quota_precedence():
    """When provider quota (e.g. 3 RPM) is stricter than credential limit, provider limit wins."""
    scopes = [
        RateLimitScope("user", "ragbait:rate:user:mock_user", limit=10, burst=1),
        RateLimitScope("credential", "ragbait:rate:credential:mock_cred", limit=5, burst=1),
        RateLimitScope("provider", "ragbait:rate:provider:mock_provider", limit=3, burst=0),
    ]

    # 3 requests succeed
    for i in range(3):
        await reserve_rate_slots(scopes, member_id=f"strict_req_{i}", window_seconds=60)

    # 4th request exceeds provider quota (limit 3 + burst 0 = 3)
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_rate_slots(scopes, member_id="strict_req_4", window_seconds=60)

    assert exc_info.value.status_code == 429
    assert "provider" in exc_info.value.message


@pytest.mark.anyio
async def test_pre_dispatch_cancellation_releases_rate_slot():
    """Releasing an uncommitted reservation refunds its rate slot in Redis."""
    user_id, cred_id = await create_test_contributor()

    scopes = [
        RateLimitScope("credential", f"ragbait:rate:credential:{cred_id}", limit=2, burst=0),
    ]

    # Consume 2 slots (capacity full)
    await reserve_rate_slots(scopes, member_id="req_a", window_seconds=60)
    await reserve_rate_slots(scopes, member_id="req_b", window_seconds=60)

    # 3rd is rejected
    with pytest.raises(AdmissionRejectionError):
        await reserve_rate_slots(scopes, member_id="req_c", window_seconds=60)

    # Release req_b
    await release_rate_slots(scopes, member_id="req_b")

    # Now req_c should succeed!
    await reserve_rate_slots(scopes, member_id="req_c", window_seconds=60)


@pytest.mark.anyio
async def test_committed_attempt_remains_counted_on_simulated_error():
    """A committed dispatch remains counted toward rate limits even after simulated error."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_committed_error_1",
        request_id="req_committed_err_1",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )

    reservation = await reserve_generation(req)
    await mark_dispatched(reservation.reservation_id)

    # Settle with provider 500 error category
    await settle_reservation(
        reservation.reservation_id,
        req=pytest.importorskip("app.modules.admission.schemas").SettlementRequest(
            actual_input_tokens=0,
            actual_output_tokens=0,
            actual_cost_cents=0,
            error_category="upstream_500_internal_error",
        ),
    )

    # Check that rate slot is STILL in Redis and was not released
    redis_conn = get_redis()
    user_key = f"ragbait:rate:user:{user_id}"
    score = await redis_conn.zscore(user_key, "req_committed_err_1")
    assert score is not None


@pytest.mark.anyio
async def test_independent_user_and_credential_scopes():
    """Different users and credentials have separate rate limit buckets."""
    user_a, cred_a = await create_test_contributor()
    user_b, cred_b = await create_test_contributor()

    scopes_a = [RateLimitScope("user", f"ragbait:rate:user:{user_a}", limit=1, burst=0)]
    scopes_b = [RateLimitScope("user", f"ragbait:rate:user:{user_b}", limit=1, burst=0)]

    # User A uses their 1 slot
    await reserve_rate_slots(scopes_a, member_id="req_a_1", window_seconds=60)

    # User A is now blocked
    with pytest.raises(AdmissionRejectionError):
        await reserve_rate_slots(scopes_a, member_id="req_a_2", window_seconds=60)

    # User B is completely unaffected and succeeds!
    await reserve_rate_slots(scopes_b, member_id="req_b_1", window_seconds=60)


@pytest.mark.anyio
async def test_no_plaintext_keys_in_redis_rate_keys():
    """Verify that Redis rate limit keys never contain raw secrets."""
    redis_conn = get_redis()
    keys = await redis_conn.keys("ragbait:rate:*")
    for k in keys:
        assert "gsk_" not in k
        assert "sk-proj" not in k
        assert "AIza" not in k
