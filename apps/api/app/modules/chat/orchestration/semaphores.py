"""Distributed Redis semaphores, UUID4 permit leases, bounded waiting, and heartbeat renewal."""

import asyncio
import logging
import random
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.core.redis import get_redis
from app.modules.chat.orchestration.config import generation_settings

logger = logging.getLogger("ragbait.chat.orchestration.semaphores")

# Global key mandated by M4.0 architecture
GLOBAL_CLOUD_SEMAPHORE_KEY = "sem:global:cloud"
GLOBAL_CLOUD_WAITERS_KEY = "sem:global:cloud:waiters"


def conversation_semaphore_key(conversation_id: UUID) -> str:
    """Return Redis ZSET semaphore key scoping max active generations per conversation."""
    return f"sem:conv:{conversation_id}"


def user_semaphore_key(user_id: UUID) -> str:
    """Return Redis ZSET semaphore key scoping max active generations per user."""
    return f"sem:user:{user_id}"


# Atomic Lua acquisition script across conversation (1), user (2), and global cloud (10) scopes.
# Guarantees all three permits succeed or NONE are added (zero partial leak).
ACQUIRE_CLOUD_PERMITS_LUA = """
local conv_key = KEYS[1]
local user_key = KEYS[2]
local global_key = KEYS[3]
local waiter_key = KEYS[4]

local lease_token = ARGV[1]
local conv_limit = tonumber(ARGV[2])
local user_limit = tonumber(ARGV[3])
local global_limit = tonumber(ARGV[4])
local ttl_sec = tonumber(ARGV[5])

local t = redis.call('TIME')
local now_us = tonumber(t[1]) * 1000000 + tonumber(t[2])
local expires_us = now_us + (ttl_sec * 1000000)

redis.call('ZREMRANGEBYSCORE', conv_key, '-inf', now_us)
redis.call('ZREMRANGEBYSCORE', user_key, '-inf', now_us)
redis.call('ZREMRANGEBYSCORE', global_key, '-inf', now_us)
redis.call('ZREMRANGEBYSCORE', waiter_key, '-inf', now_us)

local c_cnt = redis.call('ZCARD', conv_key)
if c_cnt >= conv_limit then
    redis.call('ZREM', waiter_key, lease_token)
    return {0, 'conversation', c_cnt}
end

local u_cnt = redis.call('ZCARD', user_key)
if u_cnt >= user_limit then
    redis.call('ZREM', waiter_key, lease_token)
    return {0, 'user', u_cnt}
end

local g_cnt = redis.call('ZCARD', global_key)
if g_cnt >= global_limit then
    return {0, 'global', g_cnt}
end

redis.call('ZADD', conv_key, expires_us, lease_token)
redis.call('ZADD', user_key, expires_us, lease_token)
redis.call('ZADD', global_key, expires_us, lease_token)
redis.call('ZREM', waiter_key, lease_token)

local safety_ex = ttl_sec + 120
redis.call('EXPIRE', conv_key, safety_ex)
redis.call('EXPIRE', user_key, safety_ex)
redis.call('EXPIRE', global_key, safety_ex)

return {1, string.format('%.0f', expires_us)}
"""

# Atomic Lua renewal script: extends permit TTL ONLY if lease_token exists and is unexpired in ALL 3 scopes.
RENEW_CLOUD_PERMITS_LUA = """
local conv_key = KEYS[1]
local user_key = KEYS[2]
local global_key = KEYS[3]

local lease_token = ARGV[1]
local ttl_sec = tonumber(ARGV[2])

local t = redis.call('TIME')
local now_us = tonumber(t[1]) * 1000000 + tonumber(t[2])
local new_expires_us = now_us + (ttl_sec * 1000000)

local c_score = redis.call('ZSCORE', conv_key, lease_token)
local u_score = redis.call('ZSCORE', user_key, lease_token)
local g_score = redis.call('ZSCORE', global_key, lease_token)

if (not c_score) or (not u_score) or (not g_score) then
    return {0, 'lease_missing'}
end

if tonumber(c_score) <= now_us or tonumber(u_score) <= now_us or tonumber(g_score) <= now_us then
    redis.call('ZREM', conv_key, lease_token)
    redis.call('ZREM', user_key, lease_token)
    redis.call('ZREM', global_key, lease_token)
    return {0, 'lease_expired'}
end

redis.call('ZADD', conv_key, new_expires_us, lease_token)
redis.call('ZADD', user_key, new_expires_us, lease_token)
redis.call('ZADD', global_key, new_expires_us, lease_token)

local safety_ex = ttl_sec + 120
redis.call('EXPIRE', conv_key, safety_ex)
redis.call('EXPIRE', user_key, safety_ex)
redis.call('EXPIRE', global_key, safety_ex)

return {1, string.format('%.0f', new_expires_us)}
"""

# Atomic owner-verified release script: removes ONLY this UUID4 lease_token from each scope.
RELEASE_CLOUD_PERMITS_LUA = """
local conv_key = KEYS[1]
local user_key = KEYS[2]
local global_key = KEYS[3]
local waiter_key = KEYS[4]

local lease_token = ARGV[1]

local r1 = redis.call('ZREM', conv_key, lease_token)
local r2 = redis.call('ZREM', user_key, lease_token)
local r3 = redis.call('ZREM', global_key, lease_token)
redis.call('ZREM', waiter_key, lease_token)

return r1 + r2 + r3
"""


class CloudPermitRejection(AppError):
    """Typed HTTP error raised when cloud concurrency limits reject a generation request pre-stream."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=status_code,
            details=details,
        )


class CloudGenerationPermit:
    """Owned Redis semaphore lease across conversation, user, and global cloud scopes."""

    def __init__(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        lease_token: str,
        ttl_seconds: int,
        expires_at: datetime,
    ) -> None:
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.lease_token = lease_token
        self.ttl_seconds = ttl_seconds
        self.expires_at = expires_at
        self.released = False
        self.lease_lost_event = asyncio.Event()
        self.heartbeat_task: asyncio.Task[None] | None = None


def _permit_keys(conversation_id: UUID, user_id: UUID) -> tuple[str, str, str, str]:
    return (
        conversation_semaphore_key(conversation_id),
        user_semaphore_key(user_id),
        GLOBAL_CLOUD_SEMAPHORE_KEY,
        GLOBAL_CLOUD_WAITERS_KEY,
    )


async def try_acquire_cloud_permits(
    *,
    conversation_id: UUID,
    user_id: UUID,
    lease_token: str | None = None,
    conv_limit: int | None = None,
    user_limit: int | None = None,
    global_limit: int | None = None,
    ttl_seconds: int | None = None,
) -> tuple[CloudGenerationPermit | None, str | None, int]:
    """Attempt single atomic Redis acquisition across conversation/user/global scopes.

    Returns:
        (permit, blocked_scope, current_active_count)
    Raises:
        AppError(503 SERVICE_UNAVAILABLE) if Redis is unavailable (fail-closed policy).
    """
    redis_conn = get_redis()
    if redis_conn is None:
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Cloud concurrency service is temporarily unavailable",
            status_code=503,
        )

    eff_token = lease_token or f"perm_{uuid4().hex}"
    c_lim = conv_limit or generation_settings.GENERATION_CONVERSATION_LIMIT
    u_lim = user_limit or generation_settings.GENERATION_USER_LIMIT
    g_lim = global_limit or generation_settings.GENERATION_GLOBAL_CLOUD_LIMIT
    ttl = ttl_seconds or generation_settings.GENERATION_LEASE_TTL_SECONDS
    c_key, u_key, g_key, w_key = _permit_keys(conversation_id, user_id)

    try:
        res = await redis_conn.eval(
            ACQUIRE_CLOUD_PERMITS_LUA,
            4,
            c_key,
            u_key,
            g_key,
            w_key,
            eff_token,
            c_lim,
            u_lim,
            g_lim,
            ttl,
        )
    except Exception as exc:
        logger.error(f"Redis semaphore acquisition error: {exc}")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Cloud concurrency service temporarily unreachable",
            status_code=503,
        ) from exc

    allowed = int(res[0])
    if allowed == 1:
        expires_us = int(float(res[1]))
        expires_at = datetime.fromtimestamp(expires_us / 1_000_000, tz=UTC)
        permit = CloudGenerationPermit(
            conversation_id=conversation_id,
            user_id=user_id,
            lease_token=eff_token,
            ttl_seconds=ttl,
            expires_at=expires_at,
        )
        return permit, None, 0

    blocked_scope = str(res[1])
    active_count = int(res[2])
    return None, blocked_scope, active_count


async def register_waiter(waiter_token: str, wait_ttl_seconds: int = 45) -> int:
    """Register waiter in sorted queue and return approximate 1-based position."""
    redis_conn = get_redis()
    if redis_conn is None:
        return 1
    try:
        now_score = datetime.now(UTC).timestamp() + wait_ttl_seconds
        await redis_conn.zadd(GLOBAL_CLOUD_WAITERS_KEY, {waiter_token: now_score})
        await redis_conn.expire(GLOBAL_CLOUD_WAITERS_KEY, wait_ttl_seconds + 30)
        rank = await redis_conn.zrank(GLOBAL_CLOUD_WAITERS_KEY, waiter_token)
        return int(rank) + 1 if isinstance(rank, int) else 1
    except Exception:
        return 1


async def unregister_waiter(waiter_token: str) -> None:
    """Cleanly remove waiter entry on completion, abort, or timeout."""
    redis_conn = get_redis()
    if redis_conn is None:
        return
    with suppress(Exception):
        await redis_conn.zrem(GLOBAL_CLOUD_WAITERS_KEY, waiter_token)


async def renew_cloud_permits(permit: CloudGenerationPermit) -> bool:
    """Renew TTL across all 3 Redis semaphore scopes strictly if still owned by this permit."""
    if permit.released:
        return False
    redis_conn = get_redis()
    if redis_conn is None:
        return False

    c_key, u_key, g_key, _ = _permit_keys(permit.conversation_id, permit.user_id)
    try:
        res = await redis_conn.eval(
            RENEW_CLOUD_PERMITS_LUA,
            3,
            c_key,
            u_key,
            g_key,
            permit.lease_token,
            permit.ttl_seconds,
        )
        if int(res[0]) == 1:
            expires_us = int(float(res[1]))
            permit.expires_at = datetime.fromtimestamp(expires_us / 1_000_000, tz=UTC)
            return True
        return False
    except Exception as exc:
        logger.warning(f"Redis permit renewal error for {permit.lease_token}: {exc}")
        return False


async def release_cloud_permits(permit: CloudGenerationPermit) -> int:
    """Release ONLY permits belonging to `permit.lease_token`. Returns count of removed scopes."""
    if permit.released:
        return 0
    permit.released = True
    redis_conn = get_redis()
    if redis_conn is None:
        return 0

    c_key, u_key, g_key, w_key = _permit_keys(permit.conversation_id, permit.user_id)
    try:
        removed = await redis_conn.eval(
            RELEASE_CLOUD_PERMITS_LUA,
            4,
            c_key,
            u_key,
            g_key,
            w_key,
            permit.lease_token,
        )
        return int(removed or 0)
    except Exception as exc:
        logger.warning(f"Redis permit release error for {permit.lease_token}: {exc}")
        return 0


def start_permit_heartbeat(
    permit: CloudGenerationPermit,
    *,
    heartbeat_seconds: float | None = None,
) -> asyncio.Task[None]:
    """Launch dedicated cancellable heartbeat task renewing semaphore leases periodically."""
    interval = float(heartbeat_seconds or generation_settings.GENERATION_HEARTBEAT_SECONDS)

    async def _heartbeat_loop() -> None:
        try:
            while not permit.released and not permit.lease_lost_event.is_set():
                await asyncio.sleep(interval)
                if permit.released or permit.lease_lost_event.is_set():
                    break
                ok = await renew_cloud_permits(permit)
                if not ok:
                    logger.warning(
                        f"Lost cloud generation lease {permit.lease_token} on conv={permit.conversation_id}"
                    )
                    permit.lease_lost_event.set()
                    break
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_heartbeat_loop())
    permit.heartbeat_task = task
    return task


async def stop_permit_heartbeat(permit: CloudGenerationPermit) -> None:
    """Cancel and await heartbeat background task safely."""
    task = permit.heartbeat_task
    if task is not None:
        permit.heartbeat_task = None
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


def raise_for_blocked_scope(
    *,
    scope: str,
    conversation_id: UUID,
    conv_limit: int,
    user_limit: int,
) -> None:
    """Translate immediate concurrency block into exact M4.0 error contract."""
    if scope == "conversation":
        raise CloudPermitRejection(
            code="CONVERSATION_CONCURRENCY_LIMIT",
            message="Another response is currently generating in this conversation. Please wait for it to complete.",
            status_code=409,
            details={"conversation_id": str(conversation_id), "limit": conv_limit},
        )
    if scope == "user":
        raise CloudPermitRejection(
            code="USER_CONCURRENCY_LIMIT",
            message=f"Maximum {user_limit} concurrent generations per student reached.",
            status_code=429,
            details={"scope": "user", "limit": user_limit, "retry_after": 2},
        )
    raise CloudPermitRejection(
        code="CONCURRENCY_TIMEOUT",
        message="Cloud generation capacity is currently busy. Please retry shortly.",
        status_code=429,
        details={"scope": "global", "retry_after": 5},
    )


async def wait_for_sync_cloud_permits(
    *,
    conversation_id: UUID,
    user_id: UUID,
    timeout_seconds: float | None = None,
) -> CloudGenerationPermit:
    """Acquire permits synchronously (used by stream=False calls), waiting up to timeout on global capacity."""
    max_wait = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(generation_settings.GENERATION_ADMISSION_TIMEOUT_SECONDS)
    )
    deadline = asyncio.get_running_loop().time() + max_wait
    token = f"perm_{uuid4().hex}"
    await register_waiter(token, wait_ttl_seconds=int(max_wait) + 15)
    delay = 0.15

    try:
        while True:
            permit, blocked_scope, _ = await try_acquire_cloud_permits(
                conversation_id=conversation_id,
                user_id=user_id,
                lease_token=token,
            )
            if permit is not None:
                return permit
            assert blocked_scope is not None
            if blocked_scope in ("conversation", "user"):
                raise_for_blocked_scope(
                    scope=blocked_scope,
                    conversation_id=conversation_id,
                    conv_limit=generation_settings.GENERATION_CONVERSATION_LIMIT,
                    user_limit=generation_settings.GENERATION_USER_LIMIT,
                )

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise_for_blocked_scope(
                    scope="global",
                    conversation_id=conversation_id,
                    conv_limit=generation_settings.GENERATION_CONVERSATION_LIMIT,
                    user_limit=generation_settings.GENERATION_USER_LIMIT,
                )

            await asyncio.sleep(min(delay + random.uniform(0.0, 0.05), remaining))
            delay = min(0.5, delay * 1.4)
    finally:
        await unregister_waiter(token)
