"""Tests for Tokens Per Day (TPD) quota enforcement, scopes, and accounting."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, init_redis
from app.modules.admission.errors import BudgetExhaustedError
from app.modules.admission.ledger import (
    commit_dispatch_transactional,
    get_period_boundaries,
    release_reservation_transactional,
    reserve_budget_transactional,
    settle_usage_transactional,
)
from app.modules.credentials.vault import close_vault_pool, init_vault_pool


@pytest.fixture(autouse=True)
async def setup_env():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    yield
    await close_vault_pool()
    await close_db_pool()
    await close_redis()


@pytest.mark.asyncio
async def test_tpd_daily_token_limit_enforcement():
    pool = get_pool()
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)
    day_start, _ = get_period_boundaries(now_utc)

    async with pool.acquire() as conn:
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
            "UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = $1",
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

    # Dispatch and Settle Request 1 with 3,000 actual tokens
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


@pytest.mark.asyncio
async def test_account_level_shared_tpd_enforcement():
    """Verify that multiple credentials sharing the same account_scope_id enforce shared TPD."""
    pool = get_pool()
    user_id = uuid4()
    cred_a = uuid4()
    cred_b = uuid4()
    shared_account = f"org_test_{uuid4().hex[:8]}"
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Account Tester"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"account_tester_{user_id.hex[:6]}@example.com",
        )
        await conn.execute(
            "UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = $1",
            user_id,
        )
        for c_id, fp in [(cred_a, "fp_a"), (cred_b, "fp_b")]:
            await conn.execute(
                """
                INSERT INTO public.api_key_metadata (
                    id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                    status, validation_status, sharing_scope, shared_consent,
                    account_scope_id, account_scope_verified,
                    daily_request_limit, monthly_request_limit
                )
                VALUES ($1, $2, 'groq', 'Shared Account Key', 'masked', $3, 'active', 'valid', 'owner_only', false, $4, true, 1000, 10000)
                ON CONFLICT (id) DO NOTHING
                """,
                c_id,
                user_id,
                f"{fp}_{uuid4().hex[:8]}",
                shared_account,
            )

    account_tpd_limit = 5000

    # Key A reserves 3,000 tokens on shared account
    res_a = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_a,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"acc_req_a_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=1500,
        estimated_output_tokens=1500,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_a",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
        account_scope_id=shared_account,
        effective_daily_token_limit=account_tpd_limit,
    )
    assert res_a.status == "reserved"

    # Key B attempts to reserve 3,000 tokens on the same shared account:
    # 3,000 (key A) + 3,000 (key B) = 6,000 > 5,000 -> REJECTED!
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_budget_transactional(
            user_id=user_id,
            credential_id=cred_b,
            provider_id="groq",
            model_id="openai/gpt-oss-120b",
            idempotency_key=f"acc_req_b_{uuid4().hex[:8]}",
            request_id=f"req_{uuid4().hex[:8]}",
            estimated_input_tokens=1500,
            estimated_output_tokens=1500,
            estimated_cost_cents=0,
            pricing_snapshot={},
            lease_token="lease_b",
            lease_expires_at=now_utc,
            daily_request_limit=1000,
            monthly_request_limit=10000,
            daily_spending_limit_cents=None,
            monthly_spending_limit_cents=None,
            account_scope_id=shared_account,
            effective_daily_token_limit=account_tpd_limit,
        )
    assert exc_info.value.details["limit_type"] == "tpd"


@pytest.mark.asyncio
async def test_concurrent_admissions_cannot_exceed_tpd_cap():
    """Verify that parallel concurrent requests near the TPD cap serialize and do not oversell."""
    pool = get_pool()
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Race Tester"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"race_tester_{user_id.hex[:6]}@example.com",
        )
        await conn.execute(
            "UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = $1",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                daily_request_limit, monthly_request_limit
            )
            VALUES ($1, $2, 'gemini', 'Race Key', 'masked', $3, 'active', 'valid', 'owner_only', false, 1000, 10000)
            ON CONFLICT (id) DO NOTHING
            """,
            cred_id,
            user_id,
            f"fp_{uuid4().hex[:12]}",
        )

    tpd_limit = 5000

    async def attempt_reservation(idx: int):
        try:
            return await reserve_budget_transactional(
                user_id=user_id,
                credential_id=cred_id,
                provider_id="gemini",
                model_id="gemini-3.8-flash",
                idempotency_key=f"race_{idx}_{uuid4().hex[:6]}",
                request_id=f"req_{idx}",
                estimated_input_tokens=1000,
                estimated_output_tokens=1000,  # 2000 total per request
                estimated_cost_cents=0,
                pricing_snapshot={},
                lease_token=f"lease_{idx}",
                lease_expires_at=now_utc,
                daily_request_limit=1000,
                monthly_request_limit=10000,
                daily_spending_limit_cents=None,
                monthly_spending_limit_cents=None,
                effective_daily_token_limit=tpd_limit,
            )
        except BudgetExhaustedError:
            return None

    # Launch 5 concurrent tasks requesting 2,000 tokens each (total = 10,000 against 5,000 limit)
    results = await asyncio.gather(*[attempt_reservation(i) for i in range(5)])
    successful = [r for r in results if r is not None]
    rejected = [r for r in results if r is None]

    # Exactly 2 requests of 2,000 can fit within 5,000 (2 * 2,000 = 4,000 <= 5,000; third would be 6,000)
    assert len(successful) == 2
    assert len(rejected) == 3


@pytest.mark.asyncio
async def test_unknown_quota_metadata_allows_unhindered_admission():
    """Verify that when no TPD quota exists, admission succeeds without phantom limits."""
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)
    pool = get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "No Quota Tester"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"noquota_{user_id.hex[:6]}@example.com",
        )
        await conn.execute(
            "UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = $1",
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                daily_request_limit, monthly_request_limit
            )
            VALUES ($1, $2, 'gemini', 'No Quota Key', 'masked', $3, 'active', 'valid', 'owner_only', false, 1000, 10000)
            ON CONFLICT (id) DO NOTHING
            """,
            cred_id,
            user_id,
            f"fp_{uuid4().hex[:12]}",
        )

    res = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key=f"no_quota_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=50000,
        estimated_output_tokens=50000,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_no_q",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
        effective_daily_token_limit=None,  # No TPD quota
    )
    assert res.status == "reserved"
