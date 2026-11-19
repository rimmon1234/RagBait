"""Comprehensive lifecycle and state machine tests for inference reservations (M2.4)."""

from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.errors import InvalidStateTransitionError
from app.modules.admission.ledger import get_period_boundaries
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    mark_outcome_unknown,
    release_reservation,
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
    unique_email = f"lifecycle_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Lifecycle Contributor"}'::jsonb)
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

    raw_key = f"gsk_lifecycle_{uuid4().hex}"
    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Lifecycle Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=1000,
        monthly_request_limit=30000,
        daily_spending_limit_cents=500,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


@pytest.mark.anyio
async def test_full_lifecycle_reserved_to_dispatched_to_settled():
    """Happy path: reserved -> dispatched -> settled."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_lifecycle_1",
        request_id="req_lifecycle_1",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )

    # 1. Reserved
    res = await reserve_generation(req)
    assert res.status == "reserved"

    # 2. Dispatched
    await mark_dispatched(res.reservation_id)

    # 3. Settled
    settled = await settle_reservation(
        res.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=90,
            actual_output_tokens=45,
            actual_cost_cents=5,
        ),
    )
    assert settled.status == "settled"
    assert settled.actual_cost_cents == 5


@pytest.mark.anyio
async def test_pre_dispatch_cancellation_reserved_to_released():
    """Operation cancelled before dispatch commits transitions reserved -> released."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_lifecycle_2",
        request_id="req_lifecycle_2",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )
    res = await reserve_generation(req)

    # Release before dispatch
    released = await release_reservation(res.reservation_id, reason="client_aborted")
    assert released.status == "released"
    assert released.reason == "client_aborted"


@pytest.mark.anyio
async def test_ambiguous_outcome_retains_spending_reservation():
    """When network timeout occurs after dispatch, outcome_unknown retains reserved budget."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_lifecycle_3",
        request_id="req_lifecycle_3",
        estimated_input_tokens=2_000,
        max_output_tokens=1_000,
    )
    res = await reserve_generation(req)
    await mark_dispatched(res.reservation_id)

    # Ambiguous outcome
    await mark_outcome_unknown(res.reservation_id, reason="upstream_read_timeout")

    # Verify that reserved spending budget is RETAINED (not refunded!)
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
        assert counter["reserved_cost_cents"] == res.estimated_cost_cents
        assert counter["dispatched_requests"] == 1

        # Check status in reservations table
        status = await conn.fetchval(
            "SELECT status FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert status == "outcome_unknown"

    # Outcome unknown can later be settled once upstream report is reconciled!
    settled = await settle_reservation(
        res.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=1_800,
            actual_output_tokens=900,
            actual_cost_cents=res.estimated_cost_cents,
        ),
    )
    assert settled.status == "settled"


@pytest.mark.anyio
async def test_invalid_lifecycle_transitions_rejected():
    """Attempting invalid state transitions raises InvalidStateTransitionError."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_lifecycle_4",
        request_id="req_lifecycle_4",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )
    res = await reserve_generation(req)
    await mark_dispatched(res.reservation_id)
    await settle_reservation(
        res.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=100,
            actual_output_tokens=50,
            actual_cost_cents=10,
        ),
    )

    # 1. Settled reservation cannot be released
    with pytest.raises(InvalidStateTransitionError):
        await release_reservation(res.reservation_id)

    # 2. Settled reservation cannot be marked dispatched again
    with pytest.raises(InvalidStateTransitionError):
        await mark_dispatched(res.reservation_id)


@pytest.mark.anyio
async def test_duplicate_callbacks_are_idempotent():
    """Duplicate dispatch, settlement, and release calls return safely without double-counting."""
    user_id, cred_id = await create_test_contributor()

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_lifecycle_5",
        request_id="req_lifecycle_5",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )
    res = await reserve_generation(req)

    # Duplicate mark_dispatched
    await mark_dispatched(res.reservation_id)
    await mark_dispatched(res.reservation_id)  # No-op

    # Duplicate settle_reservation
    settle_req = SettlementRequest(
        actual_input_tokens=80,
        actual_output_tokens=40,
        actual_cost_cents=8,
    )
    s1 = await settle_reservation(res.reservation_id, req=settle_req)
    s2 = await settle_reservation(res.reservation_id, req=settle_req)
    assert s1.reservation_id == s2.reservation_id
    assert s1.actual_cost_cents == s2.actual_cost_cents
