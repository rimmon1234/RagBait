"""M4.6 tests: SSE UTF-8 framing, 4 provider streaming adapters, failover, and single quota settlement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.admission.schemas import AdmissionReservation, SettlementResult
from app.modules.llm_gateway.errors import ProviderAdapterError
from app.modules.llm_gateway.interfaces import NormalizedStreamChunk, NormalizedUsage
from app.modules.llm_gateway.providers.cerebras import CerebrasAdapter
from app.modules.llm_gateway.providers.gemini import GeminiAdapter
from app.modules.llm_gateway.providers.groq import GroqAdapter
from app.modules.llm_gateway.providers.openrouter import OpenRouterAdapter
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatMessage
from app.modules.llm_gateway.streaming import (
    format_sse_comment,
    format_sse_event,
    parse_sse_stream,
)
from app.modules.llm_gateway.token_estimation import TokenEstimate
from app.modules.routing.executor import stream_cloud_candidate_events
from app.modules.routing.schemas import CandidateFilterResult, RoutingCandidate
from app.modules.routing.service import stream_route_and_dispatch


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
        error_body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self.headers = headers or {}
        self._error_body = error_body

    async def aread(self) -> bytes:
        return self._error_body

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


class _FakeHttpClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    @asynccontextmanager
    async def stream(
        self, method: str, url: str, **kwargs: Any
    ) -> AsyncIterator[_FakeStreamResponse]:
        yield self._response


def _make_candidate(provider_id: str, model_id: str) -> RoutingCandidate:
    return RoutingCandidate(
        source_type="cloud",
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        family="llama",
        capabilities=["chat", "streaming"],
        context_window=131072,
        max_output_tokens=4096,
        credential_id=uuid4(),
        credential_owner_id=uuid4(),
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
        pricing_output_cents_per_million=0,
    )


class TestSseSerializationAndParsing:
    """Verify SSE frame serialization, keepalives, and multibyte split decoding."""

    def test_format_sse_event_and_comment(self) -> None:
        frame = format_sse_event("text_delta", {"message_id": "m1", "delta": "hi"}, event_id=7)
        assert frame == 'id: 7\nevent: text_delta\ndata: {"message_id":"m1","delta":"hi"}\n\n'
        assert format_sse_comment("keep\n-alive\r") == ": keep -alive\n\n"

    async def test_parse_sse_stream_handles_split_utf8_bengali_hindi_and_crlf(self) -> None:
        bengali = "অপারেটিং সিস্টেম — পেজিং ও সেগমেন্টেশন [S1]"
        hindi = "मेमोरी प्रबंधन और सुरक्षा"
        raw = (
            ": keep-alive\r\n\r\n"
            f'id: 10\r\nevent: text_delta\r\ndata: {{"b":"{bengali}",\ndata: "h":"{hindi}"}}\r\n\r\n'
        ).encode()

        async def _tiny_byte_chunks() -> AsyncIterator[bytes]:
            # Slice every 1-2 bytes right across multibyte UTF-8 sequences
            idx = 0
            step = 1
            while idx < len(raw):
                yield raw[idx : idx + step]
                idx += step
                step = 2 if step == 1 else 1

        frames = [ev async for ev in parse_sse_stream(_tiny_byte_chunks(), include_id=True)]
        assert len(frames) == 1
        assert frames[0]["id"] == "10"
        assert frames[0]["event"] == "text_delta"
        assert bengali in frames[0]["data"]
        assert hindi in frames[0]["data"]


class TestProviderStreamAdapters:
    """Verify all 4 cloud adapters normalize chunks/usage and sanitize mid-stream error frames."""

    @pytest.mark.parametrize(
        ("adapter_cls", "module_path", "model_id", "sse_chunks", "expected_text"),
        [
            (
                GroqAdapter,
                "app.modules.llm_gateway.providers.groq.get_http_client",
                "llama-3.3-70b-versatile",
                [
                    b'data: {"id":"g1","model":"llama-3.3-70b-versatile","choices":[{"delta":{"content":"Hello "}}]}\n\n',
                    b'data: {"id":"g1","choices":[{"delta":{"content":"Groq [S1]"},"finish_reason":"stop"}]}\n\n',
                    b'data: {"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}\n\n',
                    b"data: [DONE]\n\n",
                ],
                "Hello Groq [S1]",
            ),
            (
                OpenRouterAdapter,
                "app.modules.llm_gateway.providers.openrouter.get_http_client",
                "meta-llama/llama-3.3-70b-instruct:free",
                [
                    b'data: {"id":"or1","model":"meta-llama/llama-3.3-70b-instruct:free","choices":[{"delta":{"content":"Open"}}]}\n\n',
                    b'data: {"id":"or1","choices":[{"delta":{"content":"Router [S1]"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}\n\n',
                    b"data: [DONE]\n\n",
                ],
                "OpenRouter [S1]",
            ),
            (
                CerebrasAdapter,
                "app.modules.llm_gateway.providers.cerebras.get_http_client",
                "llama-3.3-70b",
                [
                    b'data: {"id":"cb1","model":"llama-3.3-70b","choices":[{"delta":{"content":"Cere"}}]}\n\n',
                    b'data: {"id":"cb1","choices":[{"delta":{"content":"bras [S1]"},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":3,"total_tokens":12}}\n\n',
                    b"data: [DONE]\n\n",
                ],
                "Cerebras [S1]",
            ),
            (
                GeminiAdapter,
                "app.modules.llm_gateway.providers.gemini.get_http_client",
                "gemini-2.5-flash",
                [
                    b'data: {"candidates":[{"content":{"parts":[{"text":"Gem"}]},"finishReason":null}]}\n\n',
                    b'data: {"candidates":[{"content":{"parts":[{"text":"ini [S1]"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":4,"totalTokenCount":12}}\n\n',
                ],
                "Gemini [S1]",
            ),
        ],
    )
    async def test_provider_stream_happy_path_all_four_adapters(
        self,
        adapter_cls: type[Any],
        module_path: str,
        model_id: str,
        sse_chunks: list[bytes],
        expected_text: str,
    ) -> None:
        fake_resp = _FakeStreamResponse(status_code=200, chunks=sse_chunks)
        with patch(module_path, return_value=_FakeHttpClient(fake_resp)):
            adapter = adapter_cls()
            chunks = [
                c
                async for c in adapter.stream(
                    model_id=model_id,
                    messages=[{"role": "user", "content": "Hi"}],
                    raw_key="secret-api-key-never-leak",
                )
            ]
        combined = "".join(c.delta for c in chunks)
        assert combined == expected_text
        assert any(c.usage is not None and c.usage.output_tokens > 0 for c in chunks)

    @pytest.mark.parametrize(
        ("adapter_cls", "module_path", "model_id"),
        [
            (
                GroqAdapter,
                "app.modules.llm_gateway.providers.groq.get_http_client",
                "llama-3.3-70b-versatile",
            ),
            (
                OpenRouterAdapter,
                "app.modules.llm_gateway.providers.openrouter.get_http_client",
                "meta-llama/llama-3.3-70b-instruct:free",
            ),
            (
                CerebrasAdapter,
                "app.modules.llm_gateway.providers.cerebras.get_http_client",
                "llama-3.3-70b",
            ),
            (
                GeminiAdapter,
                "app.modules.llm_gateway.providers.gemini.get_http_client",
                "gemini-2.5-flash",
            ),
        ],
    )
    async def test_mid_stream_error_payload_raises_sanitized_provider_error(
        self,
        adapter_cls: type[Any],
        module_path: str,
        model_id: str,
    ) -> None:
        fake_resp = _FakeStreamResponse(
            status_code=200,
            chunks=[
                b'data: {"error":{"code":429,"message":"Rate limit exceeded for secret-api-key-never-leak"}}\n\n'
            ],
        )
        with patch(module_path, return_value=_FakeHttpClient(fake_resp)):
            adapter = adapter_cls()
            with pytest.raises(ProviderAdapterError) as exc_info:
                async for _ in adapter.stream(
                    model_id=model_id,
                    messages=[{"role": "user", "content": "Hi"}],
                    raw_key="secret-api-key-never-leak",
                ):
                    pass
        assert "secret-api-key-never-leak" not in exc_info.value.message


class TestRoutingStreamingFailoverAndSettlement:
    """Verify pre-first-token failover, post-first-token no-failover, and single settlement."""

    async def test_pre_first_token_failure_fails_over_cleanly_while_post_token_does_not(
        self,
    ) -> None:
        user_id = uuid4()
        cand1 = _make_candidate("groq", "llama-3.3-70b-versatile")
        cand2 = _make_candidate("openrouter", "meta-llama/llama-3.3-70b-instruct:free")

        req = ChatCompletionRequest(
            routing_mode="auto",
            messages=[ChatMessage(role="user", content="Explain paging")],
            stream=True,
            idempotency_key=f"failover-{uuid4().hex[:8]}",
        )

        class _FailFirstAdapter:
            async def stream(self, **kwargs: Any) -> AsyncIterator[NormalizedStreamChunk]:
                raise ProviderAdapterError(
                    provider_id="groq",
                    category="rate_limited",
                    message="Groq rate limited",
                    status_code=429,
                    retry_after=3,
                )
                yield  # pragma: no cover

        class _GoodSecondAdapter:
            async def stream(self, **kwargs: Any) -> AsyncIterator[NormalizedStreamChunk]:
                yield NormalizedStreamChunk(delta="Paging maps ", resolved_model="llama-ok")
                yield NormalizedStreamChunk(
                    delta="virtual memory [S1].",
                    finish_reason="stop",
                    resolved_model="llama-ok",
                    usage=NormalizedUsage(
                        input_tokens=15,
                        output_tokens=6,
                        total_tokens=21,
                        usage_source="authoritative",
                    ),
                )

        settled_reservations: list[Any] = []
        unknown_reservations: list[Any] = []

        async def _fake_prepare(
            user_id: Any,
            candidate: RoutingCandidate,
            req: Any,
            token_estimate: Any,
            request_id: str,
            payload_hash: str | None = None,
        ) -> tuple[AdmissionReservation, str, Any, list[dict[str, str]]]:
            now = datetime.now(UTC)
            res = AdmissionReservation(
                reservation_id=uuid4(),
                lease_token=f"lease_{uuid4().hex[:8]}",
                lease_expires_at=now + timedelta(seconds=60),
                user_id=user_id,
                credential_id=candidate.credential_id or uuid4(),
                provider_id=candidate.provider_id,
                model_id=candidate.model_id,
                status="reserved",
                estimated_input_tokens=15,
                estimated_output_tokens=512,
                estimated_cost_cents=0,
                estimated_cost_microcents=0,
                created_at=now,
            )
            adapter = (
                _FailFirstAdapter() if candidate.provider_id == "groq" else _GoodSecondAdapter()
            )
            return res, "sk-fake", adapter, [{"role": "user", "content": "Explain paging"}]

        async def _fake_settle(**kwargs: Any) -> SettlementResult:
            settled_reservations.append(kwargs["reservation_id"])
            return SettlementResult(
                reservation_id=kwargs["reservation_id"],
                status="settled",
                actual_input_tokens=15,
                actual_output_tokens=6,
                actual_cost_microcents=0,
                actual_cost_cents=0,
                settled_at=datetime.now(UTC),
            )

        async def _fake_unknown(res_id: Any, reason: str = "") -> None:
            unknown_reservations.append((res_id, reason))

        with (
            patch("app.modules.routing.service.check_user_idempotency", new=AsyncMock()),
            patch(
                "app.modules.routing.service.discover_all_candidates",
                new=AsyncMock(return_value=[cand1, cand2]),
            ),
            patch(
                "app.modules.routing.service.filter_candidates",
                return_value=(
                    [cand1, cand2],
                    [
                        CandidateFilterResult(candidate=cand1, eligible=True),
                        CandidateFilterResult(candidate=cand2, eligible=True),
                    ],
                ),
            ),
            patch("app.modules.routing.service.rank_candidates", return_value=[cand1, cand2]),
            patch(
                "app.modules.routing.executor._prepare_cloud_dispatch", side_effect=_fake_prepare
            ),
            patch("app.modules.routing.executor.settle_reservation", side_effect=_fake_settle),
            patch("app.modules.routing.executor.mark_outcome_unknown", side_effect=_fake_unknown),
            patch("app.modules.admission.cooldown.set_cooldown", new=AsyncMock()),
        ):
            stream_iter, _ = await stream_route_and_dispatch(user_id=user_id, req=req)
            events = [ev async for ev in stream_iter]

        types = [e.event_type for e in events]
        assert types == ["started", "delta", "delta", "completed"]
        assert events[0].provider_id == "openrouter"
        assert (
            "".join(e.delta for e in events if e.event_type == "delta")
            == "Paging maps virtual memory [S1]."
        )
        assert len(settled_reservations) == 1
        assert len(unknown_reservations) == 1

        # Part B: Post-first-token error MUST NOT failover to candidate #2
        class _FailMidStreamAdapter:
            async def stream(self, **kwargs: Any) -> AsyncIterator[NormalizedStreamChunk]:
                yield NormalizedStreamChunk(delta="Partial ", resolved_model="llama-ok")
                raise ProviderAdapterError(
                    provider_id="groq",
                    category="provider_unavailable",
                    message="Connection reset mid-stream",
                    status_code=502,
                )

        settled_reservations.clear()
        unknown_reservations.clear()
        seen_providers: list[str] = []

        async def _fake_prepare_mid(
            user_id: Any,
            candidate: RoutingCandidate,
            req: Any,
            token_estimate: Any,
            request_id: str,
            payload_hash: str | None = None,
        ) -> tuple[AdmissionReservation, str, Any, list[dict[str, str]]]:
            seen_providers.append(candidate.provider_id)
            now = datetime.now(UTC)
            res = AdmissionReservation(
                reservation_id=uuid4(),
                lease_token=f"lease_{uuid4().hex[:8]}",
                lease_expires_at=now + timedelta(seconds=60),
                user_id=user_id,
                credential_id=candidate.credential_id or uuid4(),
                provider_id=candidate.provider_id,
                model_id=candidate.model_id,
                status="reserved",
                estimated_input_tokens=15,
                estimated_output_tokens=512,
                estimated_cost_cents=0,
                estimated_cost_microcents=0,
                created_at=now,
            )
            return res, "sk-fake", _FailMidStreamAdapter(), [{"role": "user", "content": "Explain"}]

        with (
            patch("app.modules.routing.service.check_user_idempotency", new=AsyncMock()),
            patch(
                "app.modules.routing.service.discover_all_candidates",
                new=AsyncMock(return_value=[cand1, cand2]),
            ),
            patch(
                "app.modules.routing.service.filter_candidates",
                return_value=(
                    [cand1, cand2],
                    [
                        CandidateFilterResult(candidate=cand1, eligible=True),
                        CandidateFilterResult(candidate=cand2, eligible=True),
                    ],
                ),
            ),
            patch("app.modules.routing.service.rank_candidates", return_value=[cand1, cand2]),
            patch(
                "app.modules.routing.executor._prepare_cloud_dispatch",
                side_effect=_fake_prepare_mid,
            ),
            patch("app.modules.routing.executor.settle_reservation", side_effect=_fake_settle),
            patch("app.modules.routing.executor.mark_outcome_unknown", side_effect=_fake_unknown),
        ):
            stream_iter2, _ = await stream_route_and_dispatch(user_id=user_id, req=req)
            events2 = [ev async for ev in stream_iter2]

        assert seen_providers == ["groq"]  # NEVER attempted cand2 after emitting visible tokens!
        assert [e.event_type for e in events2] == ["started", "delta", "error"]
        assert len(unknown_reservations) == 1
        assert len(settled_reservations) == 0

    async def test_stream_cancellation_settles_quota_once_and_emits_cancelled(self) -> None:
        user_id = uuid4()
        cand = _make_candidate("groq", "llama-3.3-70b-versatile")
        req = ChatCompletionRequest(
            routing_mode="cloud_only",
            messages=[ChatMessage(role="user", content="Cancel me")],
            stream=True,
            idempotency_key=f"cancel-{uuid4().hex[:8]}",
        )
        token_est = TokenEstimate(
            estimated_input_tokens=10,
            reserved_output_tokens=128,
            total_estimated_tokens=138,
            estimation_method="test",
        )

        cancel_state = {"cancelled": False}

        def _cancel_check() -> bool:
            return cancel_state["cancelled"]

        class _MultiChunkAdapter:
            async def stream(self, **kwargs: Any) -> AsyncIterator[NormalizedStreamChunk]:
                yield NormalizedStreamChunk(delta="First part ")
                yield NormalizedStreamChunk(delta="Should not emit")

        settlement_calls: list[dict[str, Any]] = []

        async def _fake_prepare(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
            now = datetime.now(UTC)
            res = AdmissionReservation(
                reservation_id=uuid4(),
                lease_token=f"lease_{uuid4().hex[:8]}",
                lease_expires_at=now + timedelta(seconds=60),
                user_id=user_id,
                credential_id=cand.credential_id or uuid4(),
                provider_id="groq",
                model_id=cand.model_id,
                status="reserved",
                estimated_input_tokens=10,
                estimated_output_tokens=128,
                estimated_cost_cents=0,
                estimated_cost_microcents=0,
                created_at=now,
            )
            return res, "sk-fake", _MultiChunkAdapter(), [{"role": "user", "content": "Cancel me"}]

        async def _fake_settle(**kwargs: Any) -> SettlementResult:
            settlement_calls.append(kwargs)
            return SettlementResult(
                reservation_id=kwargs["reservation_id"],
                status="settled",
                actual_input_tokens=10,
                actual_output_tokens=3,
                actual_cost_microcents=0,
                actual_cost_cents=0,
                settled_at=datetime.now(UTC),
            )

        with (
            patch(
                "app.modules.routing.executor._prepare_cloud_dispatch", side_effect=_fake_prepare
            ),
            patch("app.modules.routing.executor.settle_reservation", side_effect=_fake_settle),
            patch("app.modules.routing.executor.mark_outcome_unknown", new=AsyncMock()),
        ):
            events = []
            async for ev in stream_cloud_candidate_events(
                user_id=user_id,
                candidate=cand,
                req=req,
                token_estimate=token_est,
                request_id="req-cancel-1",
                cancel_check=_cancel_check,
            ):
                events.append(ev)
                if ev.event_type == "delta":
                    cancel_state["cancelled"] = True

        assert [e.event_type for e in events] == ["started", "delta", "cancelled"]
        assert len(settlement_calls) == 1
        assert settlement_calls[0]["req"].error_category == "cancelled_by_client"
