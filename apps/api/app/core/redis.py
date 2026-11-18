"""Redis client and distributed atomic rate limiting."""

import logging

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.errors import AppError, RateLimitError

logger = logging.getLogger("ragbait.redis")

_redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis | None:
    """Initialize Redis connection."""
    global _redis_client
    if not settings.RATE_LIMIT_ENABLED:
        return None
    try:
        logger.info(f"Connecting to Redis at {settings.REDIS_URL}...")
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        await _redis_client.ping()
        logger.info("Redis connection established.")
        return _redis_client
    except Exception as e:
        logger.warning(f"Could not connect to Redis: {e}")
        _redis_client = None
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(f"Redis is required in production: {e}") from e
        return None


async def close_redis() -> None:
    """Close Redis connection."""
    global _redis_client
    if _redis_client is not None:
        logger.info("Closing Redis connection...")
        await _redis_client.aclose()
        _redis_client = None


def get_redis() -> aioredis.Redis | None:
    """Return active Redis client."""
    return _redis_client


async def check_redis_health() -> bool:
    """Ping Redis to confirm health."""
    if not settings.RATE_LIMIT_ENABLED:
        return True
    if _redis_client is None:
        return False
    try:
        await _redis_client.ping()
        return True
    except Exception:
        return False


# Lua script for atomic sliding window / fixed window counter with expiry
RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end

local ttl = redis.call('TTL', key)
if ttl < 0 then
    ttl = window
    redis.call('EXPIRE', key, window)
end

if current > limit then
    return {0, ttl}
else
    return {1, ttl}
end
"""


async def check_rate_limit(
    key_prefix: str, identifier: str, limit: int, window_seconds: int = 60
) -> None:
    """Enforce atomic rate limit using Redis.

    Raises:
        RateLimitError: If limit is exceeded (contains Retry-After).
        AppError: If Redis is unavailable in production (fail-closed policy).
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    redis_conn = get_redis()
    if redis_conn is None:
        if settings.ENVIRONMENT == "production":
            logger.error("Rate limiter failing closed: Redis unavailable in production")
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                message="Security services temporarily unavailable",
                status_code=503,
            )
        # In non-production, log warning and allow request
        logger.warning("Redis unavailable; rate limit check skipped in development")
        return

    redis_key = f"ragbait:ratelimit:{key_prefix}:{identifier}"

    try:
        res = await redis_conn.eval(RATE_LIMIT_LUA, 1, redis_key, limit, window_seconds)
        allowed, ttl = res[0], res[1]

        if not allowed:
            retry_after = max(int(ttl), 1)
            logger.warning(
                f"Rate limit exceeded for {key_prefix}:{identifier} (limit={limit}, retry_after={retry_after}s)"
            )
            raise RateLimitError(
                retry_after=retry_after,
                message=f"Rate limit exceeded. Please retry in {retry_after} seconds.",
            )
    except RateLimitError:
        raise
    except Exception as e:
        logger.error(f"Redis rate limit check error: {e}")
        if settings.ENVIRONMENT == "production":
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                message="Security rate limit verification failed",
                status_code=503,
            ) from e
