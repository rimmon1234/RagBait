"""Live upstream smoke tests against real provider endpoints.

DISABLED BY DEFAULT. Set RAGBAIT_LIVE_TESTS=1 and configure environment variables:
  - GEMINI_API_KEY or TEST_GEMINI_API_KEY
  - GROQ_API_KEY or TEST_GROQ_API_KEY
  - OPENROUTER_API_KEY or TEST_OPENROUTER_API_KEY
  - CEREBRAS_API_KEY or TEST_CEREBRAS_API_KEY
"""

import os

import pytest

from app.modules.llm_gateway.providers.cerebras import CerebrasAdapter
from app.modules.llm_gateway.providers.gemini import GeminiAdapter
from app.modules.llm_gateway.providers.groq import GroqAdapter
from app.modules.llm_gateway.providers.openrouter import OpenRouterAdapter

LIVE_TESTS_ENABLED = os.getenv("RAGBAIT_LIVE_TESTS") == "1"


@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Live provider smoke tests disabled by default (opt-in via RAGBAIT_LIVE_TESTS=1)",
)
@pytest.mark.asyncio
async def test_live_gemini_smoke():
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("TEST_GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY / TEST_GEMINI_API_KEY not set")

    adapter = GeminiAdapter()
    chunks = []
    async for chunk in adapter.stream(
        model_id="gemini-3.8-flash",
        messages=[{"role": "user", "content": "Respond with 'PONG'"}],
        raw_key=api_key,
        max_output_tokens=10,
    ):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks)
    assert len(text) > 0


@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Live provider smoke tests disabled by default (opt-in via RAGBAIT_LIVE_TESTS=1)",
)
@pytest.mark.asyncio
async def test_live_groq_smoke():
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("TEST_GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY / TEST_GROQ_API_KEY not set")

    adapter = GroqAdapter()
    res = await adapter.generate(
        model_id="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "Say hello"}],
        raw_key=api_key,
        max_output_tokens=10,
    )
    assert len(res.content) > 0


@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Live provider smoke tests disabled by default (opt-in via RAGBAIT_LIVE_TESTS=1)",
)
@pytest.mark.asyncio
async def test_live_openrouter_smoke():
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("TEST_OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY / TEST_OPENROUTER_API_KEY not set")

    adapter = OpenRouterAdapter()
    res = await adapter.generate(
        model_id="openrouter/free",
        messages=[{"role": "user", "content": "Say hello"}],
        raw_key=api_key,
        max_output_tokens=10,
    )
    assert len(res.content) > 0


@pytest.mark.skipif(
    not LIVE_TESTS_ENABLED,
    reason="Live provider smoke tests disabled by default (opt-in via RAGBAIT_LIVE_TESTS=1)",
)
@pytest.mark.asyncio
async def test_live_cerebras_smoke():
    api_key = os.getenv("CEREBRAS_API_KEY") or os.getenv("TEST_CEREBRAS_API_KEY")
    if not api_key:
        pytest.skip("CEREBRAS_API_KEY / TEST_CEREBRAS_API_KEY not set")

    adapter = CerebrasAdapter()
    res = await adapter.generate(
        model_id="cerebras/gpt-oss-120b",
        messages=[{"role": "user", "content": "Say hello"}],
        raw_key=api_key,
        max_output_tokens=10,
    )
    assert len(res.content) > 0
