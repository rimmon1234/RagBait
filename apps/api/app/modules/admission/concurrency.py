"""Distributed concurrency lease management using Redis and atomic Lua scripts."""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.errors import AppError
from app.core.redis import get_redis
from app.modules.admission.errors import ConcurrencyLimitError, LeaseError

logger = logging.getLogger("ragbait.admission.concurrency")

ACQUIRE_CONCURRENCY_LUA = """
local user_key = KEYS[1]
local cred_key = KEYS[2]

local lease_token = ARGV[1]
local max_user_concurrency = tonumber(ARGV[2])
local max_cred_concurrency = tonumber(ARGV[3])
local lease_duration_sec = tonumber(ARGV[4])
local max_lifetime_sec = tonumber(ARGV[5])

local time_res = redis.call('TIME')
local now_us = tonumber(time_res[1]) * 1000000 + tonumber(time_res[2])
local expires_at_us = now_us + (lease_duration_sec * 1000000)

-- Evict expired leases from both sets
redis.call('ZREMRANGEBYSCORE', user_key, 0, now_us)
redis.call('ZREMRANGEBYSCORE', cred_key, 0, now_us)

-- Check user concurrency
local u_count = redis.call('ZCARD', user_key)
if u_count >= max_user_concurrency then
    return {0, "user", u_count}
end

-- Check credential concurrency
local c_count = redis.call('ZCARD', cred_key)
if c_count >= max_cred_concurrency then
    return {0, "credential", c_count}
end

-- Atomically record lease in both scopes
redis.call('ZADD', user_key, expires_at_us, lease_token)
redis.call('ZADD', cred_key, expires_at_us, lease_token)

-- Record the hard maximum lifetime bound for this lease (e.g. 300s)
local max_expires_at_us = now_us + (max_lifetime_sec * 1000000)
redis.call('SET', 'ragbait:lease:max:' .. lease_token, string.format('%.0f', max_expires_at_us), 'EX', max_lifetime_sec + 60)

-- Set safety TTL on sets to ensure cleanup
redis.call('EXPIRE', user_key, max_lifetime_sec + 60)
redis.call('EXPIRE', cred_key, max_lifetime_sec + 60)

return {1, string.format('%.0f', expires_at_us), u_count + 1, c_count + 1}
"""

RENEW_CONCURRENCY_LUA = """
local user_key = KEYS[1]
local cred_key = KEYS[2]

local lease_token = ARGV[1]
local lease_duration_sec = tonumber(ARGV[2])

local time_res = redis.call('TIME')
local now_us = tonumber(time_res[1]) * 1000000 + tonumber(time_res[2])
local new_expires_at_us = now_us + (lease_duration_sec * 1000000)

-- Check if lease exists in both scopes and has not expired
local u_score = redis.call('ZSCORE', user_key, lease_token)
local c_score = redis.call('ZSCORE', cred_key, lease_token)

if not u_score or not c_score then
    return {0, "lease_not_found"}
end

if tonumber(u_score) <= now_us or tonumber(c_score) <= now_us then
    -- Already expired
    redis.call('ZREM', user_key, lease_token)
    redis.call('ZREM', cred_key, lease_token)
    redis.call('DEL', 'ragbait:lease:max:' .. lease_token)
    return {0, "lease_expired"}
end

-- Check hard maximum lifetime bound
local max_exp = redis.call('GET', 'ragbait:lease:max:' .. lease_token)
if not max_exp or now_us >= tonumber(max_exp) then
    redis.call('ZREM', user_key, lease_token)
    redis.call('ZREM', cred_key, lease_token)
    redis.call('DEL', 'ragbait:lease:max:' .. lease_token)
    return {0, "max_lifetime_exceeded"}
end

local max_exp_us = tonumber(max_exp)
if new_expires_at_us > max_exp_us then
    new_expires_at_us = max_exp_us
end

-- Extend lease expiration
redis.call('ZADD', user_key, new_expires_at_us, lease_token)
redis.call('ZADD', cred_key, new_expires_at_us, lease_token)

return {1, string.format('%.0f', new_expires_at_us)}
"""

RELEASE_CONCURRENCY_LUA = """
local user_key = KEYS[1]
local cred_key = KEYS[2]
local lease_token = ARGV[1]

redis.call('ZREM', user_key, lease_token)
redis.call('ZREM', cred_key, lease_token)
redis.call('DEL', 'ragbait:lease:max:' .. lease_token)

return 1
"""


class ConcurrencyLease:
    """Represents an active concurrency reservation lease."""

    def __init__(
        self,
        lease_token: str,
        user_id: UUID,
        credential_id: UUID,
        expires_at: datetime,
    ) -> None:
        self.lease_token = lease_token
        self.user_id = user_id
        self.credential_id = credential_id
        self.expires_at = expires_at


def _build_keys(user_id: UUID, credential_id: UUID) -> tuple[str, str]:
    user_key = f"ragbait:concurrency:user:{user_id}"
    cred_key = f"ragbait:concurrency:credential:{credential_id}"
    return user_key, cred_key


async def acquire_concurrency_lease(
    user_id: UUID,
    credential_id: UUID,
    max_user_concurrency: int | None = None,
    max_cred_concurrency: int | None = None,
    lease_duration_seconds: int | None = None,
) -> ConcurrencyLease:
    """Atomically reserve a concurrency slot for both the user and the credential.

    Fails closed if Redis is unavailable.
    Raises:
        ConcurrencyLimitError: If either user or credential has reached capacity.
        AppError: (503) If Redis is unreachable.
    """
    redis_conn = get_redis()
    if redis_conn is None:
        logger.error("Concurrency manager failing closed: Redis connection unavailable")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission coordination service temporarily unavailable",
            status_code=503,
        )

    user_limit = max_user_concurrency or settings.LLM_USER_MAX_CONCURRENT
    cred_limit = max_cred_concurrency or settings.LLM_CREDENTIAL_MAX_CONCURRENT
    lease_sec = lease_duration_seconds or settings.LLM_RESERVATION_LEASE_SECONDS
    max_lifetime = settings.LLM_MAX_RESERVATION_LIFETIME_SECONDS

    user_key, cred_key = _build_keys(user_id, credential_id)
    lease_token = f"lease_{uuid4().hex}"

    try:
        res = await redis_conn.eval(
            ACQUIRE_CONCURRENCY_LUA,
            2,
            user_key,
            cred_key,
            lease_token,
            user_limit,
            cred_limit,
            lease_sec,
            max_lifetime,
        )

        allowed = res[0]
        if not allowed:
            scope_failed = res[1]
            current_active = res[2]
            limit_val = user_limit if scope_failed == "user" else cred_limit
            logger.warning(
                f"Concurrency limit reached for {scope_failed} (active={current_active}, limit={limit_val})"
            )
            raise ConcurrencyLimitError(
                reason=f"Maximum simultaneous operations reached ({limit_val})",
                scope=scope_failed,
                current_limit=limit_val,
            )

        expires_at_us = int(float(res[1]))
        expires_at = datetime.fromtimestamp(expires_at_us / 1000000, tz=UTC)

        return ConcurrencyLease(
            lease_token=lease_token,
            user_id=user_id,
            credential_id=credential_id,
            expires_at=expires_at,
        )
    except ConcurrencyLimitError:
        raise
    except Exception as e:
        logger.error(f"Error evaluating concurrency lease script: {e}")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission concurrency check failed",
            status_code=503,
        ) from e


async def renew_concurrency_lease(
    user_id: UUID,
    credential_id: UUID,
    lease_token: str,
    lease_duration_seconds: int | None = None,
) -> datetime:
    """Extend the duration of an active concurrency lease.

    Raises:
        LeaseError: If lease is expired or not found.
    """
    redis_conn = get_redis()
    if redis_conn is None:
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission coordination service temporarily unavailable",
            status_code=503,
        )

    lease_sec = lease_duration_seconds or settings.LLM_RESERVATION_LEASE_SECONDS
    user_key, cred_key = _build_keys(user_id, credential_id)

    try:
        res = await redis_conn.eval(
            RENEW_CONCURRENCY_LUA,
            2,
            user_key,
            cred_key,
            lease_token,
            lease_sec,
        )

        success = res[0]
        if not success:
            reason = res[1]
            raise LeaseError(f"Lease renewal rejected: {reason}")

        new_expires_at_us = int(float(res[1]))
        return datetime.fromtimestamp(new_expires_at_us / 1000000, tz=UTC)
    except LeaseError:
        raise
    except Exception as e:
        logger.error(f"Error evaluating lease renewal script: {e}")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission lease renewal failed",
            status_code=503,
        ) from e


async def release_concurrency_lease(
    user_id: UUID,
    credential_id: UUID,
    lease_token: str,
) -> None:
    """Idempotently release the concurrency lease for user and credential."""
    redis_conn = get_redis()
    if redis_conn is None:
        return

    user_key, cred_key = _build_keys(user_id, credential_id)
    try:
        await redis_conn.eval(
            RELEASE_CONCURRENCY_LUA,
            2,
            user_key,
            cred_key,
            lease_token,
        )
    except Exception as e:
        logger.warning(f"Error releasing concurrency lease '{lease_token}': {e}")
