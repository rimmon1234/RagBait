"""Tests for Node Agent Ollama loopback interactions using mocked transport."""

import json

import httpx
import pytest

from ragbait_node import ollama


@pytest.mark.asyncio
async def test_check_ollama_alive():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"version": "0.40.16"}))
    async with httpx.AsyncClient(transport=transport) as client:
        alive, ver = await ollama.check_ollama_alive(client)
        assert alive is True
        assert ver == "0.40.16"


@pytest.mark.asyncio
async def test_list_ollama_models():
    mock_models = [{"name": "llama3.2:3b", "size": 2000000}]
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"models": mock_models}))
    async with httpx.AsyncClient(transport=transport) as client:
        models = await ollama.list_ollama_models(client)
        assert len(models) == 1
        assert models[0]["name"] == "llama3.2:3b"


@pytest.mark.asyncio
async def test_stream_chat_completion():
    ndjson_lines = (
        json.dumps({"message": {"content": "Hello "}, "done": False})
        + "\n"
        + json.dumps({"message": {"content": "world!"}, "done": False})
        + "\n"
        + json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "eval_count": 2,
                "prompt_eval_count": 5,
                "total_duration": 100_000_000,
                "eval_duration": 50_000_000,
            }
        )
        + "\n"
    )

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=ndjson_lines.encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        deltas = []
        final_stats = None
        async for delta, done, stats in ollama.stream_chat_completion(
            model="llama3.2:3b",
            messages=[{"role": "user", "content": "hi"}],
            client=client,
        ):
            if delta:
                deltas.append(delta)
            if done and stats:
                final_stats = stats

        assert "".join(deltas) == "Hello world!"
        assert final_stats is not None
        assert final_stats["output_tokens"] == 2
        assert final_stats["input_tokens"] == 5
        assert final_stats["tokens_per_second"] == 40.0
