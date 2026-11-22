"""Integration and execution tests for Unified Intelligent Inference Routing (M2.9)."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.errors import ConflictError
from app.core.redis import (
    close_community_redis,
    close_redis,
    get_community_redis,
    get_redis,
    init_community_redis,
    init_redis,
)
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
CREATED_TEST_NODES: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    await init_community_redis()
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
    cr_conn = get_community_redis()
    if cr_conn:
        try:
            comm_keys = await cr_conn.keys("ragbait:comm:*") + await cr_conn.keys("stream:node:*")
            if comm_keys:
                await cr_conn.delete(*comm_keys)
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
    cr_conn = get_community_redis()
    if cr_conn:
        try:
            comm_keys = await cr_conn.keys("ragbait:comm:*") + await cr_conn.keys("stream:node:*")
            if comm_keys:
                await cr_conn.delete(*comm_keys)
        except Exception:
            pass
    pool = get_pool()
    if CREATED_TEST_NODES:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM public.community_nodes WHERE id = ANY($1)", CREATED_TEST_NODES
                )
        except Exception:
            pass
        CREATED_TEST_NODES.clear()
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
    await close_community_redis()


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

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
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

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
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

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.generate",
        AsyncMock(return_value=mock_gen_result),
    ):
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

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
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

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
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
        if admission_req.provider_id != "groq":
            # Non-Groq providers are in candidate-specific cooldown
            raise AdmissionRejectionError(
                reason=f"{admission_req.provider_id} provider is temporarily cooling down",
                category="provider_cooldown",
            )
        # Next eligible provider (Groq) succeeds
        from app.modules.admission.service import reserve_generation as real_reserve

        return await real_reserve(admission_req)

    with patch("app.modules.routing.executor.reserve_generation", side_effect=mock_reserve):
        with patch(
            "app.modules.llm_gateway.providers.groq.GroqAdapter.stream",
            side_effect=_mock_stream_generator,
        ):
            result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

            assert is_streaming is True
            # Verified that it fell back to groq
            assert decision.provider_id == "groq"
            assert call_count >= 2


@pytest.mark.asyncio
async def test_route_and_dispatch_community_candidate():
    """Verify end-to-end community candidate execution through unified router."""
    import json

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from app.core.config import settings
    from app.modules.community import crypto as comm_crypto
    from app.modules.community import repository as comm_repo

    requester_id = await create_db_user(email="comm_requester@studyhub.edu")
    contributor_id = await create_db_user(email="comm_contributor@studyhub.edu")

    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_hex = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    session_id = str(uuid4())

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.community_nodes (
                owner_id, public_key, display_name, status, sharing_enabled,
                max_concurrent_jobs, max_execution_duration, last_seen_at
            )
            VALUES ($1, $2, 'Station 1', 'active', true, 2, 120, now())
            RETURNING id
            """,
            contributor_id,
            node_pub_hex,
        )
        assert row is not None
        node_id = row["id"]
        CREATED_TEST_NODES.append(node_id)

    # Register active session and sync llama3.2:3b enabled
    await comm_repo.register_node_session(node_id, session_id)
    await comm_repo.sync_node_models(node_id, [{"model_name": "llama3.2:3b"}])
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.community_node_models SET enabled = true WHERE node_id = $1 AND model_name = $2",
            node_id,
            "llama3.2:3b",
        )

    # Build completion request
    prompt_text = "What is active learning?"
    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content=prompt_text)],
        routing_mode="community_only",
        model="llama3.2:3b",
        community_opt_in=True,
        community_consent_version=settings.COMMUNITY_CONSENT_POLICY_VERSION,
        stream=True,
        idempotency_key=f"idem-comm-{uuid4().hex[:8]}",
    )

    # Execute through unified routing
    result, is_streaming, decision = await route_and_dispatch(user_id=requester_id, req=req)

    assert is_streaming is True
    assert decision.selected_source == "community"
    assert decision.provider_id == "ollama"
    assert decision.model_id == "llama3.2:3b"
    assert decision.node_id == node_id

    # 1. Verify PostgreSQL job state
    async with pool.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT * FROM public.community_inference_jobs WHERE requester_id = $1 AND node_id = $2",
            requester_id,
            node_id,
        )
        assert job_row is not None
        assert job_row["status"] == "QUEUED"
        assert job_row["model_name"] == "llama3.2:3b"
        job_id = job_row["id"]

    # 2. Verify Redis Stream dispatch message
    cr = get_community_redis()
    assert cr is not None
    stream_key = f"stream:node:{node_id}:jobs"
    messages = await cr.xrange(stream_key)
    assert len(messages) >= 1
    msg_id, msg_data = messages[-1]
    assert msg_data["job_id"] == str(job_id)

    # 3. Verify signed authorization payload & signature validity
    auth_data = json.loads(msg_data["authorization"])
    assert auth_data["job_id"] == str(job_id)
    assert auth_data["node_id"] == str(node_id)
    assert auth_data["model_name"] == "llama3.2:3b"
    assert len(auth_data["auth_nonce"]) == 32  # 16 bytes = 32 hex chars (128-bit)
    assert len(auth_data["signature"]) == 128  # Ed25519 signature in hex

    is_sig_valid = comm_crypto.verify_job_authorization(
        signature_hex=auth_data["signature"],
        job_id=job_id,
        node_id=node_id,
        model_name="llama3.2:3b",
        payload_digest=auth_data["payload_digest"],
        max_output_tokens=auth_data["max_output_tokens"],
        hard_deadline_ts=auth_data["hard_deadline_ts"],
        auth_nonce=auth_data["auth_nonce"],
    )
    assert is_sig_valid is True

    # 4. Verify ZSET concurrency reservation acquired
    zset_key = f"ragbait:comm:node:{node_id}:reservations"
    score = await cr.zscore(zset_key, str(job_id))
    assert score is not None

    # 5. Verify generation_started event can be yielded without error
    first_chunk = await anext(result)
    assert "generation_started" in first_chunk
    assert str(job_id) in first_chunk


def test_chat_message_role_and_content_sanitization():
    """Verify ChatMessage trims whitespace and normalizes role casing."""
    msg = ChatMessage(role="USER ", content="  Hello world!  ")  # type: ignore[arg-type]
    assert msg.role == "user"
    assert msg.content == "Hello world!"

    with pytest.raises(ValueError):
        ChatMessage(role="user", content="   ")


def test_chat_completion_request_reconciles_empty_fields():
    """Verify ChatCompletionRequest normalizes empty models and prunes empty messages."""
    req = ChatCompletionRequest(
        routing_mode="auto",
        model="",
        model_id="",
        messages=[
            {"role": "user", "content": "  Valid prompt  "},
            {"role": "assistant", "content": "   "},
            {"role": "assistant", "content": ""},
        ],  # type: ignore[arg-type]
    )
    assert req.model is None
    assert req.model_id is None
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert req.messages[0].content == "Valid prompt"
    assert req.idempotency_key.startswith("req-")

