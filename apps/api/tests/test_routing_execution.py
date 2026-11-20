"""Integration and execution tests for Unified Intelligent Inference Routing (M2.9)."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.errors import ConflictError
from app.core.redis import close_redis, get_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.admission.errors import AdmissionRejectionError
from app.modules.credentials.repository import store_credential
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.interfaces import (
    NormalizedGenerationResult,
    NormalizedStreamChunk,
    NormalizedUsage,
)
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatMessage
from app.modules.routing import route_and_dispatch
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_CREDS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    redis_conn = get_redis()
    if redis_conn:
        try:
            rate_keys = await redis_conn.keys("ragbait:rate:*")
            conc_keys = await redis_conn.keys("ragbait:concurrency:*")
            all_keys = (rate_keys or []) + (conc_keys or [])
            if all_keys:
                await redis_conn.delete(*all_keys)
        except Exception:
            pass
    yield
    app.dependency_overrides.clear()
    redis_conn = get_redis()
    if redis_conn:
        try:
            rate_keys = await redis_conn.keys("ragbait:rate:*")
            conc_keys = await redis_conn.keys("ragbait:concurrency:*")
            all_keys = (rate_keys or []) + (conc_keys or [])
            if all_keys:
                await redis_conn.delete(*all_keys)
        except Exception:
            pass
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
    email: str | None = None, role: str = "user", status: str = "approved"
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


async def _mock_stream_generator(*args, **kwargs) -> AsyncIterator[NormalizedStreamChunk]:
    yield NormalizedStreamChunk(delta="Hello ")
    yield NormalizedStreamChunk(delta="from router!")
    yield NormalizedStreamChunk(
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            usage_source="authoritative",
        ),
        resolved_model="gemini-3.8-flash",
    )


@pytest.mark.asyncio
async def test_route_and_dispatch_auto_mode_streaming():
    """In auto mode, router automatically selects optimal eligible candidate and streams SSE chunks."""
    user_id = await create_db_user(email="router_auto@studyhub.edu")

    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=f"valid-test-gemini-key-{uuid4().hex}",
        nickname="My Auto Key",
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

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello auto router")],
        routing_mode="auto",
        stream=True,
        idempotency_key=f"idem-auto-{uuid4().hex[:8]}",
    )

    with patch("app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream", side_effect=_mock_stream_generator):
        result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

        assert is_streaming is True
        assert decision.routing_mode == "auto"
        assert decision.selected_source == "cloud"
        assert decision.provider_id == "gemini"
        assert decision.model_id == "gemini-3.8-flash"

        events = []
        async for chunk in result:
            events.append(chunk)

        full_output = "".join(events)
        assert "generation_started" in full_output
        assert "text_delta" in full_output
        assert "generation_completed" in full_output


@pytest.mark.asyncio
async def test_server_controlled_default_model_when_omitted():
    """Omitting the model parameter must select server-controlled DEFAULT_CLOUD_MODEL."""
    user_id = await create_db_user(email="router_default@studyhub.edu")

    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=f"valid-test-gemini-key-{uuid4().hex}",
        nickname="My Default Key",
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

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello default")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=f"idem-def-{uuid4().hex[:8]}",
    )
    assert req.model_id is None

    with patch("app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream", side_effect=_mock_stream_generator):
        result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

        assert decision.model_id == "gemini-3.8-flash"
        assert decision.provider_id == "gemini"


@pytest.mark.asyncio
async def test_route_and_dispatch_non_streaming():
    """Non-streaming request returns ChatCompletionResponse with full content."""
    user_id = await create_db_user(email="router_nonstream@studyhub.edu")

    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=f"valid-test-gemini-key-{uuid4().hex}",
        nickname="My NonStream Key",
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

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello non-streaming")],
        routing_mode="cloud_only",
        stream=False,
        idempotency_key=f"idem-nonstream-{uuid4().hex[:8]}",
    )

    mock_gen_result = NormalizedGenerationResult(
        content="Non-streaming response from Gemini",
        finish_reason="stop",
        usage=NormalizedUsage(
            input_tokens=12,
            output_tokens=6,
            total_tokens=18,
            usage_source="authoritative",
        ),
        resolved_model="gemini-3.8-flash",
    )

    with patch("app.modules.llm_gateway.providers.gemini.GeminiAdapter.generate", AsyncMock(return_value=mock_gen_result)):
        result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

        assert is_streaming is False
        assert result.content == "Non-streaming response from Gemini"
        assert result.model_id == "gemini-3.8-flash"
        assert result.usage["total_tokens"] == 18


@pytest.mark.asyncio
async def test_idempotency_key_payload_hash_conflict():
    """Reusing an idempotency key with a different request payload raises 409 Conflict."""
    user_id = await create_db_user(email="router_idem@studyhub.edu")

    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=f"valid-test-gemini-key-{uuid4().hex}",
        nickname="My Idem Key",
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

    shared_key = f"idem-shared-{uuid4().hex[:8]}"

    req_1 = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Prompt One")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=shared_key,
    )

    with patch("app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream", side_effect=_mock_stream_generator):
        result, _, _ = await route_and_dispatch(user_id=user_id, req=req_1)
        # Drain stream so reservation commits
        async for _ in result:
            pass

    # Request 2 with same idempotency key but DIFFERENT payload content
    req_2 = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="DIFFERENT Prompt Two")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=shared_key,
    )

    with pytest.raises(ConflictError) as exc_info:
        await route_and_dispatch(user_id=user_id, req=req_2)

    assert "previously used with different request parameters" in str(exc_info.value)


@pytest.mark.asyncio
async def test_idempotency_cross_user_isolation():
    """Idempotency keys are strictly scoped per user; User B using User A's key does not collide."""
    user_a = await create_db_user(email="user_a_idem@studyhub.edu")
    user_b = await create_db_user(email="user_b_idem@studyhub.edu")

    for u in (user_a, user_b):
        stored_cred = await store_credential(
            owner_id=u,
            provider_id="gemini",
            raw_key=f"valid-test-gemini-key-{uuid4().hex}",
            nickname="Key",
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

    same_key = f"same-key-{uuid4().hex[:8]}"

    req_a = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="User A Prompt")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=same_key,
    )
    req_b = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="User B Prompt")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=same_key,
    )

    with patch("app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream", side_effect=_mock_stream_generator):
        res_a, _, _ = await route_and_dispatch(user_id=user_a, req=req_a)
        async for _ in res_a:
            pass

        # User B using the same key must NOT raise ConflictError because idempotency is scoped to user_id
        res_b, _, _ = await route_and_dispatch(user_id=user_b, req=req_b)
        async for _ in res_b:
            pass


@pytest.mark.asyncio
async def test_pre_dispatch_fallback_on_candidate_specific_error():
    """If Candidate #1 fails a pre-dispatch check (e.g. cooldown), router safely falls back to Candidate #2."""
    user_id = await create_db_user(email="router_fallback@studyhub.edu")

    # Add credentials for both gemini and groq
    cred_gemini = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=f"gemini-key-{uuid4().hex}",
        nickname="Gemini",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    cred_groq = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        raw_key=f"groq-key-{uuid4().hex}",
        nickname="Groq",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.extend([cred_gemini.id, cred_groq.id])

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = ANY($1)",
            [cred_gemini.id, cred_groq.id],
        )

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello fallback")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=f"idem-fb-{uuid4().hex[:8]}",
    )

    call_count = 0

    async def mock_reserve(admission_req):
        nonlocal call_count
        call_count += 1
        if admission_req.provider_id == "gemini":
            # Gemini provider is in candidate-specific cooldown
            raise AdmissionRejectionError(
                reason="Gemini provider is temporarily cooling down",
                category="provider_cooldown",
            )
        # Next eligible provider (Groq) succeeds
        from app.modules.admission.service import reserve_generation as real_reserve
        return await real_reserve(admission_req)

    with patch("app.modules.routing.executor.reserve_generation", side_effect=mock_reserve):
        with patch("app.modules.llm_gateway.providers.groq.GroqAdapter.stream", side_effect=_mock_stream_generator):
            result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

            assert is_streaming is True
            # Verified that it fell back to groq
            assert decision.provider_id == "groq"
            assert call_count >= 2
