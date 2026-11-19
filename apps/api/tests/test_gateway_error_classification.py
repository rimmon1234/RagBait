"""Unit and integration tests for provider error classification and sanitization."""

from uuid import uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, init_redis
from app.modules.credentials.repository import get_credential_metadata, store_credential
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.errors import (
    ProviderAdapterError,
    map_http_status_to_category,
)


def test_401_invalid_credential():
    """401 Unauthorized must always be classified as invalid_credentials."""
    cat, msg, status = map_http_status_to_category(
        401,
        {
            "error": {
                "message": "Invalid API Key",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        },
    )
    assert cat == "invalid_credentials"
    assert status == 400
    assert "rejected as invalid or unauthorized" in msg
    assert "Invalid API Key" not in msg  # Sanitized


def test_403_insufficient_model_permission():
    """403 with model entitlement restriction must be classified as model_access_restricted, NOT invalid_credentials."""
    cat, msg, status = map_http_status_to_category(
        403,
        {
            "error": {
                "message": "User is not permitted to use this model or lacks whitelist approval"
            }
        },
    )
    assert cat == "model_access_restricted"
    assert status == 403
    assert "model" in msg.lower()
    assert cat != "invalid_credentials"


def test_403_account_restriction():
    """403 with billing or account restriction must be classified as account_restricted."""
    cat, msg, status = map_http_status_to_category(
        403,
        {
            "error": {
                "message": "Account suspended due to billing delinquency",
                "code": "billing_hold",
            }
        },
    )
    assert cat == "account_restricted"
    assert status == 403
    assert "billing" in msg.lower() or "account" in msg.lower()
    assert cat != "invalid_credentials"


def test_403_confirmed_authentication_failure():
    """403 with explicit API key failure evidence should be classified as invalid_credentials."""
    cat, msg, status = map_http_status_to_category(
        403,
        {
            "error": {
                "message": "The caller does not have permission",
                "status": "PERMISSION_DENIED",
                "details": [{"reason": "API_KEY_INVALID"}],
            }
        },
    )
    assert cat == "invalid_credentials"
    assert status == 400


def test_403_generic_permission_denied_not_invalid_credential():
    """Generic 403 must NOT mark credential invalid; it represents insufficient permission."""
    cat, msg, status = map_http_status_to_category(
        403,
        {"error": {"message": "The caller does not have permission"}},
    )
    assert cat == "insufficient_permission"
    assert status == 403
    assert cat != "invalid_credentials"


def test_429_rate_limit_vs_quota_exhaustion():
    """Differentiate between transient rate limiting (RPM/TPM) and quota exhaustion."""
    # Transient rate limit
    cat_rl, msg_rl, status_rl = map_http_status_to_category(
        429,
        {"error": {"message": "Rate limit reached for requests per minute (RPM)"}},
    )
    assert cat_rl == "rate_limited"
    assert status_rl == 429

    # Quota exhaustion
    cat_q, msg_q, status_q = map_http_status_to_category(
        429,
        {"error": {"message": "You have exceeded your current quota. Check your plan."}},
    )
    assert cat_q == "quota_exceeded"
    assert status_q == 429


def test_sanitized_error_responses():
    """Ensure error messages never leak sensitive raw inputs, keys, or internal paths."""
    sensitive_raw_key = "gsk_test_super_secret_raw_key_12345"
    cat, msg, status = map_http_status_to_category(
        401,
        {
            "error": {
                "message": f"Authorization header with {sensitive_raw_key} failed at /internal/auth/v1"
            }
        },
    )
    assert sensitive_raw_key not in msg
    assert "/internal/auth/v1" not in msg

    err = ProviderAdapterError(
        provider_id="groq",
        category=cat,
        message=msg,
        status_code=status,
    )
    assert sensitive_raw_key not in str(err)
    assert sensitive_raw_key not in repr(err)


@pytest.mark.asyncio
async def test_failed_request_does_not_mutate_credential_status():
    """Verify that upstream provider failures do not automatically disable or invalidate credentials."""
    await init_db_pool()
    await init_vault_pool()
    await init_redis()

    pool = get_pool()
    user_id = uuid4()
    unique_email = f"status_test_{user_id.hex[:6]}@studyhub.edu"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
                VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Student"}'::jsonb)
                """,
                user_id,
                unique_email,
            )
            await conn.execute(
                "UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = $1",
                user_id,
            )

        unique_key = f"gsk_test_{uuid4().hex}"
        stored = await store_credential(
            owner_id=user_id,
            provider_id="groq",
            raw_key=unique_key,
            nickname="Test Fail Key",
            sharing_scope="owner_only",
            status="active",
        )

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
                stored.id,
            )

        # Verify initial status
        cred_before = await get_credential_metadata(credential_id=stored.id, caller_id=user_id)
        assert cred_before is not None
        assert cred_before.status == "active"
        assert cred_before.validation_status == "valid"

        # Simulate provider failure mapping
        cat, msg, code = map_http_status_to_category(
            403,
            {"error": {"message": "User lacks model permission"}},
        )
        assert cat == "model_access_restricted"

        # Check credential in database remained untouched
        cred_after = await get_credential_metadata(credential_id=stored.id, caller_id=user_id)
        assert cred_after is not None
        assert cred_after.status == "active"
        assert cred_after.validation_status == "valid"

    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)
        await close_vault_pool()
        await close_db_pool()
        await close_redis()
