"""Isolated local Ollama communication for the Node Agent.

MANDATORY SECURITY RULES:
1. Endpoint is strictly hardcoded to http://127.0.0.1:11434.
2. No backend-supplied host, URL, or port is ever accepted.
3. Redirects are rejected (follow_redirects=False).
4. Raw thinking/reasoning process blocks are strictly ignored and never yielded.
"""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

logger = logging.getLogger("ragbait.node.ollama")

OLLAMA_LOOPBACK_URL = "http://127.0.0.1:11434"


class OllamaRunnerError(Exception):
    """Exception raised when local Ollama daemon encounters a failure."""


async def check_ollama_alive(client: httpx.AsyncClient | None = None) -> tuple[bool, str]:
    """Verify local Ollama service availability."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=3.0, follow_redirects=False)
        close_client = True

    try:
        res = await client.get(f"{OLLAMA_LOOPBACK_URL}/api/version")
        if res.status_code == 200:
            version = res.json().get("version", "unknown")
            return True, version
        return False, f"HTTP {res.status_code}"
    except Exception as e:
        return False, str(e)
    finally:
        if close_client:
            await client.aclose()


async def list_ollama_models(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Retrieve all models installed on local Ollama."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=5.0, follow_redirects=False)
        close_client = True

    try:
        res = await client.get(f"{OLLAMA_LOOPBACK_URL}/api/tags")
        if res.status_code == 200:
            data = res.json()
            return list(data.get("models", []))
        return []
    except Exception as e:
        logger.warning(f"Failed to query Ollama models: {e}")
        return []
    finally:
        if close_client:
            await client.aclose()


async def stream_chat_completion(
    model: str,
    messages: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncGenerator[tuple[str, bool, dict[str, Any] | None], None]:
    """Execute streaming inference on local Ollama, yielding (delta, done, stats)."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0),
            follow_redirects=False,
        )
        close_client = True

    payload = {
        "model": model,
        "messages": messages,
        "options": options or {},
        "stream": True,
    }

    start_time = time.time()
    try:
        async with client.stream(
            "POST",
            f"{OLLAMA_LOOPBACK_URL}/api/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as res:
            if res.status_code != 200:
                body = await res.aread()
                err_text = body.decode("utf-8", errors="replace")
                raise OllamaRunnerError(f"Ollama returned HTTP {res.status_code}: {err_text}")

            async for line in res.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                done = chunk.get("done", False)
                message = chunk.get("message", {})
                content = message.get("content", "")

                stats = None
                if done:
                    eval_count = chunk.get("eval_count", 0)
                    prompt_eval_count = chunk.get("prompt_eval_count", 0)
                    total_dur_ns = chunk.get("total_duration", 0)
                    eval_dur_ns = chunk.get("eval_duration", 0)

                    duration_ms = (
                        int(total_dur_ns / 1_000_000)
                        if total_dur_ns
                        else int((time.time() - start_time) * 1000)
                    )
                    tokens_per_sec = 0.0
                    if eval_dur_ns and eval_dur_ns > 0:
                        tokens_per_sec = round(eval_count / (eval_dur_ns / 1_000_000_000), 1)

                    stats = {
                        "input_tokens": prompt_eval_count,
                        "output_tokens": eval_count,
                        "duration_ms": duration_ms,
                        "tokens_per_second": tokens_per_sec,
                    }

                yield content, done, stats

    finally:
        if close_client:
            await client.aclose()
