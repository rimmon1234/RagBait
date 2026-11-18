"""Comprehensive provider cooldown and Retry-After parser tests (M2.4)."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.cooldown import (
    check_cooldown,
    parse_retry_after,
    set_cooldown,
)
from app.modules.admission.errors import AdmissionRejectionError
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
        keys = await redis_conn.keys("ragbait:cooldown:*")
        keys.extend(await redis_conn.keys("ragbait:rate:*"))
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


def test_parse_retry_after_delta_seconds():
    """Valid integer delta-seconds parsed directly."""
    assert parse_retry_after("15") == 15
    assert parse_retry_after("  120  ") == 120


def test_parse_retry_after_http_date():
    """Valid RFC 7231 HTTP-date string correctly parsed into remaining seconds."""
    future_time = datetime.now(UTC) + timedelta(seconds=45)
    header_val = format_datetime(future_time, usegmt=True)
    parsed = parse_retry_after(header_val)
    # Allows a few seconds tolerance for test execution
    assert 40 <= parsed <= 46


def test_parse_retry_after_clamping_and_fallback():
    """Unreasonable or invalid values are safely clamped."""
    # Negative / 0 clamped to 1
    assert parse_retry_after("-10") == 1
    assert parse_retry_after("0") == 1
    # Excessive clamped to 3600 (1 hour)
    assert parse_retry_after("999999") == 3600
    # Malformed / empty falls back to default 60
    assert parse_retry_after("not-a-date") == 60
    assert parse_retry_after(None) == 60
    assert parse_retry_after("") == 60


@pytest.mark.anyio
async def test_active_provider_cooldown_blocks_admission():
    """An active cooldown on a provider blocks subsequent admission attempts with 429."""
    pool = get_pool()
    user_id = uuid4()
    unique_email = f"cooldown_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Cooldown Contributor"}'::jsonb)
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

    raw_key = f"gsk_cd_{uuid4().hex}"
    stored = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Cooldown Key",
        raw_key=raw_key,
        status="active",
        daily_request_limit=1000,
        monthly_request_limit=30000,
    )
    await update_validation_status(stored.id, user_id, "valid")

    # Set active cooldown on provider 'groq' for 10 seconds
    await set_cooldown(
        scope_type="provider",
        scope_id="groq",
        retry_after_seconds=10,
        reason="upstream_rate_limited_429",
    )

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=stored.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key="idemp_cd_1",
        request_id="req_cd_1",
        estimated_input_tokens=100,
        max_output_tokens=50,
    )

    # Attempting admission must fail with AdmissionRejectionError
    with pytest.raises(AdmissionRejectionError) as exc_info:
        await reserve_generation(req)

    assert exc_info.value.status_code == 429
    assert exc_info.value.category == "provider_cooldown"
    assert exc_info.value.retry_after >= 1


@pytest.mark.anyio
async def test_cooldown_isolation_across_providers():
    """A cooldown on groq does not affect cerebras or other providers."""
    await set_cooldown(
        scope_type="provider",
        scope_id="groq",
        retry_after_seconds=30,
    )

    # Check groq -> cooling down
    cooling, ttl, scope = await check_cooldown([("provider", "groq")])
    assert cooling is True
    assert ttl > 0

    # Check cerebras -> completely clean
    cooling_c, ttl_c, scope_c = await check_cooldown([("provider", "cerebras")])
    assert cooling_c is False
    assert ttl_c == 0
