"""Tests for Groq provider adapter."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.llm_gateway.errors import ProviderAdapterError
from app.modules.llm_gateway.providers.groq import GroqAdapter


async def _to_aiter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_groq_streaming_success():
    adapter = GroqAdapter()

    sse_body = (
        b'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "Fast "}, "finish_reason": null}]}\n\n'
        b'data: {"id": "chatcmpl-1", "choices": [{"delta": {"content": "inference!"}, "finish_reason": "stop"}]}\n\n'
        b'data: {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23}}\n\n'
        b"data: [DONE]\n\n"
    )

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {
        "x-ratelimit-remaining-requests": "29",
        "x-ratelimit-reset-requests": "2.5",
    }
    mock_response.aiter_bytes = lambda: _to_aiter([sse_body])

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch("app.modules.llm_gateway.providers.groq.get_http_client", return_value=mock_client):
        chunks = []
        async for chunk in adapter.stream(
            model_id="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="groq-test-key",
            temperature=0.7,
            max_output_tokens=512,
        ):
            chunks.append(chunk)

    call_args = mock_client.stream.call_args
    assert call_args[0][0] == "POST"
    assert "https://api.groq.com/openai/v1/chat/completions" in call_args[0][1]
    assert call_args[1]["headers"]["Authorization"] == "Bearer groq-test-key"
    assert call_args[1]["json"]["stream_options"]["include_usage"] is True

    deltas = [c.delta for c in chunks if c.delta]
    assert deltas == ["Fast ", "inference!"]

    usage_chunks = [c for c in chunks if c.usage is not None]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.input_tokens == 15
    assert u.output_tokens == 8
    assert u.total_tokens == 23
    assert u.usage_source == "authoritative"
    assert usage_chunks[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_groq_invalid_credential():
    adapter = GroqAdapter()

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.headers = {}
    mock_response.aread.return_value = b'{"error": {"message": "Invalid API Key"}}'

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch("app.modules.llm_gateway.providers.groq.get_http_client", return_value=mock_client):
        with pytest.raises(ProviderAdapterError) as exc_info:
            async for _ in adapter.stream(
                model_id="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "hi"}],
                raw_key="bad-key",
            ):
                pass

        assert exc_info.value.category == "invalid_credential"


@pytest.mark.asyncio
async def test_groq_generate_non_streaming():
    adapter = GroqAdapter()

    resp_json = {
        "id": "chatcmpl-nonstream",
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Groq non-streaming text."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
        },
    }

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = resp_json

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch("app.modules.llm_gateway.providers.groq.get_http_client", return_value=mock_client):
        res = await adapter.generate(
            model_id="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="key",
        )

    assert res.content == "Groq non-streaming text."
    assert res.finish_reason == "stop"
    assert res.usage.input_tokens == 12
    assert res.usage.output_tokens == 6
    assert res.usage.total_tokens == 18
    assert res.resolved_model == "llama-3.3-70b-versatile"
