"""Tests for distributed rate limiting and fail-secure behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.errors import AppError, RateLimitError
from app.core.redis import check_rate_limit


@pytest.mark.asyncio
async def test_rate_limit_exceeded_raises_429() -> None:
    """When Redis Lua script returns allowed=0, RateLimitError must be raised with retry_after."""
    mock_redis = AsyncMock()
    # Return [allowed=0, ttl=45]
    mock_redis.eval.return_value = [0, 45]

    with patch("app.core.redis.get_redis", return_value=mock_redis):
        with pytest.raises(RateLimitError) as exc_info:
            await check_rate_limit(
                key_prefix="test", identifier="user_123", limit=10, window_seconds=60
            )

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 45
        assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_within_bounds_passes() -> None:
    """When Redis Lua script returns allowed=1, function completes normally."""
    mock_redis = AsyncMock()
    mock_redis.eval.return_value = [1, 58]

    with patch("app.core.redis.get_redis", return_value=mock_redis):
        # Should not raise
        await check_rate_limit(
            key_prefix="test", identifier="user_123", limit=10, window_seconds=60
        )


@pytest.mark.asyncio
async def test_redis_unavailable_in_production_fails_closed() -> None:
    """In production, if Redis is down, rate limiting must fail closed with 503 Service Unavailable."""
    with (
        patch("app.core.redis.get_redis", return_value=None),
        patch.object(settings, "ENVIRONMENT", "production"),
    ):
        with pytest.raises(AppError) as exc_info:
            await check_rate_limit(key_prefix="test", identifier="user_123", limit=10)
        assert exc_info.value.status_code == 503
