"""Distributed sliding-window rate limiting using Redis server time and Lua scripts."""

import logging
from typing import NamedTuple

from app.core.errors import AppError
from app.core.redis import get_redis
from app.modules.admission.errors import AdmissionRejectionError

logger = logging.getLogger("ragbait.admission.rate")

MULTI_SCOPE_SLIDING_WINDOW_LUA = """
local member_id = ARGV[1]
local window_sec = tonumber(ARGV[2])
local num_keys = #KEYS

local time_res = redis.call('TIME')
local now_us = tonumber(time_res[1]) * 1000000 + tonumber(time_res[2])
local window_us = window_sec * 1000000

-- Phase 1: Verify all keys have capacity without adding entries
for i = 1, num_keys do
    local key = KEYS[i]
    local limit = tonumber(ARGV[2 + (i - 1) * 2 + 1])
    local burst = tonumber(ARGV[2 + (i - 1) * 2 + 2])

    -- Purge timestamps older than the sliding window
    redis.call('ZREMRANGEBYSCORE', key, 0, now_us - window_us)
    local current_count = redis.call('ZCARD', key)

    if (current_count + 1) > (limit + burst) then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_after = 1
        if #oldest >= 2 then
            local oldest_score = tonumber(oldest[2])
            local wait_us = oldest_score + window_us - now_us
            retry_after = math.ceil(wait_us / 1000000)
            if retry_after < 1 then retry_after = 1 end
        end
        return {0, retry_after, i} -- 0 = rejected, retry_after, key_index
    end
end

-- Phase 2: All scopes verified; atomically record slot in all keys
for i = 1, num_keys do
    local key = KEYS[i]
    redis.call('ZADD', key, now_us, member_id)
    redis.call('EXPIRE', key, window_sec + 10)
end

return {1, 0, 0} -- 1 = admitted
"""

RELEASE_RATE_SLOTS_LUA = """
local member_id = ARGV[1]
for i = 1, #KEYS do
    redis.call('ZREM', KEYS[i], member_id)
end
return 1
"""


class RateLimitScope(NamedTuple):
    scope_name: str
    key: str
    limit: int
    burst: int


async def reserve_rate_slots(
    scopes: list[RateLimitScope],
    member_id: str,
    window_seconds: int = 60,
) -> None:
    """Atomically check and reserve rate capacity across all applicable scopes.

    Fails closed if Redis is unavailable.
    Raises:
        AdmissionRejectionError: If any scope limit is exceeded.
        AppError: (503) If Redis is unreachable.
    """
    if not scopes:
        return

    redis_conn = get_redis()
    if redis_conn is None:
        logger.error("Rate limiter failing closed: Redis connection unavailable")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission coordination service temporarily unavailable",
            status_code=503,
        )

    keys = [s.key for s in scopes]
    argv: list[str | int] = [member_id, window_seconds]
    for s in scopes:
        argv.extend([s.limit, s.burst])

    try:
        res = await redis_conn.eval(MULTI_SCOPE_SLIDING_WINDOW_LUA, len(keys), *keys, *argv)
        allowed, retry_after, rejected_idx = res[0], res[1], res[2]

        if not allowed:
            failed_scope = scopes[rejected_idx - 1]
            logger.warning(
                f"Rate limit exceeded on scope '{failed_scope.scope_name}' (limit={failed_scope.limit}, burst={failed_scope.burst}, retry_after={retry_after}s)"
            )
            raise AdmissionRejectionError(
                reason=f"Rate limit exceeded on {failed_scope.scope_name} (limit: {failed_scope.limit} RPM)",
                category=f"rate_limit_{failed_scope.scope_name}",
                retry_after=max(int(retry_after), 1),
                details={"scope": failed_scope.scope_name, "limit": failed_scope.limit},
            )
    except AdmissionRejectionError:
        raise
    except Exception as e:
        logger.error(f"Error evaluating rate limit script: {e}")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission rate limit verification failed",
            status_code=503,
        ) from e


async def release_rate_slots(
    scopes: list[RateLimitScope],
    member_id: str,
) -> None:
    """Release uncommitted rate slots from Redis upon pre-dispatch cancellation."""
    if not scopes:
        return

    redis_conn = get_redis()
    if redis_conn is None:
        return

    keys = [s.key for s in scopes]
    try:
        await redis_conn.eval(RELEASE_RATE_SLOTS_LUA, len(keys), *keys, member_id)
    except Exception as e:
        logger.warning(f"Failed to release rate limit slots for {member_id}: {e}")
