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
    local limit = tonumber(ARGV[2 + (i - 1) * 3 + 1])
    local burst = tonumber(ARGV[2 + (i - 1) * 3 + 2])
    local weight = tonumber(ARGV[2 + (i - 1) * 3 + 3]) or 1

    -- Purge timestamps older than the sliding window
    redis.call('ZREMRANGEBYSCORE', key, 0, now_us - window_us)

    -- Calculate current volume (requests or tokens)
    local current_volume = 0
    local entries = redis.call('ZRANGE', key, 0, -1)
    for _, entry in ipairs(entries) do
        local token_weight = string.match(entry, ":(%d+)$")
        if token_weight then
            current_volume = current_volume + (tonumber(token_weight) or 1)
        else
            current_volume = current_volume + 1
        end
    end

    if (current_volume + weight) > (limit + burst) then
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
    local weight = tonumber(ARGV[2 + (i - 1) * 3 + 3]) or 1
    local entry_val = member_id
    if weight > 1 then
        entry_val = member_id .. ":" .. weight
    end
    redis.call('ZADD', key, now_us, entry_val)
    redis.call('EXPIRE', key, window_sec + 10)
end

return {1, 0, 0} -- 1 = admitted
"""

RELEASE_RATE_SLOTS_LUA = """
local member_id = ARGV[1]
for i = 1, #KEYS do
    local key = KEYS[i]
    redis.call('ZREM', key, member_id)
    local entries = redis.call('ZRANGE', key, 0, -1)
    for _, entry in ipairs(entries) do
        if entry == member_id or string.sub(entry, 1, #member_id + 1) == (member_id .. ":") then
            redis.call('ZREM', key, entry)
        end
    end
end
return 1
"""

RECONCILE_RATE_SLOTS_LUA = """
local member_id = ARGV[1]
local actual_weight = tonumber(ARGV[2])
for i = 1, #KEYS do
    local key = KEYS[i]
    local entries = redis.call('ZRANGE', key, 0, -1, 'WITHSCORES')
    for j = 1, #entries, 2 do
        local entry = entries[j]
        local score = entries[j + 1]
        if entry == member_id or string.sub(entry, 1, #member_id + 1) == (member_id .. ":") then
            redis.call('ZREM', key, entry)
            local new_val = member_id
            if actual_weight > 1 then
                new_val = member_id .. ":" .. actual_weight
            end
            redis.call('ZADD', key, score, new_val)
        end
    end
end
return 1
"""


class RateLimitScope(NamedTuple):
    scope_name: str
    key: str
    limit: int
    burst: int
    weight: int = 1
    unit: str = "requests"


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
        argv.extend([s.limit, s.burst, s.weight])

    try:
        res = await redis_conn.eval(MULTI_SCOPE_SLIDING_WINDOW_LUA, len(keys), *keys, *argv)
        allowed, retry_after, rejected_idx = res[0], res[1], res[2]

        if not allowed:
            failed_scope = scopes[rejected_idx - 1]
            unit_label = "RPM" if failed_scope.unit == "requests" else "TPM"
            logger.warning(
                f"Rate limit exceeded on scope '{failed_scope.scope_name}' (limit={failed_scope.limit} {unit_label}, burst={failed_scope.burst}, retry_after={retry_after}s)"
            )
            raise AdmissionRejectionError(
                reason=f"Rate limit exceeded on {failed_scope.scope_name} (limit: {failed_scope.limit} {unit_label})",
                category=f"rate_limit_{failed_scope.scope_name}",
                retry_after=max(int(retry_after), 1),
                details={
                    "scope": failed_scope.scope_name,
                    "limit": failed_scope.limit,
                    "unit": failed_scope.unit,
                },
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


async def reconcile_rate_slots(
    scopes: list[RateLimitScope],
    member_id: str,
    actual_tokens: int,
) -> None:
    """Reconcile token rate slots in Redis with actual token usage."""
    token_scopes = [s for s in scopes if s.unit == "tokens"]
    if not token_scopes:
        return

    redis_conn = get_redis()
    if redis_conn is None:
        return

    keys = [s.key for s in token_scopes]
    try:
        await redis_conn.eval(RECONCILE_RATE_SLOTS_LUA, len(keys), *keys, member_id, actual_tokens)
    except Exception as e:
        logger.warning(f"Failed to reconcile rate limit slots for {member_id}: {e}")
