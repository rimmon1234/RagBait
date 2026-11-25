"""Groq OpenAI-compatible chat completion provider adapter."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.modules.llm_gateway.errors import (
    ProviderAdapterError,
    map_http_status_to_category,
    parse_retry_after,
)
from app.modules.llm_gateway.http_client import get_http_client, validate_upstream_url
from app.modules.llm_gateway.interfaces import (
    BaseProviderAdapter,
    NormalizedGenerationResult,
    NormalizedStreamChunk,
    NormalizedUsage,
    raise_for_stream_error_payload,
)
from app.modules.llm_gateway.streaming import parse_sse_stream

logger = logging.getLogger("ragbait.llm_gateway.groq")

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqAdapter(BaseProviderAdapter):
    """Adapter for Groq Cloud fast inference engine."""

    def __init__(self, provider_id: str = "groq"):
        super().__init__(provider_id=provider_id)

    async def stream(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        raw_key: str,
        temperature: float = 0.7,
        max_output_tokens: int = 2048,
        top_p: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[NormalizedStreamChunk]:
        """Stream SSE chunks from Groq chat/completions with include_usage."""
        validate_upstream_url(GROQ_ENDPOINT)

        headers = {
            "Authorization": f"Bearer {raw_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if top_p is not None:
            payload["top_p"] = top_p

        client = get_http_client()
        async with client.stream("POST", GROQ_ENDPOINT, headers=headers, json=payload) as response:
            if response.status_code != 200:
                raw_bytes = await response.aread()
                body_data: dict[str, Any] = {}
                try:
                    body_data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                except Exception:
                    pass

                category, message, http_status = map_http_status_to_category(
                    response.status_code, body_data
                )
                retry_after = parse_retry_after(response.headers)
                raise ProviderAdapterError(
                    provider_id=self.provider_id,
                    category=category,
                    message=message,
                    status_code=http_status,
                    retry_after=retry_after,
                )

            last_finish_reason: str | None = None
            rate_headers = dict(response.headers)
            model_returned = model_id
            upstream_gen_id: str | None = None

            async for event in parse_sse_stream(response.aiter_bytes()):
                raw_data = event.get("data", "")
                if not raw_data or raw_data == "[DONE]":
                    continue

                try:
                    chunk_json = json.loads(raw_data)
                except Exception:
                    continue

                raise_for_stream_error_payload(self.provider_id, chunk_json)

                if "model" in chunk_json:
                    model_returned = chunk_json["model"]
                if "id" in chunk_json:
                    upstream_gen_id = chunk_json["id"]

                choices = chunk_json.get("choices") or []
                if choices:
                    first = choices[0]
                    delta = first.get("delta") or {}
                    text_delta = delta.get("content")
                    if text_delta:
                        yield NormalizedStreamChunk(
                            delta=text_delta,
                            rate_limit_headers=rate_headers,
                            upstream_generation_id=upstream_gen_id,
                        )

                    finish_r = first.get("finish_reason")
                    if finish_r:
                        last_finish_reason = finish_r

                # Check usage chunk (Groq sends it as a choice-less chunk before [DONE])
                usage_obj = chunk_json.get("usage")
                if isinstance(usage_obj, dict):
                    prompt_tokens = int(usage_obj.get("prompt_tokens") or 0)
                    completion_tokens = int(usage_obj.get("completion_tokens") or 0)
                    total_tokens = int(
                        usage_obj.get("total_tokens") or (prompt_tokens + completion_tokens)
                    )

                    yield NormalizedStreamChunk(
                        finish_reason=last_finish_reason or "stop",
                        usage=NormalizedUsage(
                            input_tokens=prompt_tokens,
                            output_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            cached_tokens=0,
                            reasoning_tokens=0,
                            usage_source="authoritative",
                        ),
                        resolved_model=model_returned,
                        upstream_generation_id=upstream_gen_id,
                        rate_limit_headers=rate_headers,
                    )

    async def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        raw_key: str,
        temperature: float = 0.7,
        max_output_tokens: int = 2048,
        top_p: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> NormalizedGenerationResult:
        """Execute a non-streaming chat completion call via Groq."""
        validate_upstream_url(GROQ_ENDPOINT)

        headers = {
            "Authorization": f"Bearer {raw_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_output_tokens,
            "stream": False,
        }
        if top_p is not None:
            payload["top_p"] = top_p

        client = get_http_client()
        response = await client.post(GROQ_ENDPOINT, headers=headers, json=payload)

        body_data: dict[str, Any] = {}
        try:
            body_data = response.json()
        except Exception:
            pass

        if response.status_code != 200:
            category, message, http_status = map_http_status_to_category(
                response.status_code, body_data
            )
            retry_after = parse_retry_after(response.headers)
            raise ProviderAdapterError(
                provider_id=self.provider_id,
                category=category,
                message=message,
                status_code=http_status,
                retry_after=retry_after,
            )

        choices = body_data.get("choices") or []
        if not choices:
            raise ProviderAdapterError(
                provider_id=self.provider_id,
                category="malformed_upstream_response",
                message="No completion choices returned by Groq",
                status_code=502,
            )

        first = choices[0]
        content = (first.get("message") or {}).get("content", "")
        finish_reason = first.get("finish_reason") or "stop"

        usage_obj = body_data.get("usage") or {}
        prompt_tokens = int(usage_obj.get("prompt_tokens") or 0)
        completion_tokens = int(usage_obj.get("completion_tokens") or 0)
        total_tokens = int(usage_obj.get("total_tokens") or (prompt_tokens + completion_tokens))

        return NormalizedGenerationResult(
            content=content,
            finish_reason=finish_reason,
            usage=NormalizedUsage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached_tokens=0,
                reasoning_tokens=0,
                usage_source="authoritative" if usage_obj else "estimated",
            ),
            resolved_model=body_data.get("model") or model_id,
            upstream_generation_id=body_data.get("id"),
            rate_limit_headers=dict(response.headers),
        )
