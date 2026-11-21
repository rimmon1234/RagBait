"""Safe, Non-Generative Provider Authentication Validation Probes."""

import logging
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger("ragbait.credentials.validator")

# Bounded response read limit to prevent memory exhaustion (64 KB)
MAX_RESPONSE_BYTES = 64 * 1024


class ValidationOutcome(StrEnum):
    """Normalized validation outcomes for credential probes."""

    VALID = "valid"
    INVALID_CREDENTIAL = "invalid_credential"
    INSUFFICIENT_PERMISSION = "insufficient_permission"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED = "unsupported"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True)
class ValidationResult:
    """Sanitized validation probe result. Never contains raw response or secrets."""

    outcome: ValidationOutcome
    safe_code: str
    message: str
    billing_tier: str = "unknown"
    billing_error: str | None = None


# Timeout bounds: 5s connect, 10s read
PROBE_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


async def probe_gemini(raw_key: str) -> ValidationResult:
    """Validate Google Gemini API key by listing available models (non-generative)."""
    url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
    headers = {"x-goog-api-key": raw_key}

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                return ValidationResult(
                    outcome=ValidationOutcome.VALID,
                    safe_code="AUTHENTICATED",
                    message="Gemini API credential authenticated successfully.",
                    billing_tier="unknown",
                    billing_error="Gemini API does not expose non-generative account billing verification endpoint",
                )
            if resp.status_code in (400, 403):
                return ValidationResult(
                    outcome=ValidationOutcome.INVALID_CREDENTIAL,
                    safe_code="INVALID_API_KEY",
                    message="The provided Gemini API key is invalid or unrecognized.",
                )
            if resp.status_code == 429:
                return ValidationResult(
                    outcome=ValidationOutcome.RATE_LIMITED,
                    safe_code="RATE_LIMITED",
                    message="Provider returned rate limit during validation.",
                )
            if resp.status_code >= 500:
                return ValidationResult(
                    outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
                    safe_code="UPSTREAM_5XX",
                    message="Gemini API is currently unavailable or returning server errors.",
                )

            return ValidationResult(
                outcome=ValidationOutcome.VALIDATION_ERROR,
                safe_code=f"HTTP_{resp.status_code}",
                message="Unexpected response from Gemini authentication check.",
            )
    except httpx.TimeoutException:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="TIMEOUT",
            message="Connection to Gemini authentication service timed out.",
        )
    except httpx.ConnectError:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="CONNECTION_FAILED",
            message="Failed to connect to Gemini authentication endpoint.",
        )
    except Exception as exc:
        logger.warning(f"Unexpected error validating Gemini key: {type(exc).__name__}")
        return ValidationResult(
            outcome=ValidationOutcome.VALIDATION_ERROR,
            safe_code="PROBE_ERROR",
            message="Internal error occurred while testing Gemini credential.",
        )


async def probe_openai_compatible(
    url: str,
    raw_key: str,
    provider_name: str,
    auth_header_format: str = "Bearer {key}",
) -> ValidationResult:
    """Validate OpenAI-compatible provider (Groq, Cerebras, OpenAI, xAI) via GET /models."""
    headers = {"Authorization": auth_header_format.format(key=raw_key)}

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                return ValidationResult(
                    outcome=ValidationOutcome.VALID,
                    safe_code="AUTHENTICATED",
                    message=f"{provider_name} API credential authenticated successfully.",
                    billing_tier="unknown",
                    billing_error=f"{provider_name} does not expose non-generative account billing verification endpoint",
                )
            if resp.status_code == 401:
                return ValidationResult(
                    outcome=ValidationOutcome.INVALID_CREDENTIAL,
                    safe_code="INVALID_API_KEY",
                    message=f"The provided {provider_name} API key is invalid or rejected.",
                )
            if resp.status_code == 403:
                return ValidationResult(
                    outcome=ValidationOutcome.INSUFFICIENT_PERMISSION,
                    safe_code="FORBIDDEN",
                    message=f"Credential lacks permission to access {provider_name} models.",
                )
            if resp.status_code == 429:
                return ValidationResult(
                    outcome=ValidationOutcome.RATE_LIMITED,
                    safe_code="RATE_LIMITED",
                    message=f"{provider_name} returned rate limit during validation.",
                )
            if resp.status_code >= 500:
                return ValidationResult(
                    outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
                    safe_code="UPSTREAM_5XX",
                    message=f"{provider_name} service is currently unavailable or returning errors.",
                )

            return ValidationResult(
                outcome=ValidationOutcome.VALIDATION_ERROR,
                safe_code=f"HTTP_{resp.status_code}",
                message=f"Unexpected status {resp.status_code} from {provider_name} check.",
            )
    except httpx.TimeoutException:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="TIMEOUT",
            message=f"Connection to {provider_name} service timed out.",
        )
    except httpx.ConnectError:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="CONNECTION_FAILED",
            message=f"Failed to connect to {provider_name} endpoint.",
        )
    except Exception as exc:
        logger.warning(f"Unexpected error validating {provider_name} key: {type(exc).__name__}")
        return ValidationResult(
            outcome=ValidationOutcome.VALIDATION_ERROR,
            safe_code="PROBE_ERROR",
            message=f"Internal error occurred while testing {provider_name} credential.",
        )


async def probe_openrouter(raw_key: str) -> ValidationResult:
    """Validate OpenRouter API key via official GET /api/v1/key inspection endpoint."""
    url = "https://openrouter.ai/api/v1/key"
    headers = {"Authorization": f"Bearer {raw_key}"}

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                billing_tier = "unknown"
                billing_error = None
                try:
                    data = resp.json().get("data") or {}
                    is_free = data.get("is_free_tier")
                    if is_free is True:
                        billing_tier = "verified_free_only"
                    elif is_free is False:
                        billing_tier = "paid"
                    else:
                        billing_tier = "unknown"
                        billing_error = "OpenRouter response did not include is_free_tier flag"
                except Exception as exc:
                    billing_tier = "unknown"
                    billing_error = f"Failed to parse OpenRouter key inspection response: {exc}"

                return ValidationResult(
                    outcome=ValidationOutcome.VALID,
                    safe_code="AUTHENTICATED",
                    message="OpenRouter API key authenticated successfully.",
                    billing_tier=billing_tier,
                    billing_error=billing_error,
                )
            if resp.status_code == 401:
                return ValidationResult(
                    outcome=ValidationOutcome.INVALID_CREDENTIAL,
                    safe_code="INVALID_API_KEY",
                    message="The provided OpenRouter API key is invalid or rejected.",
                )
            if resp.status_code == 403:
                return ValidationResult(
                    outcome=ValidationOutcome.INSUFFICIENT_PERMISSION,
                    safe_code="FORBIDDEN",
                    message="OpenRouter API key does not have permission to access resources.",
                )
            if resp.status_code == 429:
                return ValidationResult(
                    outcome=ValidationOutcome.RATE_LIMITED,
                    safe_code="RATE_LIMITED",
                    message="OpenRouter returned rate limit during validation.",
                )
            if resp.status_code >= 500:
                return ValidationResult(
                    outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
                    safe_code="UPSTREAM_5XX",
                    message="OpenRouter service is currently unavailable.",
                )

            return ValidationResult(
                outcome=ValidationOutcome.VALIDATION_ERROR,
                safe_code=f"HTTP_{resp.status_code}",
                message=f"Unexpected status {resp.status_code} from OpenRouter check.",
            )
    except httpx.TimeoutException:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="TIMEOUT",
            message="Connection to OpenRouter service timed out.",
        )
    except httpx.ConnectError:
        return ValidationResult(
            outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
            safe_code="CONNECTION_FAILED",
            message="Failed to connect to OpenRouter endpoint.",
        )
    except Exception as exc:
        logger.warning(f"Unexpected error validating OpenRouter key: {type(exc).__name__}")
        return ValidationResult(
            outcome=ValidationOutcome.VALIDATION_ERROR,
            safe_code="PROBE_ERROR",
            message="Internal error occurred while testing OpenRouter credential.",
        )


async def validate_provider_credential(provider_id: str, raw_key: str) -> ValidationResult:
    """Dispatch a safe, non-generative authentication probe according to provider_id.

    NEVER invokes chat completion or model generation.
    """
    cleaned_key = raw_key.strip()
    if not cleaned_key:
        return ValidationResult(
            outcome=ValidationOutcome.INVALID_CREDENTIAL,
            safe_code="EMPTY_KEY",
            message="Credential key cannot be empty.",
        )

    match provider_id.lower():
        case "gemini":
            return await probe_gemini(cleaned_key)
        case "groq":
            return await probe_openai_compatible(
                url="https://api.groq.com/openai/v1/models",
                raw_key=cleaned_key,
                provider_name="Groq",
            )
        case "openrouter":
            return await probe_openrouter(cleaned_key)
        case "cerebras":
            return await probe_openai_compatible(
                url="https://api.cerebras.ai/v1/models",
                raw_key=cleaned_key,
                provider_name="Cerebras",
            )
        case "openai":
            return await probe_openai_compatible(
                url="https://api.openai.com/v1/models",
                raw_key=cleaned_key,
                provider_name="OpenAI",
            )
        case "xai" | "grok":
            return await probe_openai_compatible(
                url="https://api.x.ai/v1/models",
                raw_key=cleaned_key,
                provider_name="xAI",
            )
        case _:
            return ValidationResult(
                outcome=ValidationOutcome.UNSUPPORTED,
                safe_code="UNSUPPORTED_PROVIDER",
                message=f"Automated credential validation is not supported for provider '{provider_id}'.",
            )
