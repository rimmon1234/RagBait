"""Integration tests for unified inference gateway lifecycle (streaming and non-streaming)."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.credentials.repository import store_credential
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.interfaces import (
    NormalizedGenerationResult,
    NormalizedStreamChunk,
    NormalizedUsage,
)
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_CREDS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()
    pool = get_pool()
    if CREATED_TEST_CREDS:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM public.api_key_metadata WHERE id = ANY($1)", CREATED_TEST_CREDS
                )
        except Exception:
            pass
        CREATED_TEST_CREDS.clear()
    if CREATED_TEST_USERS:
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM auth.users WHERE id = ANY($1)", CREATED_TEST_USERS)
        except Exception:
            pass
        CREATED_TEST_USERS.clear()
    await close_vault_pool()
    await close_db_pool()
    await close_redis()


async def create_db_user(
    email: str | None = None,
    role: str = "user",
    status: str = "approved",
    with_entitlement: bool = True,
) -> UUID:
    pool = get_pool()
    user_id = uuid4()
    if not email:
        unique_email = f"student_{user_id.hex[:8]}@studyhub.edu"
    else:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name": "Student"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            unique_email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = $1, approval_status = $2
            WHERE id = $3
            """,
            role,
            status,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)

    if with_entitlement:
        cred = await store_credential(
            owner_id=user_id,
            provider_id="groq",
            raw_key=f"gsk-qualifying-{uuid4().hex}",
            nickname="Contributed Shared Key",
            sharing_scope="community_shared",
            shared_consent=True,
            status="active",
        )
        CREATED_TEST_CREDS.append(cred.id)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
                cred.id,
            )

    return user_id


def setup_auth_context(
    user_id: UUID,
    email: str = "student@studyhub.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> dict[str, str]:
    profile = make_test_profile(user_id=user_id, email=email, role=role, approval_status=status)
    token = create_test_jwt(user_id=user_id, email=email)

    app.dependency_overrides[get_current_profile] = lambda: profile
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    return {"Authorization": f"Bearer {token}"}


async def _mock_stream_generator() -> AsyncIterator[NormalizedStreamChunk]:
    yield NormalizedStreamChunk(delta="Hello ")
    yield NormalizedStreamChunk(delta="Student!")
    yield NormalizedStreamChunk(
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=15,
            output_tokens=6,
            total_tokens=21,
            usage_source="authoritative",
        ),
        resolved_model="gemini-3.8-flash",
    )


@pytest.mark.asyncio
async def test_unified_gateway_streaming_e2e(client: httpx.AsyncClient):
    user_id = await create_db_user(email="stream_user@studyhub.edu")
    headers = setup_auth_context(user_id, email="stream_user@studyhub.edu")

    # Store a valid credential in the vault
    unique_key = f"valid-test-gemini-key-{uuid4().hex}"
    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=unique_key,
        nickname="My Gemini Key",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored_cred.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
            stored_cred.id,
        )

    idempotency_key = f"stream_test_{uuid4().hex[:12]}"
    payload = {
        "provider_id": "gemini",
        "model_id": "gemini-3.8-flash",
        "credential_id": str(stored_cred.id),
        "messages": [
            {"role": "system", "content": "You are a tutor."},
            {"role": "user", "content": "Explain gravity."},
        ],
        "params": {
            "temperature": 0.5,
            "max_output_tokens": 512,
        },
        "stream": True,
        "idempotency_key": idempotency_key,
    }

    from unittest.mock import MagicMock

    mock_adapter = MagicMock()
    mock_adapter.stream = lambda *args, **kwargs: _mock_stream_generator()

    with patch(
        "app.modules.llm_gateway.service._get_provider_adapter",
        return_value=mock_adapter,
    ):
        resp = await client.post("/api/v1/llm/chat/completions", json=payload, headers=headers)

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    body_text = resp.text
    assert "event: generation_started" in body_text
    assert "event: text_delta" in body_text
    assert "Hello " in body_text
    assert "Student!" in body_text
    assert "event: usage" in body_text
    assert "event: generation_completed" in body_text

    # Verify database reservation was committed and settled
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, actual_input_tokens, actual_output_tokens,
                   resolved_model_id, upstream_provider, settled_at
            FROM public.llm_usage_reservations
            WHERE user_id = $1 AND idempotency_key = $2
            """,
            user_id,
            idempotency_key,
        )
        assert row is not None
        assert row["status"] == "settled"
        assert row["actual_input_tokens"] == 15
        assert row["actual_output_tokens"] == 6
        assert row["resolved_model_id"] == "gemini-3.8-flash"
        assert row["upstream_provider"] == "gemini"
        assert row["settled_at"] is not None


@pytest.mark.asyncio
async def test_unified_gateway_non_streaming_e2e(client: httpx.AsyncClient):
    user_id = await create_db_user(email="nonstream_user@studyhub.edu")
    headers = setup_auth_context(user_id, email="nonstream_user@studyhub.edu")

    unique_key = f"valid-test-gemini-key-{uuid4().hex}"
    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=unique_key,
        nickname="My Gemini Key 2",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(stored_cred.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
            stored_cred.id,
        )

    idempotency_key = f"nonstream_test_{uuid4().hex[:12]}"
    payload = {
        "provider_id": "gemini",
        "model_id": "gemini-3.8-flash",
        "credential_id": str(stored_cred.id),
        "messages": [
            {"role": "user", "content": "Explain mitosis."},
        ],
        "params": {
            "temperature": 0.7,
            "max_output_tokens": 256,
        },
        "stream": False,
        "idempotency_key": idempotency_key,
    }

    mock_adapter = AsyncMock()
    mock_adapter.generate.return_value = NormalizedGenerationResult(
        content="Mitosis is cellular division resulting in two identical daughter cells.",
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=18,
            output_tokens=14,
            total_tokens=32,
            usage_source="authoritative",
        ),
        resolved_model="gemini-3.8-flash",
        upstream_generation_id="gen-gemini-999",
    )

    with patch(
        "app.modules.llm_gateway.service._get_provider_adapter",
        return_value=mock_adapter,
    ):
        resp = await client.post("/api/v1/llm/chat/completions", json=payload, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert (
        data["content"] == "Mitosis is cellular division resulting in two identical daughter cells."
    )
    assert data["finish_reason"] == "stop"
    assert data["resolved_model_id"] == "gemini-3.8-flash"
    assert data["upstream_generation_id"] == "gen-gemini-999"
    assert data["usage"]["total_tokens"] == 32
