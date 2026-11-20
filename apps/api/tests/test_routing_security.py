"""Security, privacy boundaries, and access control tests for Intelligent Routing (M2.9)."""

from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.errors import AuthorizationError, BadRequestError, ConflictError, NotFoundError
from app.core.redis import close_redis, get_redis, init_redis
from app.main import app
from app.modules.credentials.repository import store_credential
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.llm_gateway.interfaces import NormalizedStreamChunk, NormalizedUsage
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatMessage
from app.modules.routing import preview_route, route_and_dispatch

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


async def _mock_stream_generator(*args, **kwargs) -> AsyncIterator[NormalizedStreamChunk]:
    yield NormalizedStreamChunk(delta="Hello ")
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
async def test_cloud_only_never_selects_community_node():
    """In cloud_only mode, community nodes must NEVER be selected or discovered."""
    user_id = await create_db_user(email="sec_cloud_only@studyhub.edu")

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Classified academic query")],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=f"idem-sec-{uuid4().hex[:8]}",
    )

    preview = await preview_route(user_id=user_id, req=req)
    if preview.selected_candidate:
        assert preview.selected_candidate.source_type == "cloud"
        assert preview.selected_candidate.source_type != "community"

    # Also verify direct candidate discovery excludes community sources
    from app.modules.routing.discovery import discover_all_candidates

    candidates = await discover_all_candidates(
        user_id=user_id,
        routing_mode="cloud_only",
        community_opt_in=False,
    )
    for c in candidates:
        assert c.source_type == "cloud"
        assert c.source_type != "community"


@pytest.mark.asyncio
async def test_auto_without_community_opt_in_never_selects_community():
    """In auto mode, community nodes must NEVER be evaluated or selected."""
    user_id = await create_db_user(email="sec_auto_no_optin@studyhub.edu")

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Standard query")],
        routing_mode="auto",
        stream=True,
        idempotency_key=f"idem-sec2-{uuid4().hex[:8]}",
    )

    preview = await preview_route(user_id=user_id, req=req)
    # Selected candidate must not be community
    if preview.selected_candidate:
        assert preview.selected_candidate.source_type == "cloud"


@pytest.mark.asyncio
async def test_community_only_mode_discontinued():
    """Routing mode community_only must raise BadRequestError immediately with discontinued notice."""
    user_id = await create_db_user(email="sec_comm_discontinued@studyhub.edu")

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Peer query")],
        routing_mode="community_only",
        stream=True,
        idempotency_key=f"idem-sec3-{uuid4().hex[:8]}",
    )

    with pytest.raises(BadRequestError) as exc_info:
        await route_and_dispatch(user_id=user_id, req=req)

    assert "Community local inference has been discontinued" in str(exc_info.value)
    assert "strictly private and never shared" in str(exc_info.value)


@pytest.mark.asyncio
async def test_community_opt_in_strictly_rejected():
    """Requests with community_opt_in=True must raise BadRequestError immediately."""
    user_id = await create_db_user(email="sec_comm_optin_rej@studyhub.edu")

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Peer query")],
        routing_mode="auto",
        community_opt_in=True,
        stream=True,
        idempotency_key=f"idem-sec4-{uuid4().hex[:8]}",
    )

    with pytest.raises(BadRequestError) as exc_info:
        await route_and_dispatch(user_id=user_id, req=req)

    assert "Local model sharing is disabled" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_ollama_rejected_as_server_cloud_provider():
    """Requesting provider_id='ollama' must raise BadRequestError explaining it runs in browser."""
    user_id = await create_db_user(email="sec_ollama_rej@studyhub.edu")

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Local query")],
        routing_mode="cloud_only",
        provider_id="ollama",
        stream=True,
        idempotency_key=f"idem-sec5-{uuid4().hex[:8]}",
    )

    with pytest.raises(BadRequestError) as exc_info:
        await route_and_dispatch(user_id=user_id, req=req)

    assert "Provider 'ollama' is not supported as a server-side cloud provider" in str(exc_info.value)
    assert "browser" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_unauthorized_credential_access_prevented():
    """User B cannot access or route through User A's private owner_only credential."""
    user_a = await create_db_user(email="owner_a@studyhub.edu")
    user_b = await create_db_user(email="attacker_b@studyhub.edu")

    cred_a = await store_credential(
        owner_id=user_a,
        provider_id="gemini",
        raw_key=f"private-key-a-{uuid4().hex}",
        nickname="Private Key A",
        sharing_scope="owner_only",
        shared_consent=False,
        status="active",
    )
    CREATED_TEST_CREDS.append(cred_a.id)

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET validation_status = 'valid' WHERE id = $1",
            cred_a.id,
        )

    # User B tries to route using User A's explicit credential_id
    req_b = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Sneaky prompt")],
        routing_mode="cloud_only",
        credential_id=cred_a.id,
        stream=True,
        idempotency_key=f"idem-sec5-{uuid4().hex[:8]}",
    )

    # Must be rejected because User B does not own cred_a and it is owner_only
    with pytest.raises((NotFoundError, AuthorizationError, ConflictError)):
        await route_and_dispatch(user_id=user_b, req=req_b)


@pytest.mark.asyncio
async def test_telemetry_safe_headers_no_prompt_leak():
    """Route decision and preview telemetry must NEVER contain prompt text, completion text, or API keys."""
    user_id = await create_db_user(email="telemetry_test@studyhub.edu")

    secret_raw_key = f"super-secret-key-{uuid4().hex}"
    stored_cred = await store_credential(
        owner_id=user_id,
        provider_id="gemini",
        raw_key=secret_raw_key,
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

    confidential_prompt = "TOP_SECRET_PROMPT_TEXT_CONFIDENTIAL"
    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content=confidential_prompt)],
        routing_mode="cloud_only",
        stream=True,
        idempotency_key=f"idem-sec6-{uuid4().hex[:8]}",
    )

    with patch(
        "app.modules.llm_gateway.providers.gemini.GeminiAdapter.stream",
        side_effect=_mock_stream_generator,
    ):
        result, is_streaming, decision = await route_and_dispatch(user_id=user_id, req=req)

        # Inspect route decision metadata
        decision_str = str(decision.model_dump())
        assert confidential_prompt not in decision_str
        assert secret_raw_key not in decision_str

        # Inspect preview response
        preview = await preview_route(user_id=user_id, req=req)
        preview_str = str(preview.model_dump())
        assert confidential_prompt not in preview_str
        assert secret_raw_key not in preview_str
