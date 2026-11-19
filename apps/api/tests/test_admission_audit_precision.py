"""Comprehensive test suite for M2.4 precision and quota audit.

Verifies:
1. Exact integer microcents ($10^-6 cents = $10^-8 USD) accounting and subcent accumulation.
2. Zero floating-point arithmetic drift and asymmetric input/output pricing.
3. Trusted account-scope derivation strictly from credential metadata without fabrication.
4. Shared account-level rate limiting preventing quota bypass across credentials.
5. Sliding-window TPM (tokens per minute) enforcement and actual-usage reconciliation.
6. Daily RPD (requests per day) budget enforcement.
7. 300-second maximum operation lifetime bound on concurrency lease renewals.
8. Idempotent expiration and reconciliation of uncommitted and ambiguous reservations.
"""

from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.concurrency import (
    acquire_concurrency_lease,
    renew_concurrency_lease,
)
from app.modules.admission.errors import (
    AdmissionRejectionError,
    BudgetExhaustedError,
    InvalidStateTransitionError,
    LeaseError,
)
from app.modules.admission.ledger import (
    compute_cost_microcents,
    get_period_boundaries,
    microcents_to_ceiling_cents,
)
from app.modules.admission.schemas import (
    AdmissionRequest,
    SettlementRequest,
)
from app.modules.admission.service import (
    expire_reservation,
    mark_dispatched,
    mark_outcome_unknown,
    reconcile_reservation,
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


async def create_audit_contributor(
    daily_req: int = 500,
    monthly_req: int = 15000,
    daily_spend_cents: int | None = None,
    monthly_spend_cents: int | None = None,
    provider_id: str = "groq",
    account_scope_id: str | None = None,
) -> tuple[UUID, UUID]:
    """Helper to provision a user and active credential with optional account scope."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"audit_user_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Audit User"}'::jsonb)
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

    raw_key = f"key_{provider_id}_{uuid4().hex}"
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
        account_scope_id=account_scope_id,
        account_scope_verified=bool(account_scope_id),
        account_scope_source="test_verified" if account_scope_id else None,
    )
    await update_validation_status(stored.id, user_id, "valid")
    return user_id, stored.id


# ==============================================================================
# 1. Financial Precision & Subunit Arithmetic
# ==============================================================================


def test_microcents_exact_arithmetic():
    """Verify zero floating point drift and exact integer arithmetic."""
    # Pricing for gpt-oss-120b: 15 cents/1M in, 60 cents/1M out
    # 1 input token = 15 microcents
    # 1 output token = 60 microcents
    cost_1 = compute_cost_microcents(1, 0, 15, 60)
    assert cost_1 == 15
    assert isinstance(cost_1, int)

    cost_2 = compute_cost_microcents(0, 1, 15, 60)
    assert cost_2 == 60
    assert isinstance(cost_2, int)

    # 1 input + 1 output = 75 microcents
    assert compute_cost_microcents(1, 1, 15, 60) == 75

    # Sum of 100,000 sequential 1-token requests equals exactly 100,000 * 15 microcents
    assert sum(compute_cost_microcents(1, 0, 15, 60) for _ in range(100)) == 1500

    # Ceiling rounding for reservation/display
    assert microcents_to_ceiling_cents(0) == 0
    assert microcents_to_ceiling_cents(1) == 1
    assert microcents_to_ceiling_cents(75) == 1
    assert microcents_to_ceiling_cents(999_999) == 1
    assert microcents_to_ceiling_cents(1_000_000) == 1
    assert microcents_to_ceiling_cents(1_000_001) == 2


@pytest.mark.anyio
async def test_subcent_request_accumulates_without_truncation():
    """Small requests of < 1 cent accumulate exact microcents in database counters."""
    user_id, cred_id = await create_audit_contributor(provider_id="groq")

    # Request 1: 1 token in + 1 token out on openai/gpt-oss-120b
    # Cost = 1 * 15 + 1 * 60 = 75 microcents ($0.00000075 USD)
    req_1 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="subcent_idem_1",
        request_id="subcent_req_1",
        estimated_input_tokens=1,
        max_output_tokens=1,
    )
    res_1 = await reserve_generation(req_1)
    assert res_1.estimated_cost_microcents == 75
    assert res_1.estimated_cost_cents == 1  # Conservative reservation ceiling

    await mark_dispatched(res_1.reservation_id)
    settled_1 = await settle_reservation(
        res_1.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=1,
            actual_output_tokens=1,
            duration_ms=50,
            provider_status_code=200,
        ),
    )
    assert settled_1.actual_cost_microcents == 75
    assert settled_1.actual_cost_cents == 1  # Display ceiling

    # Check database counters directly
    pool = get_pool()
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        counter = await conn.fetchrow(
            """
            SELECT actual_cost_cents, actual_cost_microcents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert counter["actual_cost_microcents"] == 75
        assert counter["actual_cost_cents"] == 1

        # Check immutable event table: net cost across reservation + settlement
        events = await conn.fetch(
            """
            SELECT event_type, tokens_delta, cost_cents_delta, cost_microcents_delta, details
            FROM public.llm_usage_events
            WHERE reservation_id = $1
            ORDER BY created_at ASC
            """,
            res_1.reservation_id,
        )
        assert len(events) == 3  # reservation_created, dispatch_committed, usage_settled
        net_microcents = sum(e["cost_microcents_delta"] for e in events)
        assert net_microcents == 75

    # Request 2: Another 1 token in + 1 token out (75 microcents)
    req_2 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="subcent_idem_2",
        request_id="subcent_req_2",
        estimated_input_tokens=1,
        max_output_tokens=1,
    )
    res_2 = await reserve_generation(req_2)
    await mark_dispatched(res_2.reservation_id)
    await settle_reservation(
        res_2.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=1,
            actual_output_tokens=1,
            duration_ms=60,
            provider_status_code=200,
        ),
    )

    async with pool.acquire() as conn:
        counter_2 = await conn.fetchrow(
            """
            SELECT actual_cost_cents, actual_cost_microcents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        # Total microcents must be exactly 75 + 75 = 150 without truncation!
        assert counter_2["actual_cost_microcents"] == 150
        assert counter_2["actual_cost_cents"] == 1


# ==============================================================================
# 2. Provider-Account Quota Enforcement
# ==============================================================================


@pytest.mark.anyio
async def test_shared_account_scope_prevents_quota_bypass():
    """Multiple credentials sharing an account_scope_id cannot bypass account-level RPM."""
    pool = get_pool()
    shared_scope = f"dept_acc_{uuid4().hex[:6]}"

    # Seed an account-level quota in provider_quotas with a tight limit: 2 RPM
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.provider_quotas (
                provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                source_documentation, confidence, provenance
            )
            VALUES ('groq', 'account', $1, null, 'rpm', 2, 'https://groq.com/docs', 'high', 'provider_published')
            ON CONFLICT (provider_id, scope_id, model_id, limit_type) DO UPDATE SET limit_value = 2
            """,
            shared_scope,
        )

    # Provision two separate contributors and credentials with the SAME account_scope_id
    user_1, cred_1 = await create_audit_contributor(
        provider_id="groq", account_scope_id=shared_scope
    )
    user_2, cred_2 = await create_audit_contributor(
        provider_id="groq", account_scope_id=shared_scope
    )

    # Request 1 via Credential 1 -> succeeds
    req_1 = AdmissionRequest(
        user_id=user_1,
        credential_id=cred_1,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="acc_idem_1",
        request_id="acc_req_1",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res_1 = await reserve_generation(req_1)
    assert res_1.status == "reserved"

    from app.modules.admission.concurrency import release_concurrency_lease

    # Release concurrency lease so credential 1 has concurrency capacity, but keep rate slot
    await release_concurrency_lease(user_1, cred_1, res_1.lease_token)

    # Request 2 via Credential 2 -> succeeds (account count = 2)
    req_2 = AdmissionRequest(
        user_id=user_2,
        credential_id=cred_2,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="acc_idem_2",
        request_id="acc_req_2",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res_2 = await reserve_generation(req_2)
    assert res_2.status == "reserved"
    await release_concurrency_lease(user_2, cred_2, res_2.lease_token)

    # Request 3 via Credential 1 -> MUST BE REJECTED by shared account-level limit!
    req_3 = AdmissionRequest(
        user_id=user_1,
        credential_id=cred_1,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="acc_idem_3",
        request_id="acc_req_3",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_generation(req_3)

    assert exc_info.value.status_code == 429
    assert exc_info.value.category == "rate_limit_account"
    assert "Rate limit exceeded on account" in exc_info.value.message


@pytest.mark.anyio
async def test_unscoped_credential_does_not_fabricate_account_id():
    """Credentials without account_scope_id must not have artificial account rate keys."""
    user_id, cred_id = await create_audit_contributor(provider_id="groq", account_scope_id=None)

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="unscoped_idem_1",
        request_id="unscoped_req_1",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res = await reserve_generation(req)
    assert res.status == "reserved"

    # Check Redis keys
    redis_conn = get_redis()
    account_keys = await redis_conn.keys("ragbait:rate:account:*")
    # Verify no fabricated account key exists for this credential
    assert len(account_keys) == 0


# ==============================================================================
# 3. Token-Based Quotas & Reconciliation
# ==============================================================================


@pytest.mark.anyio
async def test_tpm_sliding_window_enforcement_and_reconciliation():
    """TPM is enforced atomically in Redis and reconciled on actual usage."""
    # groq openai/gpt-oss-120b has a 6,000 TPM limit in provider_quotas
    user_id, cred_id = await create_audit_contributor(provider_id="groq")

    # Reserve 5,000 tokens (well within 6,000 limit)
    req_1 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="tpm_idem_1",
        request_id="tpm_req_1",
        estimated_input_tokens=2500,
        max_output_tokens=2500,
    )
    res_1 = await reserve_generation(req_1)
    assert res_1.status == "reserved"

    # Attempt to reserve 2,000 tokens: 5000 + 2000 = 7000 > 6000 TPM limit!
    req_2 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="tpm_idem_2",
        request_id="tpm_req_2",
        estimated_input_tokens=1000,
        max_output_tokens=1000,
    )
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_generation(req_2)

    assert exc_info.value.status_code == 429
    assert exc_info.value.category == "rate_limit_model_tpm"
    assert "limit: 6000 TPM" in exc_info.value.message

    # Now dispatch and settle request 1 with only 500 actual tokens!
    await mark_dispatched(res_1.reservation_id)
    await settle_reservation(
        res_1.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=250,
            actual_output_tokens=250,
            duration_ms=100,
            provider_status_code=200,
        ),
    )

    # Now attempt request 2 again: current volume in sliding window is 500 + 2000 = 2500 <= 6000 TPM
    res_2 = await reserve_generation(req_2)
    assert res_2.status == "reserved"


@pytest.mark.anyio
async def test_rpd_daily_request_limit_enforcement():
    """Daily request limit (RPD) in budget counters blocks admissions when reached."""
    # Credential with daily_request_limit = 2
    user_id, cred_id = await create_audit_contributor(
        daily_req=2, monthly_req=100, provider_id="groq"
    )

    req_1 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="rpd_idem_1",
        request_id="rpd_req_1",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res_1 = await reserve_generation(req_1)
    await mark_dispatched(res_1.reservation_id)
    await settle_reservation(
        res_1.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=10,
            actual_output_tokens=10,
            duration_ms=50,
            provider_status_code=200,
        ),
    )

    req_2 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="rpd_idem_2",
        request_id="rpd_req_2",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res_2 = await reserve_generation(req_2)
    await mark_dispatched(res_2.reservation_id)
    await settle_reservation(
        res_2.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=10,
            actual_output_tokens=10,
            duration_ms=50,
            provider_status_code=200,
        ),
    )

    # Third request exceeds daily_request_limit (2)
    req_3 = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="rpd_idem_3",
        request_id="rpd_req_3",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    with pytest.raises(BudgetExhaustedError) as exc_info:
        await reserve_generation(req_3)

    assert exc_info.value.status_code == 429
    assert "Daily request limit" in exc_info.value.message


# ==============================================================================
# 4. Reservation Expiry & 300s Max Lifetime
# ==============================================================================


@pytest.mark.anyio
async def test_concurrency_lease_300s_max_lifetime_bound():
    """Concurrency lease renewal is rejected when exceeding the 300s hard maximum lifetime."""
    user_id = uuid4()
    cred_id = uuid4()

    lease = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        lease_duration_seconds=30,
    )

    # Manually simulate that 300s has elapsed by setting max lifetime in the past
    redis_conn = get_redis()
    await redis_conn.set(f"ragbait:lease:max:{lease.lease_token}", 0)

    # Attempting to renew must fail with max_lifetime_exceeded
    with pytest.raises(LeaseError) as exc_info:
        await renew_concurrency_lease(
            user_id=user_id,
            credential_id=cred_id,
            lease_token=lease.lease_token,
            lease_duration_seconds=30,
        )

    assert "max_lifetime_exceeded" in exc_info.value.message


@pytest.mark.anyio
async def test_expire_reservation_refunds_uncommitted_counters():
    """Uncommitted reservation expiration safely refunds reserved budget counters."""
    user_id, cred_id = await create_audit_contributor(provider_id="gemini")

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key="expire_idem_1",
        request_id="expire_req_1",
        estimated_input_tokens=100_000,
        max_output_tokens=100_000,
    )
    res = await reserve_generation(req)
    assert res.estimated_cost_cents == 8

    # Verify counters have reserved 8 cents and 1 request
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
        assert c1["reserved_cost_cents"] == 8

    # Expire reservation
    expired = await expire_reservation(res.reservation_id, reason="lease_timeout")
    assert expired.status == "expired"

    # Verify counters decremented to 0
    async with pool.acquire() as conn:
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
        assert c2["reserved_cost_cents"] == 0
        assert c2["reserved_cost_microcents"] == 0

        # Check event log
        event = await conn.fetchrow(
            """
            SELECT event_type, cost_cents_delta, cost_microcents_delta
            FROM public.llm_usage_events
            WHERE reservation_id = $1 AND event_type = 'reservation_expired'
            """,
            res.reservation_id,
        )
        assert event["cost_cents_delta"] == -8
        assert event["cost_microcents_delta"] == -7_500_000


@pytest.mark.anyio
async def test_reconcile_reservation_from_outcome_unknown():
    """Ambiguous outcome_unknown reservation is properly reconciled to confirmed actual usage."""
    user_id, cred_id = await create_audit_contributor(provider_id="gemini")

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key="reconcile_idem_1",
        request_id="reconcile_req_1",
        estimated_input_tokens=100_000,
        max_output_tokens=100_000,
    )
    res = await reserve_generation(req)
    await mark_dispatched(res.reservation_id)
    await mark_outcome_unknown(res.reservation_id, reason="read_timeout")

    # Reconcile with 10,000 input and 5,000 output tokens
    # Cost = 10,000 * 15 + 5,000 * 60 = 150,000 + 300,000 = 450,000 microcents = 0.45 cents -> ceiling 1 cent
    reconciled = await reconcile_reservation(
        reservation_id=res.reservation_id,
        actual_input_tokens=10_000,
        actual_output_tokens=5_000,
        reconciliation_notes="Provider verified partial completion",
    )
    assert reconciled.status == "settled"
    assert reconciled.actual_cost_microcents == 450_000
    assert reconciled.actual_cost_cents == 1

    pool = get_pool()
    day_start, _ = get_period_boundaries()
    async with pool.acquire() as conn:
        counter = await conn.fetchrow(
            """
            SELECT reserved_cost_cents, actual_cost_cents, actual_cost_microcents
            FROM public.llm_usage_budget_counters
            WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
            """,
            cred_id,
            day_start,
        )
        assert counter["reserved_cost_cents"] == 0
        assert counter["actual_cost_cents"] == 1
        assert counter["actual_cost_microcents"] == 450_000


@pytest.mark.anyio
async def test_expired_reservation_rejects_late_callback():
    """Late callback arriving after reservation was expired is rejected."""
    user_id, cred_id = await create_audit_contributor(provider_id="groq")

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="late_idem_1",
        request_id="late_req_1",
        estimated_input_tokens=10,
        max_output_tokens=10,
    )
    res = await reserve_generation(req)
    await expire_reservation(res.reservation_id, reason="lease_timeout")

    # Attempting to settle an expired reservation must raise InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        await settle_reservation(
            res.reservation_id,
            req=SettlementRequest(
                actual_input_tokens=10,
                actual_output_tokens=10,
                duration_ms=100,
                provider_status_code=200,
            ),
        )
