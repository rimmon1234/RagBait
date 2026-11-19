"""Provider error classification and cooldown mapping."""

import email.utils
import time
from collections.abc import Mapping
from typing import Any

from app.core.errors import AppError


class ProviderAdapterError(AppError):
    """Normalized exception representing an upstream AI inference provider failure."""

    def __init__(
        self,
        provider_id: str,
        category: str,
        message: str,
        status_code: int = 502,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            code=f"PROVIDER_{category.upper()}",
            message=message,
            status_code=status_code,
            details={
                "provider_id": provider_id,
                "category": category,
                "retry_after": retry_after,
                **(details or {}),
            },
        )
        self.provider_id = provider_id
        self.category = category
        self.retry_after = retry_after


def parse_retry_after(headers: Mapping[str, str]) -> int | None:
    """Extract and parse cooldown duration from provider rate limit response headers.

    Checks:
      1. `Retry-After` (integer seconds or HTTP date string)
      2. `x-ratelimit-reset-requests` (seconds format or duration string)
    """
    raw_retry = headers.get("retry-after") or headers.get("Retry-After")
    if raw_retry:
        raw_retry = raw_retry.strip()
        # Case A: Integer seconds
        try:
            val = int(raw_retry)
            return max(1, val)
        except ValueError:
            pass

        # Case B: HTTP date
        try:
            dt = email.utils.parsedate_to_datetime(raw_retry)
            now = time.time()
            diff = int(dt.timestamp() - now)
            return max(1, diff) if diff > 0 else 1
        except Exception:
            pass

    # Fallback: check reset-requests
    reset_req = headers.get("x-ratelimit-reset-requests")
    if reset_req:
        try:
            val_f = float(reset_req)
            return max(1, int(val_f))
        except ValueError:
            pass

    return None


def map_http_status_to_category(
    status_code: int,
    body_data: dict[str, Any] | None = None,
) -> tuple[str, str, int]:
    """Map upstream HTTP status code and body into (internal_category, safe_message, http_status).

    Never leaks raw upstream tracebacks or credential fragments in safe_message.
    """
    body = body_data or {}
    err_val = body.get("error")
    err_msg = ""
    if isinstance(err_val, dict):
        err_msg = str(err_val.get("message") or "").lower()

    if status_code in (401, 403):
        return (
            "invalid_credential",
            "The configured provider credential was rejected or lacks permissions",
            400,
        )

    if status_code == 404:
        return (
            "model_unavailable",
            "The requested model is unavailable or not recognized by the upstream provider",
            404,
        )

    if status_code == 429:
        if "quota" in err_msg or "resource_exhausted" in err_msg:
            return (
                "provider_quota_exhausted",
                "The upstream provider reported quota exhaustion for this tier",
                429,
            )
        return (
            "provider_rate_limited",
            "The upstream provider is currently rate limited",
            429,
        )

    if status_code == 400:
        if "safety" in err_msg or "content" in err_msg or "filter" in err_msg:
            return (
                "content_safety_blocked",
                "Generation blocked by provider content and safety policies",
                400,
            )
        if "context" in err_msg or "too large" in err_msg:
            return (
                "request_too_large",
                "The prompt exceeds upstream context length limits",
                400,
            )
        return (
            "invalid_request",
            "The upstream provider rejected the request payload formatting",
            400,
        )

    if status_code in (502, 503, 504, 529):
        return (
            "provider_unavailable",
            "The upstream inference provider is temporarily overloaded or unavailable",
            503,
        )

    if status_code >= 500:
        return (
            "provider_unavailable",
            "An internal error occurred at the upstream provider",
            502,
        )

    return (
        "unknown_provider_error",
        f"Upstream provider returned unexpected status code {status_code}",
        502,
    )
