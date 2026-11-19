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

    Categories:
      - `invalid_credentials`: Upstream rejected authentication (401 or confirmed invalid key).
      - `insufficient_permission`: Generic 403 where credential is authenticated but lacks permission.
      - `model_access_restricted`: Model requires specific entitlement or permissions.
      - `account_restricted`: Account suspended, billing hold, or payment required.
      - `quota_exceeded`: Quota or credit exhaustion (429 or 403 quota exhaustion).
      - `rate_limited`: Transient provider rate limiting (RPM/TPM).
      - `content_safety_blocked`: Content policy or moderation block.
      - `request_too_large`: Prompt exceeds upstream context window.
      - `invalid_request`: Payload syntax or formatting rejection.
      - `model_unavailable`: Upstream 404 for model.
      - `provider_unavailable`: Upstream 5xx service outage.

    Never leaks raw upstream tracebacks, authorization headers, or credential fragments.
    """
    body = body_data or {}
    err_val = body.get("error")

    err_msg = ""
    err_code = ""
    err_status = ""
    details_reasons: list[str] = []

    if isinstance(err_val, dict):
        err_msg = str(err_val.get("message") or "").lower()
        err_code = str(err_val.get("code") or "").lower()
        err_status = str(err_val.get("status") or "").lower()
        raw_details = err_val.get("details")
        if isinstance(raw_details, list):
            for d in raw_details:
                if isinstance(d, dict) and "reason" in d:
                    details_reasons.append(str(d["reason"]).lower())
    elif isinstance(err_val, str):
        err_msg = err_val.lower()

    # 1. 401 Unauthorized: Always authentication failure
    if status_code == 401:
        return (
            "invalid_credentials",
            "The configured provider credential was rejected as invalid or unauthorized",
            400,
        )

    # 2. 403 Forbidden: Differentiate auth, model, account, quota, and generic permission
    if status_code == 403:
        # Check 2A: Confirmed authentication failure evidence
        if (
            "api_key_invalid" in details_reasons
            or "api_key_invalid" in err_code
            or "invalid_api_key" in err_code
            or "key not found" in err_msg
            or "api key not valid" in err_msg
            or "invalid api key" in err_msg
        ):
            return (
                "invalid_credentials",
                "The configured provider credential was rejected as invalid",
                400,
            )

        # Check 2B: Quota or credit exhaustion on 403 (e.g. GCP RESOURCE_EXHAUSTED or OpenRouter out of credits)
        if (
            "quota" in err_msg
            or "resource_exhausted" in err_msg
            or "resource_exhausted" in err_status
            or "credit" in err_msg
            or "insufficient_quota" in err_code
        ):
            return (
                "quota_exceeded",
                "The upstream provider reported quota exhaustion for this credential or project",
                429,
            )

        # Check 2C: Account or billing hold
        if (
            "consumer_suspended" in details_reasons
            or "billing" in err_msg
            or "payment" in err_msg
            or "account suspended" in err_msg
            or "account disabled" in err_msg
            or "deactivated" in err_msg
            or "suspended" in err_msg
        ):
            return (
                "account_restricted",
                "The upstream provider account has billing holds, restrictions, or is suspended",
                403,
            )

        # Check 2D: Model access restriction
        if (
            "model" in err_msg
            or "not permitted to use this model" in err_msg
            or "restricted" in err_msg
            or "whitelist" in err_msg
            or "entitlement" in err_msg
            or "access denied to model" in err_msg
        ):
            return (
                "model_access_restricted",
                "The credential is authenticated but lacks access or entitlement for the requested model",
                403,
            )

        # Check 2E: Generic permission denied (DO NOT mark credential invalid!)
        return (
            "insufficient_permission",
            "The upstream provider rejected access due to insufficient permissions",
            403,
        )

    # 3. 402 Payment Required
    if status_code == 402:
        return (
            "account_restricted",
            "The upstream provider requires payment or account credits are exhausted",
            402,
        )

    # 4. 429 Rate Limit vs Quota Exhaustion
    if status_code == 429:
        if (
            "quota" in err_msg
            or "resource_exhausted" in err_msg
            or "resource_exhausted" in err_status
            or "daily" in err_msg
            or "monthly" in err_msg
            or "credit" in err_msg
            or "insufficient_quota" in err_code
            or "quota_exceeded" in err_code
        ):
            return (
                "quota_exceeded",
                "The upstream provider reported quota exhaustion for this tier",
                429,
            )
        return (
            "rate_limited",
            "The upstream provider is currently rate limited",
            429,
        )

    # 5. 400 Bad Request
    if status_code == 400:
        # Gemini sometimes returns 400 with API_KEY_INVALID in details or message
        if (
            "api_key_invalid" in details_reasons
            or "api key not valid" in err_msg
            or "invalid api key" in err_msg
        ):
            return (
                "invalid_credentials",
                "The configured provider credential was rejected as invalid",
                400,
            )

        if "safety" in err_msg or "content" in err_msg or "filter" in err_msg:
            return (
                "content_safety_blocked",
                "Generation blocked by provider content and safety policies",
                400,
            )

        if "context" in err_msg or "too large" in err_msg or "maximum context length" in err_msg:
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

    # 6. 404 Not Found
    if status_code == 404:
        return (
            "model_unavailable",
            "The requested model is unavailable or not recognized by the upstream provider",
            404,
        )

    # 7. 5xx Service Unavailable / Bad Gateway
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

    # 8. Unclassified fallback
    return (
        "invalid_request",
        f"The upstream provider returned HTTP {status_code}",
        400,
    )
