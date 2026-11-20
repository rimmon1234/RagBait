"""Comprehensive distributed concurrency lease and heartbeat tests (M2.4)."""

import asyncio
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.errors import AppError
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.admission.concurrency import (
    acquire_concurrency_lease,
    release_concurrency_lease,
    renew_concurrency_lease,
)
from app.modules.admission.errors import ConcurrencyLimitError, LeaseError
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
        keys = await redis_conn.keys("ragbait:concurrency:*")
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


@pytest.mark.anyio
async def test_credential_concurrency_cap_one():
    """A single credential allows at most 1 active concurrent operation."""
    user_id = uuid4()
    cred_id = uuid4()

    # 1. Acquire first lease -> succeeds
    lease_1 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        max_user_concurrency=2,
        max_cred_concurrency=1,
    )
    assert lease_1.lease_token is not None

    # 2. Attempt second lease with same credential -> rejected
    with pytest.raises(ConcurrencyLimitError) as exc_info:
        await acquire_concurrency_lease(
            user_id=user_id,
            credential_id=cred_id,
            max_user_concurrency=2,
            max_cred_concurrency=1,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.scope == "credential"
    assert exc_info.value.current_limit == 1

    # 3. Release first lease -> now second lease can be acquired!
    await release_concurrency_lease(user_id, cred_id, lease_1.lease_token)

    lease_2 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        max_user_concurrency=2,
        max_cred_concurrency=1,
    )
    assert lease_2.lease_token != lease_1.lease_token


@pytest.mark.anyio
async def test_user_concurrency_cap_two():
    """A user is capped at 2 simultaneous operations across distinct credentials."""
    user_id = uuid4()
    cred_a = uuid4()
    cred_b = uuid4()
    cred_c = uuid4()

    # Lease 1 on cred A
    lease_1 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_a,
        max_user_concurrency=2,
        max_cred_concurrency=1,
    )

    # Lease 2 on cred B
    _lease_2 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_b,
        max_user_concurrency=2,
        max_cred_concurrency=1,
    )

    # Lease 3 on cred C -> rejected on USER scope (2 max reached)
    with pytest.raises(ConcurrencyLimitError) as exc_info:
        await acquire_concurrency_lease(
            user_id=user_id,
            credential_id=cred_c,
            max_user_concurrency=2,
            max_cred_concurrency=1,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.scope == "user"
    assert exc_info.value.current_limit == 2

    # Release lease 1 -> now cred C succeeds
    await release_concurrency_lease(user_id, cred_a, lease_1.lease_token)
    lease_3 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_c,
        max_user_concurrency=2,
        max_cred_concurrency=1,
    )
    assert lease_3.lease_token is not None


@pytest.mark.anyio
async def test_lease_renewal_extends_expiration():
    """Heartbeat renewal successfully extends lease expiration time."""
    user_id = uuid4()
    cred_id = uuid4()

    lease = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        lease_duration_seconds=5,
    )
    initial_exp = lease.expires_at

    # Renew lease with 15s duration
    new_exp = await renew_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        lease_token=lease.lease_token,
        lease_duration_seconds=15,
    )
    assert new_exp > initial_exp


@pytest.mark.anyio
async def test_stale_lease_cannot_renew_or_release_newer_lease():
    """A stale or invalid lease token cannot renew or affect an existing lease."""
    user_id = uuid4()
    cred_id = uuid4()

    real_lease = await acquire_concurrency_lease(user_id=user_id, credential_id=cred_id)

    # Fake/stale token cannot renew
    with pytest.raises(LeaseError):
        await renew_concurrency_lease(user_id, cred_id, "lease_fake_stale_token_123")

    # Fake/stale release does not release real lease
    await release_concurrency_lease(user_id, cred_id, "lease_fake_stale_token_123")

    # Real lease still occupies slot -> second acquire is rejected
    with pytest.raises(ConcurrencyLimitError):
        await acquire_concurrency_lease(user_id=user_id, credential_id=cred_id)

    # Releasing real lease succeeds
    await release_concurrency_lease(user_id, cred_id, real_lease.lease_token)


@pytest.mark.anyio
async def test_idempotent_duplicate_release():
    """Duplicate releases on the same lease token are completely harmless."""
    user_id = uuid4()
    cred_id = uuid4()

    lease = await acquire_concurrency_lease(user_id=user_id, credential_id=cred_id)
    await release_concurrency_lease(user_id, cred_id, lease.lease_token)
    # Second release does not error
    await release_concurrency_lease(user_id, cred_id, lease.lease_token)


@pytest.mark.anyio
async def test_expired_lease_automatic_cleanup():
    """Expired leases are pruned by server time on subsequent checks."""
    user_id = uuid4()
    cred_id = uuid4()

    # Acquire lease with 1s duration
    lease = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        lease_duration_seconds=1,
    )

    # Wait 1.1s for lease to expire
    await asyncio.sleep(1.2)

    # Second acquire should succeed because expired lease is pruned by Lua script!
    lease_2 = await acquire_concurrency_lease(
        user_id=user_id,
        credential_id=cred_id,
        lease_duration_seconds=10,
    )
    assert lease_2.lease_token != lease.lease_token


@pytest.mark.anyio
async def test_concurrency_fails_closed_when_redis_unavailable():
    """When Redis is unreachable, concurrency manager fails closed with 503."""
    user_id = uuid4()
    cred_id = uuid4()

    with patch("app.modules.admission.concurrency.get_redis", return_value=None):
        with pytest.raises(AppError) as exc_info:
            await acquire_concurrency_lease(user_id=user_id, credential_id=cred_id)
        assert exc_info.value.status_code == 503
