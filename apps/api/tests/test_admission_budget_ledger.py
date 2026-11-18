"""Comprehensive PostgreSQL usage ledger, budget locking, and immutable event tests (M2.4)."""

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.errors import BudgetExhaustedError
from app.modules.admission.ledger import (
    commit_dispatch_transactional,
    get_period_boundaries,
    reserve_budget_transactional,
    settle_usage_transactional,
)
from app.modules.admission.schemas import AdmissionRequest
from app.modules.admission.service import reserve_generation
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


async def create_test_contributor_with_limits(
    daily_req: int = 500,
    monthly_req: int = 15000,
    daily_spend_cents: int | None = None,
    monthly_spend_cents: int | None = None,
) -> tuple[UUID, UUID]:
    """Helper to provision a user and an active credential with specific budgets."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"budget_user_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Budget User"}'::jsonb)
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

    raw_key = f"gsk_budget_test_{uuid4().hex}"
    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Budget Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=daily_req,
        monthly_request_limit=monthly_req,
        daily_spending_limit_cents=daily_spend_cents,
        monthly_spending_limit_cents=monthly_spend_cents,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


@pytest.mark.anyio
async def test_daily_request_limit_exhaustion():
    """When daily request capacity is full, further reservations are rejected with BudgetExhaustedError."""
    user_id, cred_id = await create_test_contributor_with_limits(daily_req=3, monthly_req=100)

    # 3 reservations succeed
    for i in range(3):
        req = AdmissionRequest(
            user_id=user_id,
            credential_id=cred_id,
            provider_id="groq",
            model_id="openai/gpt-oss-120b",
            idempotency_key=f"idemp_daily_req_{i}",
            request_id=f"req_daily_{i}",
            estimated_input_tokens=100,
            max_output_tokens=50,
        )
        res = await reserve_generation(req)
        # Release concurrency so next test iteration isn't blocked by concurrency
        from app.modules.admission.concurrency import release_concurrency_lease

        await release_concurrency_lease(user_id, cred_id, res.lease_token)

    # 4th reservation exceeds daily limit (3)
    req_4 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_daily_req_4",
        request_id="req_daily_4",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_generation(req_4)

    assert exc_info.value.status_code == 429
    assert "Daily request limit exceeded" in exc_info.value.message


@pytest.mark.anyio
async def test_daily_spending_limit_exhaustion():
    """When daily spending cap is reached, new admissions are rejected."""
    # 50 cents daily cap
    user_id, cred_id = await create_test_contributor_with_limits(
        daily_req=100,
        monthly_req=1000,
        daily_spend_cents=50,  # $0.50
    )

    # gpt-oss-120b costs 15 cents in / 60 cents out per million
    # 500k input + 500k output = (500k * 15 + 500k * 60) / 1M = 7.5 + 30 = 37.5 -> 38 cents
    req_1 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_spend_1",
        request_id="req_spend_1",
        estimated_input_tokens=500_000,
        max_output_tokens=500_000,
    )
    res_1 = await reserve_generation(req_1)
    assert res_1.estimated_cost_cents == 38

    from app.modules.admission.concurrency import release_concurrency_lease

    await release_concurrency_lease(user_id, cred_id, res_1.lease_token)

    # Second request requires 38 cents -> 38 + 38 = 76 cents > 50 cents cap!
    req_2 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_spend_2",
        request_id="req_spend_2",
        estimated_input_tokens=500_000,
        max_output_tokens=500_000,
    )
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_generation(req_2)

    assert exc_info.value.status_code == 429
    assert "Daily spending cap exceeded" in exc_info.value.message


@pytest.mark.anyio
async def test_concurrent_reservations_cannot_exceed_budget():
    """Simultaneous concurrent reservations use row locking to prevent budget overrun."""
    user_id, cred_id = await create_test_contributor_with_limits(
        daily_req=100,
        monthly_req=1000,
        daily_spend_cents=100,  # $1.00
    )

    day_start, month_start = get_period_boundaries()

    # Launch 5 concurrent database budget reservation attempts each requesting 30 cents (total 150 cents)
    async def try_reserve(idx: int):
        try:
            await reserve_budget_transactional(
                user_id=user_id,
                credential_id=cred_id,
                provider_id="groq",
                model_id="openai/gpt-oss-120b",
                idempotency_key=f"concurrent_res_{idx}_{uuid4().hex[:6]}",
                request_id=f"req_concurrent_{idx}",
                estimated_input_tokens=100,
                estimated_output_tokens=100,
                estimated_cost_cents=30,  # 30 cents each
                pricing_snapshot={},
                lease_token=f"lease_{idx}",
                lease_expires_at=pytest.importorskip("datetime").datetime.now(
                    pytest.importorskip("datetime").UTC
                ),
                daily_request_limit=100,
                monthly_request_limit=1000,
                daily_spending_limit_cents=100,
                monthly_spending_limit_cents=1000,
            )
            return True
        except BudgetExhaustedError:
            return False

    results = await asyncio.gather(*[try_reserve(i) for i in range(5)])

    # Exactly 3 should succeed (3 * 30 = 90 <= 100), and 2 must fail!
    assert results.count(True) == 3
    assert results.count(False) == 2


@pytest.mark.anyio
async def test_settlement_reconciles_estimated_vs_actual_cost():
    """Settling actual usage reconciles reserved cost aggregate and updates confirmed spend."""
    user_id, cred_id = await create_test_contributor_with_limits(
        daily_req=100,
        monthly_req=1000,
        daily_spend_cents=200,
    )

    # Reserve 50 cents estimated
    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_reconcile_1",
        request_id="req_reconcile_1",
        estimated_input_tokens=100_000,
        max_output_tokens=100_000,
    )
    reservation = await reserve_generation(req)
    await commit_dispatch_transactional(reservation.reservation_id)

    # Settle with actual usage of only 15 cents
    settled = await settle_usage_transactional(
        reservation_id=reservation.reservation_id,
        actual_input_tokens=50_000,
        actual_output_tokens=10_000,
        actual_cost_cents=15,
    )
    assert settled.actual_cost_cents == 15

    # Verify database budget counters: reserved_cost_cents should be 0, actual_cost_cents should be 15
    pool = get_pool()
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        counter = await conn.fetchrow(
            """
            SELECT reserved_cost_cents, actual_cost_cents, dispatched_requests
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert counter["reserved_cost_cents"] == 0
        assert counter["actual_cost_cents"] == 15
        assert counter["dispatched_requests"] == 1


@pytest.mark.anyio
async def test_immutable_usage_events_trigger():
    """Direct database updates or deletions on public.llm_usage_events are blocked by trigger."""
    user_id, cred_id = await create_test_contributor_with_limits()
    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_immutable_1",
        request_id="req_immutable_1",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    reservation = await reserve_generation(req)

    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch event id
        event_id = await conn.fetchval(
            "SELECT id FROM public.llm_usage_events WHERE reservation_id = $1",
            reservation.reservation_id,
        )
        assert event_id is not None

        # Attempt UPDATE -> trigger raises 42501
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "UPDATE public.llm_usage_events SET tokens_delta = 9999 WHERE id = $1",
                event_id,
            )

        # Attempt DELETE -> trigger raises 42501
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "DELETE FROM public.llm_usage_events WHERE id = $1",
                event_id,
            )
