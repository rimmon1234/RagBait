"""M4.6 tests: real Redis 3-scope distributed semaphores, leases, heartbeats, and multiprocess safety."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.chat.orchestration.semaphores import (
    GLOBAL_CLOUD_SEMAPHORE_KEY,
    GLOBAL_CLOUD_WAITERS_KEY,
    CloudGenerationPermit,
    CloudPermitRejection,
    conversation_semaphore_key,
    release_cloud_permits,
    renew_cloud_permits,
    start_permit_heartbeat,
    stop_permit_heartbeat,
    try_acquire_cloud_permits,
    user_semaphore_key,
    wait_for_sync_cloud_permits,
)


@pytest.fixture(autouse=True)
async def _redis_lifecycle():
    await init_redis()
    redis_conn = get_redis()
    assert redis_conn is not None
    await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
    yield
    redis_conn = get_redis()
    if redis_conn is not None:
        await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
    await close_redis()


async def _cleanup_keys(conv_ids: list[Any], user_ids: list[Any]) -> None:
    redis_conn = get_redis()
    if redis_conn is None:
        return
    keys = [GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY]
    keys.extend(conversation_semaphore_key(c) for c in conv_ids)
    keys.extend(user_semaphore_key(u) for u in user_ids)
    if keys:
        await redis_conn.delete(*keys)


class TestRedisDistributedSemaphores:
    """Verify atomic 1/conv, 2/user, 10/global semaphore enforcement against live Redis."""

    async def test_conversation_user_and_global_limits_with_zero_partial_leak(self) -> None:
        u1 = uuid4()
        u1_convs = [uuid4(), uuid4(), uuid4()]
        other_users: list[Any] = []
        other_convs: list[Any] = []

        # 1. Conversation limit = 1
        p1, scope, _ = await try_acquire_cloud_permits(conversation_id=u1_convs[0], user_id=u1)
        assert p1 is not None and scope is None

        p_dup_conv, scope_dup, _ = await try_acquire_cloud_permits(
            conversation_id=u1_convs[0], user_id=u1
        )
        assert p_dup_conv is None
        assert scope_dup == "conversation"

        # 2. User limit = 2 across distinct conversations
        p2, scope2, _ = await try_acquire_cloud_permits(conversation_id=u1_convs[1], user_id=u1)
        assert p2 is not None and scope2 is None

        p3_blocked, scope3, cnt3 = await try_acquire_cloud_permits(
            conversation_id=u1_convs[2], user_id=u1
        )
        assert p3_blocked is None
        assert scope3 == "user"
        assert cnt3 == 2

        redis_conn = get_redis()
        assert redis_conn is not None
        # Rejected acquisition must leak ZERO entries into conversation 3's semaphore
        assert await redis_conn.zcard(conversation_semaphore_key(u1_convs[2])) == 0

        # 3. Fill global cloud semaphore up to 10 (currently 2 used -> add 8 distinct users)
        other_permits = []
        for _ in range(8):
            ou = uuid4()
            oc = uuid4()
            other_users.append(ou)
            other_convs.append(oc)
            op, s, _ = await try_acquire_cloud_permits(conversation_id=oc, user_id=ou)
            assert op is not None and s is None
            other_permits.append(op)

        assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 10

        # 11th fresh user/conv must be blocked globally WITHOUT leaking into conv/user sets
        overflow_u = uuid4()
        overflow_c = uuid4()
        other_users.append(overflow_u)
        other_convs.append(overflow_c)
        p11, scope11, cnt11 = await try_acquire_cloud_permits(
            conversation_id=overflow_c, user_id=overflow_u
        )
        assert p11 is None
        assert scope11 == "global"
        assert cnt11 == 10
        assert await redis_conn.zcard(conversation_semaphore_key(overflow_c)) == 0
        assert await redis_conn.zcard(user_semaphore_key(overflow_u)) == 0

        # 4. Release 1 slot -> 11th user immediately acquires
        assert await release_cloud_permits(other_permits[0]) == 3
        p11_ok, scope11_ok, _ = await try_acquire_cloud_permits(
            conversation_id=overflow_c, user_id=overflow_u
        )
        assert p11_ok is not None and scope11_ok is None

        for perm in [p1, p2, p11_ok, *other_permits[1:]]:
            await release_cloud_permits(perm)
        assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0
        await _cleanup_keys([*u1_convs, *other_convs], [u1, *other_users])

    async def test_stale_foreign_lease_release_cannot_evict_valid_owner(self) -> None:
        uid = uuid4()
        cid = uuid4()
        owner, _, _ = await try_acquire_cloud_permits(
            conversation_id=cid, user_id=uid, ttl_seconds=10
        )
        assert owner is not None

        # Forge a permit object with wrong lease_token
        forged = CloudGenerationPermit(
            conversation_id=cid,
            user_id=uid,
            lease_token="forged_token_xyz",
            ttl_seconds=10,
            expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        removed = await release_cloud_permits(forged)
        assert removed == 0

        # Real owner still holds slot (blocked for duplicate)
        dup, scope, _ = await try_acquire_cloud_permits(conversation_id=cid, user_id=uid)
        assert dup is None and scope == "conversation"

        assert await release_cloud_permits(owner) == 3
        await _cleanup_keys([cid], [uid])

    async def test_heartbeat_renewal_extends_ttl_and_detects_lease_loss(self) -> None:
        uid = uuid4()
        cid = uuid4()
        # Short 2s TTL with 0.35s heartbeat must keep lease alive past 2.4s!
        permit, _, _ = await try_acquire_cloud_permits(
            conversation_id=cid, user_id=uid, ttl_seconds=2
        )
        assert permit is not None
        initial_expiry = permit.expires_at

        start_permit_heartbeat(permit, heartbeat_seconds=0.35)
        try:
            await asyncio.sleep(2.3)
            assert not permit.lease_lost_event.is_set()
            assert permit.expires_at > initial_expiry

            # Slot is STILL held actively
            dup, scope, _ = await try_acquire_cloud_permits(conversation_id=cid, user_id=uid)
            assert dup is None and scope == "conversation"

            # Simulate lease eviction / TTL expiry externally
            redis_conn = get_redis()
            assert redis_conn is not None
            await redis_conn.zrem(GLOBAL_CLOUD_SEMAPHORE_KEY, permit.lease_token)

            await asyncio.sleep(0.55)
            assert permit.lease_lost_event.is_set()
            assert await renew_cloud_permits(permit) is False
        finally:
            await stop_permit_heartbeat(permit)
            await release_cloud_permits(permit)
            await _cleanup_keys([cid], [uid])

    async def test_expired_lease_cannot_be_revived(self) -> None:
        uid = uuid4()
        cid = uuid4()
        permit, _, _ = await try_acquire_cloud_permits(
            conversation_id=cid, user_id=uid, ttl_seconds=1
        )
        assert permit is not None
        await asyncio.sleep(1.15)
        assert await renew_cloud_permits(permit) is False
        # Subsequent caller cleanly reclaims expired slot
        reclaimed, scope, _ = await try_acquire_cloud_permits(
            conversation_id=cid, user_id=uid, ttl_seconds=10
        )
        assert reclaimed is not None and scope is None
        await release_cloud_permits(reclaimed)
        await _cleanup_keys([cid], [uid])

    async def test_bounded_global_wait_times_out_and_cleans_waiter(self) -> None:
        held: list[Any] = []
        users: list[Any] = []
        convs: list[Any] = []
        for _ in range(10):
            u, c = uuid4(), uuid4()
            users.append(u)
            convs.append(c)
            p, _, _ = await try_acquire_cloud_permits(conversation_id=c, user_id=u)
            assert p is not None
            held.append(p)

        waiter_u, waiter_c = uuid4(), uuid4()
        users.append(waiter_u)
        convs.append(waiter_c)
        with pytest.raises(CloudPermitRejection) as exc_info:
            await wait_for_sync_cloud_permits(
                conversation_id=waiter_c,
                user_id=waiter_u,
                timeout_seconds=0.5,
            )
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "CONCURRENCY_TIMEOUT"

        redis_conn = get_redis()
        assert redis_conn is not None
        assert await redis_conn.zcard(GLOBAL_CLOUD_WAITERS_KEY) == 0

        for p in held:
            await release_cloud_permits(p)
        await _cleanup_keys(convs, users)

    async def test_redis_outage_fails_closed_with_503(self) -> None:
        with patch("app.modules.chat.orchestration.semaphores.get_redis", return_value=None):
            with pytest.raises(AppError) as exc_info:
                await try_acquire_cloud_permits(conversation_id=uuid4(), user_id=uuid4())
        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"


class TestMultiProcessSemaphoreContention:
    """Spawn independent worker OS processes competing against the same Redis instance."""

    async def test_two_worker_processes_enforce_cross_process_limits(self) -> None:
        shared_user = str(uuid4())
        conv_ids = [str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())]
        api_root = str(Path(__file__).resolve().parents[1])

        worker_script = """
import asyncio, json, sys
from uuid import UUID
from app.core.redis import init_redis, close_redis
from app.modules.chat.orchestration.semaphores import try_acquire_cloud_permits, release_cloud_permits

async def main():
    user_id = UUID(sys.argv[1])
    conv_a = UUID(sys.argv[2])
    conv_b = UUID(sys.argv[3])
    await init_redis()
    try:
        acquired = []
        blocked = []
        for c in (conv_a, conv_b):
            p, scope, _ = await try_acquire_cloud_permits(conversation_id=c, user_id=user_id, ttl_seconds=15)
            if p is not None:
                acquired.append(p)
            else:
                blocked.append(scope)
        await asyncio.sleep(0.6)
        for p in acquired:
            await release_cloud_permits(p)
        print(json.dumps({"acquired": len(acquired), "blocked": blocked}))
    finally:
        await close_redis()

asyncio.run(main())
"""
        proc1 = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker_script,
            shared_user,
            conv_ids[0],
            conv_ids[1],
            cwd=api_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc2 = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker_script,
            shared_user,
            conv_ids[2],
            conv_ids[3],
            cwd=api_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        out1, err1 = await proc1.communicate()
        out2, err2 = await proc2.communicate()
        assert proc1.returncode == 0, err1.decode("utf-8", errors="replace")
        assert proc2.returncode == 0, err2.decode("utf-8", errors="replace")

        res1 = json.loads(out1.decode("utf-8").strip().splitlines()[-1])
        res2 = json.loads(out2.decode("utf-8").strip().splitlines()[-1])

        total_acquired = int(res1["acquired"]) + int(res2["acquired"])
        total_blocked = res1["blocked"] + res2["blocked"]
        # Max 2/user across BOTH separate OS processes!
        assert total_acquired == 2
        assert len(total_blocked) == 2
        assert all(s == "user" for s in total_blocked)

        redis_conn = get_redis()
        assert redis_conn is not None
        assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0
