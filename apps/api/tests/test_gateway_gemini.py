"""Tests for Google Gemini native provider adapter."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.llm_gateway.errors import ProviderAdapterError
from app.modules.llm_gateway.providers.gemini import GeminiAdapter


@pytest.mark.asyncio
async def test_gemini_streaming_success():
    adapter = GeminiAdapter()

    sse_body = (
        b'data: {"candidates": [{"content": {"parts": [{"text": "Hello "}], "role": "model"}}]}\n\n'
        b'data: {"candidates": [{"content": {"parts": [{"text": "World!"}], "role": "model"}, "finishReason": "STOP"}], '
        b'"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15, "cachedContentTokenCount": 2}}\n\n'
    )

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.aiter_bytes = lambda: _to_aiter([sse_body])

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    messages = [
        {"role": "system", "content": "You are Gemini."},
        {"role": "user", "content": "Hi"},
    ]

    with patch(
        "app.modules.llm_gateway.providers.gemini.get_http_client", return_value=mock_client
    ):
        chunks = []
        async for chunk in adapter.stream(
            model_id="gemini-1.5-flash",
            messages=messages,
            raw_key="secret-gemini-key",
            temperature=0.5,
            max_output_tokens=1000,
        ):
            chunks.append(chunk)

    # Verify request call
    call_args = mock_client.stream.call_args
    assert call_args[0][0] == "POST"
    assert (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:streamGenerateContent?alt=sse"
        in call_args[0][1]
    )
    assert call_args[1]["headers"]["x-goog-api-key"] == "secret-gemini-key"
    assert "key=" not in call_args[0][1]  # Strict requirement: API key never in query params

    payload = call_args[1]["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "You are Gemini."
    assert payload["contents"][0]["role"] == "user"
    assert payload["generationConfig"]["temperature"] == 0.5
    assert payload["generationConfig"]["maxOutputTokens"] == 1000

    # Verify streamed chunks
    deltas = [c.delta for c in chunks if c.delta]
    assert deltas == ["Hello ", "World!"]

    # Verify terminal usage chunk
    usage_chunks = [c for c in chunks if c.usage is not None]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.input_tokens == 10
    assert u.output_tokens == 5
    assert u.total_tokens == 15
    assert u.cached_tokens == 2
    assert u.usage_source == "authoritative"
    assert usage_chunks[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_gemini_safety_block_raises():
    adapter = GeminiAdapter()

    sse_body = b'data: {"promptFeedback": {"blockReason": "SAFETY"}}\n\n'

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.aiter_bytes = lambda: _to_aiter([sse_body])

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.gemini.get_http_client", return_value=mock_client
    ):
        with pytest.raises(ProviderAdapterError) as exc_info:
            async for _ in adapter.stream(
                model_id="gemini-1.5-flash",
                messages=[{"role": "user", "content": "blocked prompt"}],
                raw_key="key",
            ):
                pass

        assert exc_info.value.category == "content_safety_blocked"


@pytest.mark.asyncio
async def test_gemini_429_retry_after():
    adapter = GeminiAdapter()

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "30"}
    mock_response.aread.return_value = b'{"error": {"code": 429, "message": "Resource exhausted"}}'

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.gemini.get_http_client", return_value=mock_client
    ):
        with pytest.raises(ProviderAdapterError) as exc_info:
            async for _ in adapter.stream(
                model_id="gemini-1.5-flash",
                messages=[{"role": "user", "content": "hi"}],
                raw_key="key",
            ):
                pass

        assert exc_info.value.category in (
            "provider_quota_exhausted",
            "provider_rate_limited",
            "quota_exceeded",
            "rate_limited",
        )
        assert exc_info.value.retry_after == 30


@pytest.mark.asyncio
async def test_gemini_generate_non_streaming():
    adapter = GeminiAdapter()

    resp_json = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Academic explanation."}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 10,
            "totalTokenCount": 30,
        },
    }

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = resp_json

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.gemini.get_http_client", return_value=mock_client
    ):
        res = await adapter.generate(
            model_id="gemini-1.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="key",
        )

    assert res.content == "Academic explanation."
    assert res.finish_reason == "stop"
    assert res.usage.input_tokens == 20
    assert res.usage.output_tokens == 10
    assert res.usage.total_tokens == 30


async def _to_aiter(items):
    for item in items:
        yield item
