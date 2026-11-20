"""Tests for OpenRouter provider adapter and dynamic alias handling."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.llm_gateway.providers.openrouter import OpenRouterAdapter


async def _to_aiter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_openrouter_dynamic_alias_streaming():
    adapter = OpenRouterAdapter()

    # Dynamic alias scenario: Request sends 'openrouter/free'
    # Upstream chunk returns actual resolved model 'meta-llama/llama-3.2-3b-instruct:free'
    sse_body = (
        b'data: {"id": "gen-123", "model": "meta-llama/llama-3.2-3b-instruct:free", "choices": [{"delta": {"content": "Free "}}]}\n\n'
        b'data: {"id": "gen-123", "model": "meta-llama/llama-3.2-3b-instruct:free", "choices": [{"delta": {"content": "routing!"}, "finish_reason": "stop"}]}\n\n'
        b'data: {"id": "gen-123", "model": "meta-llama/llama-3.2-3b-instruct:free", "choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 14, "total_tokens": 39}}\n\n'
        b"data: [DONE]\n\n"
    )

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.aiter_bytes = lambda: _to_aiter([sse_body])

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.stream.return_value.__aenter__.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.openrouter.get_http_client",
        return_value=mock_client,
    ):
        chunks = []
        async for chunk in adapter.stream(
            model_id="openrouter/free",
            messages=[{"role": "user", "content": "hello"}],
            raw_key="openrouter-test-key",
            temperature=0.7,
            max_output_tokens=256,
        ):
            chunks.append(chunk)

    call_args = mock_client.stream.call_args
    assert call_args[0][0] == "POST"
    assert "https://openrouter.ai/api/v1/chat/completions" in call_args[0][1]
    assert call_args[1]["headers"]["Authorization"] == "Bearer openrouter-test-key"
    assert call_args[1]["headers"]["HTTP-Referer"] == "https://ragbait.app"
    assert call_args[1]["headers"]["X-Title"] == "RagBait"
    assert call_args[1]["json"]["model"] == "openrouter/free"

    # Verify deltas
    deltas = [c.delta for c in chunks if c.delta]
    assert deltas == ["Free ", "routing!"]

    # Verify resolved model captured from upstream chunks
    for c in chunks:
        assert c.resolved_model == "meta-llama/llama-3.2-3b-instruct:free"
        assert c.upstream_generation_id == "gen-123"

    # Verify usage
    usage_chunks = [c for c in chunks if c.usage is not None]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.input_tokens == 25
    assert u.output_tokens == 14
    assert u.total_tokens == 39
    assert u.usage_source == "authoritative"


@pytest.mark.asyncio
async def test_openrouter_non_streaming_dynamic_alias():
    adapter = OpenRouterAdapter()

    resp_json = {
        "id": "gen-456",
        "model": "meta-llama/llama-3.2-3b-instruct:free",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Resolved non-streaming text."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 18,
            "completion_tokens": 12,
            "total_tokens": 30,
        },
    }

    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = resp_json

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    with patch(
        "app.modules.llm_gateway.providers.openrouter.get_http_client",
        return_value=mock_client,
    ):
        res = await adapter.generate(
            model_id="openrouter/free",
            messages=[{"role": "user", "content": "hi"}],
            raw_key="key",
        )

    assert res.content == "Resolved non-streaming text."
    assert res.finish_reason == "stop"
    assert res.resolved_model == "meta-llama/llama-3.2-3b-instruct:free"
    assert res.upstream_generation_id == "gen-456"
    assert res.usage.input_tokens == 18
    assert res.usage.output_tokens == 12
    assert res.usage.total_tokens == 30
