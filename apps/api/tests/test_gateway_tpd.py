"""Tests for Tokens Per Day (TPD) quota enforcement and accounting."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.modules.admission.errors import BudgetExhaustedError
from app.modules.admission.ledger import (
    commit_dispatch_transactional,
    get_period_boundaries,
    release_reservation_transactional,
    reserve_budget_transactional,
    settle_usage_transactional,
)
from app.modules.credentials.vault import init_vault_pool


@pytest.fixture(autouse=True)
async def setup_env():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    yield


@pytest.mark.asyncio
async def test_tpd_daily_token_limit_enforcement():
    pool = get_pool()
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)
    day_start, _ = get_period_boundaries(now_utc)

    async with pool.acquire() as conn:
        # Create test user via auth.users so profile is created by trigger
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "TPD Tester"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"tpd_tester_{user_id.hex[:6]}@example.com",
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = 'user', approval_status = 'approved'
            WHERE id = $1
            """,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                daily_request_limit, monthly_request_limit
            )
            VALUES ($1, $2, 'gemini', 'TPD Key', 'masked', $3, 'active', 'valid', 'owner_only', false, 1000, 10000)
            ON CONFLICT (id) DO NOTHING
            """,
            cred_id,
            user_id,
            f"fp_{uuid4().hex[:12]}",
        )

    tpd_limit = 10000  # 10,000 tokens per day

    # Request 1: 4,000 estimated tokens (2,000 in, 2,000 out) -> Admitted
    res1 = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key=f"tpd_req_1_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=2000,
        estimated_output_tokens=2000,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_1",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
        effective_daily_token_limit=tpd_limit,
    )
    assert res1.status == "reserved"

    # Verify counter has reserved_tokens = 4000
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reserved_tokens, actual_tokens
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert row["reserved_tokens"] == 4000
        assert row["actual_tokens"] == 0

    # Request 2: 5,000 estimated tokens -> Admitted (Total reserved = 9,000 <= 10,000)
    res2 = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key=f"tpd_req_2_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=2500,
        estimated_output_tokens=2500,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_2",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
        effective_daily_token_limit=tpd_limit,
    )
    assert res2.status == "reserved"

    # Request 3: 2,000 estimated tokens -> REJECTED (9,000 + 2,000 = 11,000 > 10,000)
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_budget_transactional(
            user_id=user_id,
            credential_id=cred_id,
            provider_id="gemini",
            model_id="gemini-3.8-flash",
            idempotency_key=f"tpd_req_3_{uuid4().hex[:8]}",
            request_id=f"req_{uuid4().hex[:8]}",
            estimated_input_tokens=1000,
            estimated_output_tokens=1000,
            estimated_cost_cents=0,
            pricing_snapshot={},
            lease_token="lease_3",
            lease_expires_at=now_utc,
            daily_request_limit=1000,
            monthly_request_limit=10000,
            daily_spending_limit_cents=None,
            monthly_spending_limit_cents=None,
            effective_daily_token_limit=tpd_limit,
        )
    assert exc_info.value.details["limit_type"] == "tpd"

    # Dispatch and Settle Request 1 with 3,000 actual tokens (1,500 in, 1,500 out)
    # Reconciles: reserved_tokens decreases by 4,000; actual_tokens increases by 3,000.
    # Total daily usage = 3,000 actual + 5,000 reserved = 8,000.
    await commit_dispatch_transactional(res1.reservation_id)
    await settle_usage_transactional(
        reservation_id=res1.reservation_id,
        actual_input_tokens=1500,
        actual_output_tokens=1500,
        actual_cost_cents=0,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reserved_tokens, actual_tokens
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert row["reserved_tokens"] == 5000
        assert row["actual_tokens"] == 3000

    # Request 4: 1,500 tokens now succeeds! (8,000 + 1,500 = 9,500 <= 10,000)
    res4 = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key=f"tpd_req_4_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=750,
        estimated_output_tokens=750,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_4",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
        effective_daily_token_limit=tpd_limit,
    )
    assert res4.status == "reserved"

    # Cancellation refunds reserved tokens
    await release_reservation_transactional(res4.reservation_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT reserved_tokens, actual_tokens
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert row["reserved_tokens"] == 5000
        assert row["actual_tokens"] == 3000
