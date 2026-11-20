"""Tests for failure scenarios and fail-closed guarantees in admission control (M2.4)."""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.errors import AppError
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.concurrency import (
    acquire_concurrency_lease,
)
from app.modules.admission.errors import ConcurrencyLimitError
from app.modules.admission.rate_limiter import (
    RateLimitScope,
    reserve_rate_slots,
)
from app.modules.admission.schemas import AdmissionRequest
from app.modules.admission.service import (
    reserve_generation,
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
    unique_email = f"failclose_{user_id.hex[:8]}@university.edu"

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
        nickname="Failclose Groq Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=1000,
        monthly_request_limit=30000,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


# ==============================================================================
# 1. Redis Unavailable Fail-Closed Tests
# ==============================================================================


@pytest.mark.anyio
async def test_redis_offline_rate_limiter_fails_closed():
    """Rate limiter fails closed with HTTP 503 when Redis is unavailable."""
    scope = RateLimitScope("user", "ragbait:rate:user:fake", limit=10, burst=1)

    with patch("app.modules.admission.rate_limiter.get_redis", return_value=None):
        with pytest.raises(AppError) as exc_info:
            await reserve_rate_slots([scope], member_id="req_offline")
        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"


@pytest.mark.anyio
async def test_redis_offline_concurrency_fails_closed():
    """Concurrency manager fails closed with HTTP 503 when Redis is unavailable."""
    with patch("app.modules.admission.concurrency.get_redis", return_value=None):
        with pytest.raises(AppError) as exc_info:
            await acquire_concurrency_lease(user_id=uuid4(), credential_id=uuid4())
        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"


@pytest.mark.anyio
async def test_redis_offline_admission_service_fails_closed():
    """End-to-end reserve_generation fails closed when Redis is offline."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=100,
        max_output_tokens=200,
    )

    with patch("app.modules.admission.rate_limiter.get_redis", return_value=None):
        with pytest.raises(AppError) as exc_info:
            await reserve_generation(req)
        assert exc_info.value.status_code == 503


# ==============================================================================
# 2. PostgreSQL Unavailable Fail-Closed Tests
# ==============================================================================


@pytest.mark.anyio
async def test_postgres_offline_admission_service_fails_closed():
    """reserve_generation fails closed when PostgreSQL database pool is unavailable."""
    user_id = uuid4()
    cred_id = uuid4()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=100,
        max_output_tokens=200,
    )

    with patch(
        "app.modules.admission.service.get_pool",
        side_effect=RuntimeError("PostgreSQL pool connection failed"),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await reserve_generation(req)
        assert "PostgreSQL pool connection failed" in str(exc_info.value)


# ==============================================================================
# 3. Partial Failure Compensation / Rollback Tests
# ==============================================================================


@pytest.mark.anyio
async def test_partial_failure_db_error_rolls_back_redis_state():
    """When Redis rate & concurrency succeed but PostgreSQL transaction fails, Redis state is rolled back."""
    user_id, cred_id = await create_test_contributor()
    redis_conn = get_redis()
    assert redis_conn is not None

    req_id = f"req_{uuid4().hex}"
    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=req_id,
        estimated_input_tokens=100,
        max_output_tokens=200,
    )

    # Patch reserve_budget_transactional to simulate a database transaction crash
    with patch(
        "app.modules.admission.service.reserve_budget_transactional",
        side_effect=RuntimeError("Simulated database failure during budget locking"),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await reserve_generation(req)
        assert "Simulated database failure" in str(exc_info.value)

    # Verify that concurrency leases were rolled back from Redis
    user_conc_key = f"ragbait:concurrency:user:{user_id}"
    cred_conc_key = f"ragbait:concurrency:credential:{cred_id}"
    user_leases = await redis_conn.zcard(user_conc_key)
    cred_leases = await redis_conn.zcard(cred_conc_key)
    assert user_leases == 0
    assert cred_leases == 0

    # Verify that the rate limit slot was rolled back from Redis
    user_rate_key = f"ragbait:rate:user:{user_id}"
    cred_rate_key = f"ragbait:rate:credential:{cred_id}"
    user_score = await redis_conn.zscore(user_rate_key, req_id)
    cred_score = await redis_conn.zscore(cred_rate_key, req_id)
    assert user_score is None
    assert cred_score is None


@pytest.mark.anyio
async def test_partial_failure_concurrency_limit_rolls_back_rate_slots():
    """When Step A (rate slots) succeeds but Step B (concurrency) fails, rate slots are rolled back."""
    user_id, cred_id = await create_test_contributor()
    redis_conn = get_redis()
    assert redis_conn is not None

    # Saturate credential concurrency capacity (cap is 1)
    lease1 = await acquire_concurrency_lease(user_id, cred_id)
    assert lease1

    req_id = f"req_{uuid4().hex}"
    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=req_id,
        estimated_input_tokens=100,
        max_output_tokens=200,
    )

    # Attempting a 2nd reservation on same credential must raise ConcurrencyLimitError
    with pytest.raises(ConcurrencyLimitError) as exc_info:
        await reserve_generation(req)
    assert exc_info.value.scope == "credential"

    # Verify that the rate limit slot acquired in Step A was cleanly rolled back
    user_rate_key = f"ragbait:rate:user:{user_id}"
    cred_rate_key = f"ragbait:rate:credential:{cred_id}"
    user_score = await redis_conn.zscore(user_rate_key, req_id)
    cred_score = await redis_conn.zscore(cred_rate_key, req_id)
    assert user_score is None
    assert cred_score is None
