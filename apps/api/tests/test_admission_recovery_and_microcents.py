"""Focused verification tests for automated expiration recovery and exact microcent budget semantics.

Simulates:
1. Crash after reservation persistence.
2. Expired reserved record recovery.
3. Duplicate sweeper execution.
4. Concurrent sweepers with FOR UPDATE SKIP LOCKED.
5. Partial Redis cleanup failure resilience.
6. Budget counters correctly restored.
7. Dispatched records NOT incorrectly expired.
8. Outcome-unknown reservations retaining conservative budget.
9. 500 sequential subcent requests under a 1-cent spending budget.
10. Independent request-count limit enforcement and dashboard display comparison.
"""

import asyncio
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.errors import BudgetExhaustedError
from app.modules.admission.ledger import get_period_boundaries
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    get_usage_summary_for_user,
    mark_dispatched,
    mark_outcome_unknown,
    recover_expired_reservations,
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

    redis_conn = get_redis()
    if redis_conn:
        keys = await redis_conn.keys("ragbait:rate:*")
        keys.extend(await redis_conn.keys("ragbait:concurrency:*"))
        keys.extend(await redis_conn.keys("ragbait:lease:*"))
        if keys:
            await redis_conn.delete(*keys)

    # Expire and sweep any leftover uncommitted reservations from previous test runs
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.llm_usage_reservations SET lease_expires_at = now() - interval '1 hour' WHERE status = 'reserved'"
        )
    await recover_expired_reservations(batch_size=1000)

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


async def create_test_contributor(
    daily_req: int = 1000,
    monthly_req: int = 50000,
    daily_spend_cents: int | None = None,
    monthly_spend_cents: int | None = None,
    provider_id: str = "groq",
) -> tuple[UUID, UUID]:
    """Helper to provision a user and active credential with specific limits."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"recovery_user_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Recovery User"}'::jsonb)
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

    raw_key = f"gsk_rec_{uuid4().hex}"
    stored = await store_credential(
        owner_id=user_id,
        provider_id=provider_id,
        nickname=f"{provider_id.capitalize()} Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=daily_req,
        monthly_request_limit=monthly_req,
        daily_spending_limit_cents=daily_spend_cents,
        monthly_spending_limit_cents=monthly_spend_cents,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


# ==============================================================================
# Area 1: Automated Expiration & Recovery Tests
# ==============================================================================


@pytest.mark.anyio
async def test_worker_crash_after_reservation_and_recovery():
    """Simulate a worker crash after reservation persistence; sweeper cleanly recovers state."""
    user_id, cred_id = await create_test_contributor(daily_spend_cents=100)

    # 1. Reserve generation
    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="crash_sim_1",
        request_id="req_crash_1",
        estimated_input_tokens=500,
        max_output_tokens=500,
    )
    res = await reserve_generation(req)
    assert res.status == "reserved"

    # Verify counters hold reservation
    pool = get_pool()
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        c1 = await conn.fetchrow(
            """
            SELECT reserved_requests, reserved_cost_cents, reserved_cost_microcents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert c1["reserved_requests"] == 1
        assert c1["reserved_cost_microcents"] == res.estimated_cost_microcents

        # 2. Worker crashes here!
        # Simulate time passing so lease_expires_at is in the past
        await conn.execute(
            """
            UPDATE public.llm_usage_reservations
            SET lease_expires_at = now() - interval '5 seconds'
            WHERE id = $1
            """,
            res.reservation_id,
        )

    # 3. Automatic sweeper runs
    swept = await recover_expired_reservations(batch_size=50)
    assert swept == 1

    # 4. Verify PostgreSQL state: reservation is expired, counters refunded
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert status == "expired"

        c2 = await conn.fetchrow(
            """
            SELECT reserved_requests, reserved_cost_cents, reserved_cost_microcents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert c2["reserved_requests"] == 0
        assert c2["reserved_cost_microcents"] == 0
        assert c2["reserved_cost_cents"] == 0

        # Verify immutable event log
        event = await conn.fetchrow(
            """
            SELECT event_type, cost_cents_delta, cost_microcents_delta
            FROM public.llm_usage_events
            WHERE reservation_id = $1 AND event_type = 'reservation_expired'
            """,
            res.reservation_id,
        )
        assert event is not None
        assert event["cost_microcents_delta"] == -res.estimated_cost_microcents


@pytest.mark.anyio
async def test_duplicate_sweeper_execution_is_idempotent():
    """Duplicate sweeper executions on already-swept records are safe no-ops."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="dup_sweep_1",
        request_id="req_dup_sweep_1",
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    res = await reserve_generation(req)

    # Fast forward lease expiration
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.llm_usage_reservations SET lease_expires_at = now() - interval '2 seconds' WHERE id = $1",
            res.reservation_id,
        )

    # Pass 1: Sweeps 1
    assert await recover_expired_reservations(batch_size=50) == 1

    # Pass 2: Sweeps 0 (already expired)
    assert await recover_expired_reservations(batch_size=50) == 0

    # Status remains expired, counters remain 0
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        c = await conn.fetchrow(
            "SELECT reserved_requests, reserved_cost_microcents FROM public.llm_usage_budget_counters WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2",
            cred_id,
            day_start,
        )
        assert c["reserved_requests"] == 0
        assert c["reserved_cost_microcents"] == 0


@pytest.mark.anyio
async def test_concurrent_sweepers_with_skip_locked():
    """Multiple simultaneous sweepers never double-process rows due to FOR UPDATE SKIP LOCKED."""
    user_id, cred_id = await create_test_contributor()
    res_ids: list[UUID] = []

    # Create 6 expired reservations
    for i in range(6):
        req = AdmissionRequest(
            user_id=user_id,
            credential_id=cred_id,
            provider_id="groq",
            model_id="openai/gpt-oss-120b",
            idempotency_key=f"concurrent_sweep_{i}",
            request_id=f"req_concurrent_sweep_{i}",
            estimated_input_tokens=10,
            max_output_tokens=10,
        )
        res = await reserve_generation(req)
        res_ids.append(res.reservation_id)
        # Release concurrency lease so credential can reserve the next one
        from app.modules.admission.concurrency import release_concurrency_lease

        await release_concurrency_lease(user_id, cred_id, res.lease_token)

    # Expire all 6 in the past
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.llm_usage_reservations SET lease_expires_at = now() - interval '10 seconds' WHERE id = ANY($1)",
            res_ids,
        )

    # Run 4 concurrent sweepers simultaneously
    sweeper_results = await asyncio.gather(
        recover_expired_reservations(batch_size=5),
        recover_expired_reservations(batch_size=5),
        recover_expired_reservations(batch_size=5),
        recover_expired_reservations(batch_size=5),
    )

    # Total swept across all sweepers must be exactly 6!
    assert sum(sweeper_results) == 6

    # Verify all 6 are expired
    async with pool.acquire() as conn:
        expired_count = await conn.fetchval(
            "SELECT count(*) FROM public.llm_usage_reservations WHERE id = ANY($1) AND status = 'expired'",
            res_ids,
        )
        assert expired_count == 6


@pytest.mark.anyio
async def test_partial_redis_cleanup_failure_resilience():
    """If Redis fails during cleanup, PostgreSQL transaction succeeds and recovers state."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="redis_fail_1",
        request_id="req_redis_fail_1",
        estimated_input_tokens=20,
        max_output_tokens=20,
    )
    res = await reserve_generation(req)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.llm_usage_reservations SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
            res.reservation_id,
        )

    # Mock release_concurrency_lease to simulate a Redis failure
    with patch(
        "app.modules.admission.service.release_concurrency_lease",
        side_effect=Exception("Simulated Redis timeout"),
    ):
        swept = await recover_expired_reservations(batch_size=10)
        assert swept == 1

    # Verify PostgreSQL committed regardless of Redis error
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert status == "expired"


@pytest.mark.anyio
async def test_dispatched_and_outcome_unknown_never_expired_by_sweeper():
    """Sweeper strictly filters on status='reserved'; dispatched and outcome_unknown are never touched."""
    user_id, cred_id = await create_test_contributor()

    # Request 1: dispatched
    req_1 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="dispatched_idem",
        request_id="req_dispatched_test",
        estimated_input_tokens=20,
        max_output_tokens=20,
    )
    res_1 = await reserve_generation(req_1)
    await mark_dispatched(res_1.reservation_id)

    from app.modules.admission.concurrency import release_concurrency_lease

    await release_concurrency_lease(user_id, cred_id, res_1.lease_token)

    # Request 2: outcome_unknown
    req_2 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="unknown_idem",
        request_id="req_unknown_test",
        estimated_input_tokens=20,
        max_output_tokens=20,
    )
    res_2 = await reserve_generation(req_2)
    await mark_dispatched(res_2.reservation_id)
    await mark_outcome_unknown(res_2.reservation_id, reason="read_timeout")

    # Set both lease_expires_at into the past
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.llm_usage_reservations SET lease_expires_at = now() - interval '60 seconds' WHERE id = ANY($1)",
            [res_1.reservation_id, res_2.reservation_id],
        )

    # Run sweeper
    swept = await recover_expired_reservations(batch_size=50)
    assert swept == 0

    # Verify statuses are unchanged
    async with pool.acquire() as conn:
        s1 = await conn.fetchval(
            "SELECT status FROM public.llm_usage_reservations WHERE id = $1",
            res_1.reservation_id,
        )
        s2 = await conn.fetchval(
            "SELECT status FROM public.llm_usage_reservations WHERE id = $1",
            res_2.reservation_id,
        )
        assert s1 == "dispatched"
        assert s2 == "outcome_unknown"

        # Verify outcome_unknown retained its conservative budget
        day_start, _ = get_period_boundaries()
        counter = await conn.fetchrow(
            "SELECT reserved_cost_microcents FROM public.llm_usage_budget_counters WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2",
            cred_id,
            day_start,
        )
        assert counter["reserved_cost_microcents"] > 0


# ==============================================================================
# Area 2: Exact Microcent Budget Semantics & 500-Request Regression Test
# ==============================================================================


@pytest.mark.anyio
async def test_500_sequential_subcent_requests_under_1_cent_budget():
    """Verify exact microcent budget semantics: 500 requests @ 75 microcents cost 37,500 microcents.

    A 1-cent spending budget is $0.01 = 1,000,000 microcents.
    500 * 75 = 37,500 microcents (< 1,000,000).
    Proves that individual requests are NOT rounded to 1 cent each, avoiding artificial budget exhaustion.
    """
    # 1 cent daily spending budget, 1000 request limit
    user_id, cred_id = await create_test_contributor(
        daily_req=1000,
        monthly_req=50000,
        daily_spend_cents=1,  # $0.01
        provider_id="groq",
    )

    # 1 token in + 1 token out on openai/gpt-oss-120b
    # Input cost: 15 cents/1M. Output cost: 60 cents/1M.
    # Total cost = 1 * 15 + 1 * 60 = 75 microcents ($0.00000075 USD).
    NUM_REQUESTS = 500
    EXPECTED_TOTAL_MICROCENTS = NUM_REQUESTS * 75  # 37,500 microcents

    redis_conn = get_redis()
    for i in range(NUM_REQUESTS):
        # Reset sliding-window RPM rate keys so the rapid sub-second loop tests the PostgreSQL budget counters
        if redis_conn:
            rate_keys = await redis_conn.keys("ragbait:rate:*")
            if rate_keys:
                await redis_conn.delete(*rate_keys)
        req = AdmissionRequest(
            user_id=user_id,
            credential_id=cred_id,
            provider_id="groq",
            model_id="openai/gpt-oss-120b",
            idempotency_key=f"subcent_500_{i}",
            request_id=f"req_subcent_500_{i}",
            estimated_input_tokens=1,
            max_output_tokens=1,
        )
        res = await reserve_generation(req)
        await mark_dispatched(res.reservation_id)
        await settle_reservation(
            res.reservation_id,
            req=SettlementRequest(
                actual_input_tokens=1,
                actual_output_tokens=1,
                duration_ms=10,
                provider_status_code=200,
            ),
        )

    # 1. Verify exact database counter values
    pool = get_pool()
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        counter = await conn.fetchrow(
            """
            SELECT dispatched_requests, reserved_requests, actual_cost_microcents,
                   reserved_cost_microcents, actual_cost_cents, reserved_cost_cents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert counter["dispatched_requests"] == NUM_REQUESTS
        assert counter["reserved_requests"] == 0
        assert counter["actual_cost_microcents"] == EXPECTED_TOTAL_MICROCENTS
        assert counter["reserved_cost_microcents"] == 0
        # Display cents: 37,500 microcents rounds up to 1 whole cent on integer dashboard
        assert counter["actual_cost_cents"] == 1
        assert counter["reserved_cost_cents"] == 0

    # 2. Verify dashboard serialization: display cents vs exact microcents
    summary = await get_usage_summary_for_user(user_id)
    assert summary.total_dispatched_requests == NUM_REQUESTS
    assert summary.total_settled_requests == NUM_REQUESTS
    assert summary.total_input_tokens == NUM_REQUESTS
    assert summary.total_output_tokens == NUM_REQUESTS
    assert summary.total_actual_cost_microcents == EXPECTED_TOTAL_MICROCENTS
    assert summary.daily_spending_microcents == EXPECTED_TOTAL_MICROCENTS
    assert summary.daily_spending_cents == 1  # 1 cent on whole-cent card
    assert summary.daily_spending_budget_cents == 1

    # 3. Verify independent request-count limit enforcement
    # If daily request limit is reduced to 500, request #501 fails on requests, NOT spending!
    if redis_conn:
        rate_keys = await redis_conn.keys("ragbait:rate:*")
        if rate_keys:
            await redis_conn.delete(*rate_keys)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET daily_request_limit = 500 WHERE id = $1",
            cred_id,
        )

    req_501 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="subcent_501",
        request_id="req_subcent_501",
        estimated_input_tokens=1,
        max_output_tokens=1,
    )
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_generation(req_501)

    assert exc_info.value.status_code == 429
    assert exc_info.value.details.get("limit_type") == "requests"
    assert "Daily request limit exceeded (500/500)" in exc_info.value.message
