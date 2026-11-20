"""Tests for Cerebras provider adapter with reasoning token accounting."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.llm_gateway.providers.cerebras import CerebrasAdapter


async def _to_aiter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_cerebras_reasoning_and_cached_tokens():
    adapter = CerebrasAdapter()

    sse_body = (
        b'data: {"id": "chatcmpl-cer-1", "choices": [{"delta": {"content": "Cerebras "}}]}\n\n'
        b'data: {"id": "chatcmpl-cer-1", "choices": [{"delta": {"content": "speed!"}, "finish_reason": "stop"}]}\n\n'
        b'data: {"id": "chatcmpl-cer-1", "choices": [], "usage": {'
        b'"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150, '
        b'"prompt_tokens_details": {"cached_tokens": 15}, '
        b'"completion_tokens_details": {"reasoning_tokens": 40}'
        b"}}\n\n"
        b"data: [DONE]\n\n"
    )

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {
        "x-ratelimit-limit-requests-day": "14400",
        "x-ratelimit-limit-tokens-minute": "60000",
    }
    mock_response.aiter_bytes = lambda: _to_aiter([sse_body])

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.cerebras.get_http_client",
        return_value=mock_client,
    ):
        chunks = []
        async for chunk in adapter.stream(
            model_id="llama3.1-8b",
            messages=[{"role": "user", "content": "think fast"}],
            raw_key="cerebras-key",
            temperature=0.6,
            max_output_tokens=1024,
        ):
            chunks.append(chunk)

    call_args = mock_client.stream.call_args
    assert call_args[0][0] == "POST"
    assert "https://api.cerebras.ai/v1/chat/completions" in call_args[0][1]
    assert call_args[1]["headers"]["Authorization"] == "Bearer cerebras-key"

    deltas = [c.delta for c in chunks if c.delta]
    assert deltas == ["Cerebras ", "speed!"]

    usage_chunks = [c for c in chunks if c.usage is not None]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage

    assert u.input_tokens == 50
    assert u.output_tokens == 100
    assert u.total_tokens == 150  # Must not double-count reasoning or cached tokens
    assert u.cached_tokens == 15
    assert u.reasoning_tokens == 40
    assert u.usage_source == "authoritative"


@pytest.mark.asyncio
async def test_cerebras_generate_non_streaming():
    adapter = CerebrasAdapter()

    resp_json = {
        "id": "chatcmpl-cer-nonstream",
        "model": "llama3.1-8b",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Wafer-scale completion."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 30,
            "completion_tokens": 45,
            "total_tokens": 75,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = resp_json

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.cerebras.get_http_client",
        return_value=mock_client,
    ):
        res = await adapter.generate(
            model_id="llama3.1-8b",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="key",
        )

    assert res.content == "Wafer-scale completion."
    assert res.finish_reason == "stop"
    assert res.usage.input_tokens == 30
    assert res.usage.output_tokens == 45
    assert res.usage.total_tokens == 75
    assert res.usage.reasoning_tokens == 12
