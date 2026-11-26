"""Comprehensive Test Suite for Workstream A: OpenRouter Free-Tier Enforcement & Routing Correction."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.credentials.validator import (
    ValidationOutcome,
    probe_openrouter,
)
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatMessage
from app.modules.routing.discovery import discover_cloud_candidates
from app.modules.routing.free_models import is_approved_free_model
from app.modules.routing.policy import filter_candidates
from app.modules.routing.schemas import RoutingCandidate
from app.modules.routing.service import route_and_dispatch
from tests.conftest import create_test_jwt

CREATED_TEST_USERS: list[UUID] = []


async def _cleanup_test_data() -> None:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.api_key_metadata SET status = 'revoked', shared_consent = false WHERE nickname LIKE '%OpenRouter Test Key%'"
            )
    except Exception:
        pass
    CREATED_TEST_USERS.clear()


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_redis()
    await _cleanup_test_data()
    yield
    app.dependency_overrides.clear()
    await _cleanup_test_data()


async def create_test_user(email_prefix: str = "openrouter_tester") -> tuple[UUID, str]:
    """Provision a verified test user."""
    user_id = uuid4()
    email = f"{email_prefix}_{user_id.hex[:6]}@test.internal"
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name":"Test User"}'::jsonb);
            """,
            user_id,
            email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = 'user', approval_status = 'approved'
            WHERE id = $1;
            """,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    token = create_test_jwt(user_id=user_id, email=email)
    return user_id, token


async def insert_test_credential(
    user_id: UUID,
    provider_id: str = "openrouter",
    status: str = "active",
    validation_status: str = "valid",
    sharing_scope: str = "community_shared",
    shared_consent: bool = True,
    billing_tier: str = "verified_free_only",
    age_days: int = 0,
) -> UUID:
    """Insert a credential metadata row directly for testing."""
    cred_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                sharing_consent_timestamp, consent_version,
                billing_tier, billing_verified_at, billing_verification_source
            )
            VALUES (
                $1, $2, $3, 'OpenRouter Test Key', 'sk-or-...test', $4,
                $5, $6, $7, $8, now(), 'v1',
                $9, now() - interval '{age_days} days', 'openrouter_probe'
            )
            """,
            cred_id,
            user_id,
            provider_id,
            uuid4().hex[:16],
            status,
            validation_status,
            sharing_scope,
            shared_consent,
            billing_tier,
        )
    return cred_id


# ==============================================================================
# 1. Endpoint & Billing Tier Probe Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_openrouter_endpoint_uses_api_v1_key():
    """Verify OpenRouter key probe targets official GET /api/v1/key, NOT /auth/key."""
    recorded_urls: list[str] = []

    def handler(request: httpx.Request):
        recorded_urls.append(str(request.url))
        return httpx.Response(200, json={"data": {"is_free_tier": True, "label": "Test"}})

    transport = httpx.MockTransport(handler)
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
        res = await probe_openrouter("sk-or-test-key-1234")

    assert res.outcome == ValidationOutcome.VALID
    assert res.safe_code == "AUTHENTICATED"
    assert res.billing_tier == "verified_free_only"
    assert res.billing_error is None
    assert len(recorded_urls) == 1
    assert recorded_urls[0] == "https://openrouter.ai/api/v1/key"
    assert "auth/key" not in recorded_urls[0]


@pytest.mark.asyncio
async def test_openrouter_is_free_tier_true():
    """is_free_tier = True sets billing_tier = 'verified_free_only'."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"is_free_tier": True}})
    )
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
        res = await probe_openrouter("sk-or-valid-free")

    assert res.outcome == ValidationOutcome.VALID
    assert res.billing_tier == "verified_free_only"
    assert res.billing_error is None


@pytest.mark.asyncio
async def test_openrouter_is_free_tier_false():
    """is_free_tier = False sets billing_tier = 'paid'."""
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"is_free_tier": False}})
    )
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
        res = await probe_openrouter("sk-or-paid-key")

    assert res.outcome == ValidationOutcome.VALID
    assert res.billing_tier == "paid"
    assert res.billing_error is not None
    assert "not free tier" in res.billing_error


@pytest.mark.asyncio
async def test_openrouter_missing_or_non_bool_free_tier():
    """Missing or non-boolean is_free_tier defaults to 'unknown'."""
    # Missing is_free_tier
    t1 = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"label": "Key"}}))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t1)):
        res1 = await probe_openrouter("sk-or-key")
    assert res1.billing_tier == "unknown"

    # Non-boolean (string "true")
    t2 = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"is_free_tier": "true"}}))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t2)):
        res2 = await probe_openrouter("sk-or-key")
    assert res2.billing_tier == "unknown"


@pytest.mark.asyncio
async def test_openrouter_malformed_json_response():
    """Malformed response body sets billing_tier = 'unknown'."""
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="Not JSON"))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t)):
        res = await probe_openrouter("sk-or-key")
    assert res.outcome == ValidationOutcome.VALID
    assert res.billing_tier == "unknown"
    assert res.billing_error is not None


@pytest.mark.asyncio
async def test_openrouter_error_status_codes():
    """HTTP 401, 429, 500, and timeouts return appropriate ValidationOutcome."""
    # 401
    t401 = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "Unauthorized"}))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t401)):
        r401 = await probe_openrouter("bad-key")
    assert r401.outcome == ValidationOutcome.INVALID_CREDENTIAL
    assert r401.safe_code == "INVALID_API_KEY"

    # 429
    t429 = httpx.MockTransport(lambda r: httpx.Response(429, json={"error": "Rate limited"}))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t429)):
        r429 = await probe_openrouter("rate-limited-key")
    assert r429.outcome == ValidationOutcome.RATE_LIMITED

    # 500
    t500 = httpx.MockTransport(lambda r: httpx.Response(500, text="Internal Error"))
    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=t500)):
        r500 = await probe_openrouter("server-err-key")
    assert r500.outcome == ValidationOutcome.PROVIDER_UNAVAILABLE


# ==============================================================================
# 2. Free-Model Verification Policy Tests
# ==============================================================================


def test_is_approved_free_model_rules():
    """Verify free model rules: must end with :free or be in allowlist; openrouter/free is rejected."""
    # Approved models ending with :free
    assert (
        is_approved_free_model("openrouter", "openrouter/meta-llama/llama-3.3-70b-instruct:free")
        is True
    )
    assert is_approved_free_model("openrouter", "meta-llama/llama-3.2-3b-instruct:free") is True
    assert is_approved_free_model("openrouter", "google/gemini-2.0-flash-exp:free") is True

    # Paid or normal models without :free
    assert is_approved_free_model("openrouter", "openai/gpt-4o") is False
    assert is_approved_free_model("openrouter", "anthropic/claude-3.5-sonnet") is False
    assert is_approved_free_model("openrouter", "meta-llama/llama-3.3-70b-instruct") is False

    # Dynamic router openrouter/free without verified fallback
    assert is_approved_free_model("openrouter", "openrouter/free") is False

    # Approved free-tier Gemini and Groq models
    assert is_approved_free_model("gemini", "gemini-3.8-flash") is True
    assert is_approved_free_model("gemini", "gemini-3.5-flash-lite") is True
    assert is_approved_free_model("groq", "openai/gpt-oss-120b") is True
    assert is_approved_free_model("groq", "openai/gpt-oss-20b") is True

    # Paid-only providers and unapproved paid models remain rejected
    assert is_approved_free_model("openai", "gpt-4o") is False
    assert is_approved_free_model("xai", "grok-2") is False
    assert is_approved_free_model("gemini", "gemini-2.5-pro-paid") is False


@pytest.mark.asyncio
async def test_community_pool_failover_skips_cooling_key_to_next_healthy_key():
    """When one community-shared key in the pool is in cooldown, discovery provides the next healthy pooled key."""
    requester_id, _ = await create_test_user("pool_req")
    contrib1, _ = await create_test_user("pool_c1")
    contrib2, _ = await create_test_user("pool_c2")

    cred1 = await insert_test_credential(
        user_id=contrib1,
        provider_id="groq",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="verified_free_only",
    )
    cred2 = await insert_test_credential(
        user_id=contrib2,
        provider_id="groq",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="verified_free_only",
    )

    async def _fake_cooldown(scopes):
        if ("credential", str(cred1)) in scopes:
            return (True, 30, "cooldown")
        return (False, 0, None)

    with patch(
        "app.modules.routing.discovery.check_cooldown",
        side_effect=_fake_cooldown,
    ):
        candidates = await discover_cloud_candidates(
            user_id=requester_id,
            requested_provider_id="groq",
            requested_model_id="openai/gpt-oss-120b",
        )

    healthy_shared = [c for c in candidates if c.is_shared_credential and not c.is_cooling_down]
    assert len(healthy_shared) == 1
    assert healthy_shared[0].credential_id == cred2


# ==============================================================================
# 3. Community Dispatch & Routing Boundary Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_shared_openrouter_free_key_discovers_only_free_models():
    """Community-shared OpenRouter key is ONLY attached to approved :free models."""
    user_id, _ = await create_test_user("discoverer")
    contributor_id, _ = await create_test_user("contributor")

    # Contributor contributes verified free key
    await insert_test_credential(
        user_id=contributor_id,
        provider_id="openrouter",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="verified_free_only",
    )

    candidates = await discover_cloud_candidates(
        user_id=user_id,
        requested_provider_id="openrouter",
    )

    # All discovered candidates using shared credential MUST be approved free models
    shared_candidates = [c for c in candidates if c.is_shared_credential]
    assert len(shared_candidates) > 0
    for sc in shared_candidates:
        assert is_approved_free_model(sc.provider_id, sc.model_id) is True
        assert sc.model_id.endswith(":free")


@pytest.mark.asyncio
async def test_shared_openrouter_key_rejects_paid_model_filter():
    """Hard filter rejects shared credentials attempted on non-free models."""
    candidate_paid = RoutingCandidate(
        source_type="cloud",
        provider_id="openrouter",
        model_id="openai/gpt-4o",
        display_name="GPT-4o via OpenRouter",
        family="gpt-4o",
        context_window=128000,
        max_output_tokens=4096,
        is_shared_credential=True,  # Shared credential
        credential_id=uuid4(),
        free_tier_eligible=False,
    )

    eligible, filter_results = filter_candidates(
        candidates=[candidate_paid],
        routing_mode="cloud_only",
        community_opt_in=True,
        estimated_input_tokens=100,
        requested_max_output=100,
    )

    assert len(eligible) == 0
    assert len(filter_results) == 1
    assert filter_results[0].rejection_code == "PAID_MODEL_COMMUNITY_DISALLOWED"


@pytest.mark.asyncio
async def test_paid_or_unknown_credential_excluded_from_community_pool():
    """Credentials with billing_tier = 'paid' or 'unknown' are never pooled for community sharing."""
    user_id, _ = await create_test_user("pool_tester")
    c1, _ = await create_test_user("paid_contrib")
    c2, _ = await create_test_user("unknown_contrib")

    # User 1 contributes paid key
    await insert_test_credential(
        user_id=c1,
        provider_id="openrouter",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="paid",
    )

    # User 2 contributes unknown key
    await insert_test_credential(
        user_id=c2,
        provider_id="openrouter",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="unknown",
    )

    # Discovering candidates for a non-contributing student must return NO shared candidates
    candidates = await discover_cloud_candidates(
        user_id=user_id,
        requested_provider_id="openrouter",
    )
    shared_candidates = [c for c in candidates if c.is_shared_credential]
    assert len(shared_candidates) == 0


@pytest.mark.asyncio
async def test_stale_billing_verification_excluded_from_community_pool():
    """Credentials verified > 30 days ago are excluded from community pool."""
    user_id, _ = await create_test_user("stale_pool_user")
    contributor_id, _ = await create_test_user("stale_contributor")

    # Contributor key verified 35 days ago
    await insert_test_credential(
        user_id=contributor_id,
        provider_id="openrouter",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="verified_free_only",
        age_days=35,
    )

    candidates = await discover_cloud_candidates(
        user_id=user_id,
        requested_provider_id="openrouter",
    )
    shared_candidates = [c for c in candidates if c.is_shared_credential]
    assert len(shared_candidates) == 0


@pytest.mark.asyncio
async def test_shared_dispatch_success_on_approved_free_model():
    """Community-shared verified free key executes successfully on approved :free model."""
    user_id, token = await create_test_user("shared_dispatch_user")
    contributor_id, _ = await create_test_user("shared_dispatch_contrib")

    # Insert verified free credential
    cred_id = await insert_test_credential(
        user_id=contributor_id,
        provider_id="openrouter",
        sharing_scope="community_shared",
        shared_consent=True,
        billing_tier="verified_free_only",
    )

    req = ChatCompletionRequest(
        routing_mode="cloud_only",
        provider_id="openrouter",
        model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        messages=[ChatMessage(role="user", content="Hello free model")],
        idempotency_key=f"idem-free-{uuid4().hex[:8]}",
        stream=False,
    )

    # Mock gateway execution and vault decryption so no real outbound API call is made
    from app.modules.credentials.schemas import DecryptedCredential
    from app.modules.llm_gateway.interfaces import NormalizedGenerationResult, NormalizedUsage

    mock_gen_result = NormalizedGenerationResult(
        content="Hello student! I am a free model.",
        finish_reason="stop",
        usage=NormalizedUsage(input_tokens=10, output_tokens=10, total_tokens=20),
        resolved_model="meta-llama/llama-3.3-70b-instruct:free",
    )

    mock_decrypted = DecryptedCredential(
        id=cred_id,
        owner_id=contributor_id,
        provider_id="openrouter",
        raw_key="sk-or-v1-fake-free-test-key",
        masked_key="sk-or-...test",
        status="active",
        sharing_scope="community_shared",
        shared_consent=True,
    )

    with (
        patch(
            "app.modules.routing.executor.retrieve_decrypted_credential",
            new=AsyncMock(return_value=mock_decrypted),
        ),
        patch(
            "app.modules.llm_gateway.providers.openrouter.OpenRouterAdapter.generate",
            new=AsyncMock(return_value=mock_gen_result),
        ),
    ):
        result, is_streaming, decision = await route_and_dispatch(
            user_id=user_id,
            req=req,
        )

    assert is_streaming is False
    assert decision.selected_source == "cloud"
    assert decision.provider_id == "openrouter"
    assert decision.model_id == "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    assert "Hello free model" in str(result) or "free model" in str(result)


@pytest.mark.asyncio
async def test_openrouter_adapter_normalizes_prefixed_concrete_model_slugs():
    """Adapter strips internal 'openrouter/' prefix for concrete models while preserving 'openrouter/free'."""
    from types import SimpleNamespace

    from app.modules.llm_gateway.providers.openrouter import (
        OpenRouterAdapter,
        _normalize_openrouter_model_id,
    )

    assert _normalize_openrouter_model_id("openrouter/free") == "openrouter/free"
    assert (
        _normalize_openrouter_model_id("openrouter/meta-llama/llama-3.3-70b-instruct:free")
        == "meta-llama/llama-3.3-70b-instruct:free"
    )
    assert (
        _normalize_openrouter_model_id("meta-llama/llama-3.3-70b-instruct:free")
        == "meta-llama/llama-3.3-70b-instruct:free"
    )

    captured_payloads: list[dict] = []

    class _FakeStreamResponse:
        status_code = 200
        headers: dict[str, str] = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield b'data: {"id":"gen-1","model":"meta-llama/llama-3.3-70b-instruct:free","choices":[{"delta":{"content":"Hi"},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

    class _FakeClient:
        async def post(self, url, headers=None, json=None):
            captured_payloads.append(json)
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {
                    "id": "gen-1",
                    "model": json["model"],
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )

        def stream(self, method, url, headers=None, json=None):
            captured_payloads.append(json)
            return _FakeStreamResponse()

    adapter = OpenRouterAdapter()
    with patch(
        "app.modules.llm_gateway.providers.openrouter.get_http_client",
        return_value=_FakeClient(),
    ):
        await adapter.generate(
            model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="sk-or-v1-test",
        )
        chunks = [
            chunk
            async for chunk in adapter.stream(
                model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
                messages=[{"role": "user", "content": "hi"}],
                raw_key="sk-or-v1-test",
            )
        ]
        await adapter.generate(
            model_id="openrouter/free",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="sk-or-v1-test",
        )

    assert captured_payloads[0]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert captured_payloads[1]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert captured_payloads[2]["model"] == "openrouter/free"
    assert any(c.delta == "Hi" for c in chunks)


@pytest.mark.asyncio
async def test_stream_cloud_candidate_events_empty_output_raises_failover_error():
    """Empty upstream stream marks reservation unknown and raises PreFirstTokenCandidateError for failover."""
    from types import SimpleNamespace

    from app.modules.llm_gateway.interfaces import NormalizedStreamChunk, NormalizedUsage
    from app.modules.llm_gateway.token_estimation import TokenEstimate
    from app.modules.routing.executor import (
        PreFirstTokenCandidateError,
        stream_cloud_candidate_events,
    )
    from app.modules.routing.schemas import RoutingCandidate

    class _EmptyStreamAdapter:
        async def stream(self, **kwargs):
            yield NormalizedStreamChunk(
                delta="",
                finish_reason="stop",
                resolved_model="nvidia/nemotron-3.5-lightning:free",
                usage=NormalizedUsage(input_tokens=20, output_tokens=0, total_tokens=20),
            )

    res_id = uuid4()
    candidate = RoutingCandidate(
        source_type="cloud",
        provider_id="openrouter",
        model_id="openrouter/free",
        display_name="OpenRouter Free Router",
        family="openrouter",
        context_window=16384,
        max_output_tokens=4096,
        credential_id=uuid4(),
        credential_owner_id=uuid4(),
        is_shared_credential=True,
        pricing_input_cents_per_million=0,
        pricing_output_cents_per_million=0,
        free_tier_eligible=True,
    )
    req = ChatCompletionRequest(
        routing_mode="cloud_only",
        provider_id="openrouter",
        model_id="openrouter/free",
        messages=[ChatMessage(role="user", content="Hi")],
        idempotency_key=f"idem-empty-exec-{uuid4().hex[:8]}",
        stream=True,
    )
    token_est = TokenEstimate(
        estimated_input_tokens=10,
        reserved_output_tokens=100,
        total_estimated_tokens=110,
    )
    mark_unknown_mock = AsyncMock()

    with (
        patch(
            "app.modules.routing.executor._prepare_cloud_dispatch",
            new=AsyncMock(
                return_value=(
                    SimpleNamespace(reservation_id=res_id),
                    "sk-or-v1-test",
                    _EmptyStreamAdapter(),
                    [{"role": "user", "content": "Hi"}],
                )
            ),
        ),
        patch("app.modules.routing.executor.mark_outcome_unknown", new=mark_unknown_mock),
    ):
        with pytest.raises(PreFirstTokenCandidateError, match="empty text output"):
            async for _ in stream_cloud_candidate_events(
                user_id=uuid4(),
                candidate=candidate,
                req=req,
                token_estimate=token_est,
                request_id="req-empty",
            ):
                pass

    mark_unknown_mock.assert_awaited_once_with(res_id, reason="malformed_upstream_response")
