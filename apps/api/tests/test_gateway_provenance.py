"""Tests for LLM Gateway usage provenance lifecycle."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, init_redis
from app.modules.admission.ledger import (
    commit_dispatch_transactional,
    mark_outcome_unknown_transactional,
    reconcile_usage_transactional,
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
async def test_usage_source_lifecycle_pending_to_authoritative():
    """Verify that a reservation is created as 'pending' and only becomes 'authoritative' upon settlement."""
    pool = get_pool()
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Prov Tester"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"prov_tester_{user_id.hex[:6]}@example.com",
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
            VALUES ($1, $2, 'gemini', 'Prov Key', 'masked', $3, 'active', 'valid', 'owner_only', false, 1000, 10000)
            ON CONFLICT (id) DO NOTHING
            """,
            cred_id,
            user_id,
            f"fp_{uuid4().hex[:12]}",
        )

    # 1. Create reservation
    res = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        idempotency_key=f"prov_test_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=100,
        estimated_output_tokens=200,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_prov_1",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
    )

    # Inspect in database: initial status must be pending (NOT authoritative)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, usage_source, actual_input_tokens, actual_output_tokens FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert row["status"] == "reserved"
        assert row["usage_source"] == "pending"
        assert row["actual_input_tokens"] is None
        assert row["actual_output_tokens"] is None

    # 2. Commit dispatch
    await commit_dispatch_transactional(res.reservation_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, usage_source FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert row["status"] == "dispatched"
        assert row["usage_source"] == "pending"

    # 3. Settle with authoritative usage
    await settle_usage_transactional(
        reservation_id=res.reservation_id,
        actual_input_tokens=95,
        actual_output_tokens=180,
        actual_cost_cents=0,
        actual_cost_microcents=5000,
        resolved_model_id="gemini-3.8-flash",
        upstream_provider="gemini",
        upstream_generation_id="gen-gemini-123",
        usage_source="authoritative",
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, usage_source, actual_input_tokens, actual_output_tokens,
                   resolved_model_id, upstream_provider, upstream_generation_id
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            res.reservation_id,
        )
        assert row["status"] == "settled"
        assert row["usage_source"] == "authoritative"
        assert row["actual_input_tokens"] == 95
        assert row["actual_output_tokens"] == 180
        assert row["resolved_model_id"] == "gemini-3.8-flash"
        assert row["upstream_provider"] == "gemini"
        assert row["upstream_generation_id"] == "gen-gemini-123"


@pytest.mark.asyncio
async def test_outcome_unknown_and_reconciliation_provenance():
    """Verify that failed/ambiguous streams become 'unknown' and reconcile to 'authoritative'."""
    pool = get_pool()
    user_id = uuid4()
    cred_id = uuid4()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Prov Tester 2"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"prov_tester2_{user_id.hex[:6]}@example.com",
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
            VALUES ($1, $2, 'groq', 'Groq Key', 'masked', $3, 'active', 'valid', 'owner_only', false, 1000, 10000)
            ON CONFLICT (id) DO NOTHING
            """,
            cred_id,
            user_id,
            f"fp_{uuid4().hex[:12]}",
        )

    res = await reserve_budget_transactional(
        user_id=user_id,
        credential_id=cred_id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"prov_test2_{uuid4().hex[:8]}",
        request_id=f"req_{uuid4().hex[:8]}",
        estimated_input_tokens=150,
        estimated_output_tokens=300,
        estimated_cost_cents=0,
        pricing_snapshot={},
        lease_token="lease_prov_2",
        lease_expires_at=now_utc,
        daily_request_limit=1000,
        monthly_request_limit=10000,
        daily_spending_limit_cents=None,
        monthly_spending_limit_cents=None,
    )

    await commit_dispatch_transactional(res.reservation_id)

    # Upstream timeout: mark outcome unknown
    await mark_outcome_unknown_transactional(res.reservation_id, reason="upstream_timeout")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, usage_source, actual_input_tokens, actual_output_tokens FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert row["status"] == "outcome_unknown"
        assert row["usage_source"] == "unknown"
        # Missing usage does not become zero
        assert row["actual_input_tokens"] is None
        assert row["actual_output_tokens"] is None

    # Later: sweeper reconciles with confirmed usage from audit logs
    await reconcile_usage_transactional(
        reservation_id=res.reservation_id,
        actual_input_tokens=140,
        actual_output_tokens=280,
        actual_cost_microcents=12000,
        reconciliation_notes="Recovered from provider audit trail",
        usage_source="authoritative",
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, usage_source, actual_input_tokens, actual_output_tokens FROM public.llm_usage_reservations WHERE id = $1",
            res.reservation_id,
        )
        assert row["status"] == "settled"
        assert row["usage_source"] == "authoritative"
        assert row["actual_input_tokens"] == 140
        assert row["actual_output_tokens"] == 280
