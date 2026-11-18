"""Provider cooldown tracking and Retry-After header parsing."""

import json
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from app.core.errors import AppError
from app.core.redis import get_redis

logger = logging.getLogger("ragbait.admission.cooldown")

# Enforced boundaries for retry_after clamping
MIN_COOLDOWN_SECONDS = 1
MAX_COOLDOWN_SECONDS = 3600  # 1 hour maximum platform cooldown


def parse_retry_after(header_val: str | None) -> int:
    """Parse Retry-After header supporting both delta-seconds and HTTP-date formats.

    Clamps result within [MIN_COOLDOWN_SECONDS, MAX_COOLDOWN_SECONDS].
    Returns default 60s if header is invalid or missing.
    """
    if not header_val or not header_val.strip():
        return 60

    val = header_val.strip()

    # 1. Delta-seconds (most common)
    try:
        seconds = int(val)
        return max(MIN_COOLDOWN_SECONDS, min(seconds, MAX_COOLDOWN_SECONDS))
    except ValueError:
        pass

    # 2. HTTP-date (RFC 7231 / RFC 2822)
    try:
        dt = parsedate_to_datetime(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        diff_seconds = int((dt - now).total_seconds())
        return max(MIN_COOLDOWN_SECONDS, min(diff_seconds, MAX_COOLDOWN_SECONDS))
    except Exception as e:
        logger.warning(f"Could not parse HTTP-date in Retry-After header ('{val}'): {e}")
        return 60


async def set_cooldown(
    scope_type: str,
    scope_id: str,
    retry_after_seconds: int,
    reason: str = "upstream_rate_limited",
    details: dict[str, Any] | None = None,
) -> None:
    """Set an active cooldown in Redis across a specific scope.

    Valid scope types: 'provider', 'account', 'model', 'credential'.
    """
    redis_conn = get_redis()
    if redis_conn is None:
        logger.warning("Cannot set cooldown: Redis is unavailable")
        return

    clamped_sec = max(MIN_COOLDOWN_SECONDS, min(retry_after_seconds, MAX_COOLDOWN_SECONDS))
    key = f"ragbait:cooldown:{scope_type}:{scope_id}"
    payload = {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "reason": reason,
        "cooldown_seconds": clamped_sec,
        "set_at": datetime.now(UTC).isoformat(),
        "details": details or {},
    }

    try:
        await redis_conn.set(key, json.dumps(payload), ex=clamped_sec)
        logger.info(
            f"Cooldown established on {scope_type}:{scope_id} for {clamped_sec}s (reason: {reason})"
        )
    except Exception as e:
        logger.error(f"Failed to record cooldown in Redis: {e}")


async def check_cooldown(
    scopes: list[tuple[str, str]],
) -> tuple[bool, int, str | None]:
    """Check if any of the candidate scopes has an active cooldown.

    Returns:
        (is_cooling_down, remaining_seconds, active_scope_description)
    """
    redis_conn = get_redis()
    if redis_conn is None:
        logger.error("Cooldown check failing closed: Redis connection unavailable")
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Admission coordination service temporarily unavailable",
            status_code=503,
        )

    for scope_type, scope_id in scopes:
        key = f"ragbait:cooldown:{scope_type}:{scope_id}"
        try:
            ttl = await redis_conn.ttl(key)
            if ttl > 0:
                logger.warning(
                    f"Active cooldown found on {scope_type}:{scope_id} ({ttl}s remaining)"
                )
                return True, int(ttl), f"{scope_type}:{scope_id}"
        except Exception as e:
            logger.error(f"Error checking cooldown key '{key}': {e}")
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                message="Admission cooldown check failed",
                status_code=503,
            ) from e

    return False, 0, None
