"""Google Gemini native REST and SSE adapter."""

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

logger = logging.getLogger("ragbait.llm_gateway.gemini")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _map_finish_reason(gemini_reason: str | None) -> str:
    """Map Gemini candidate finish reasons to standard normalized finish reasons."""
    if not gemini_reason:
        return "stop"
    match = gemini_reason.upper()
    if match == "STOP":
        return "stop"
    if match == "MAX_TOKENS":
        return "length"
    if match in ("SAFETY", "RECITATION"):
        return "content_filter"
    return "unknown"


def _build_gemini_payload(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_output_tokens: int = 2048,
    top_p: float | None = None,
) -> dict[str, Any]:
    """Construct Google Gemini native payload."""
    contents: list[dict[str, Any]] = []
    system_parts: list[dict[str, str]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_parts.append({"text": content})
        else:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})

    # Gemini requires at least one content item
    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})

    gen_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
    }
    if top_p is not None:
        gen_config["topP"] = top_p

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": gen_config,
    }

    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}

    return payload


class GeminiAdapter(BaseProviderAdapter):
    """Native adapter for Google Gemini via Generative Language API."""

    def __init__(self, provider_id: str = "gemini"):
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
        """Stream SSE chunks from Gemini :streamGenerateContent?alt=sse."""
        clean_model = model_id.removeprefix("models/")
        url = f"{GEMINI_BASE_URL}/{clean_model}:streamGenerateContent?alt=sse"
        validate_upstream_url(url)

        headers = {
            "x-goog-api-key": raw_key,
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        payload = _build_gemini_payload(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            top_p=top_p,
        )

        client = get_http_client()
        async with client.stream("POST", url, headers=headers, json=payload) as response:
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

            async for event in parse_sse_stream(response.aiter_bytes()):
                raw_data = event.get("data", "")
                if not raw_data:
                    continue

                try:
                    chunk_json = json.loads(raw_data)
                except Exception:
                    continue

                raise_for_stream_error_payload(self.provider_id, chunk_json)

                # Check promptFeedback
                feedback = chunk_json.get("promptFeedback")
                if isinstance(feedback, dict) and feedback.get("blockReason"):
                    raise ProviderAdapterError(
                        provider_id=self.provider_id,
                        category="content_safety_blocked",
                        message=f"Generation blocked by Gemini: {feedback.get('blockReason')}",
                        status_code=400,
                    )

                # Candidates
                candidates = chunk_json.get("candidates") or []
                if candidates:
                    first = candidates[0]
                    content_obj = first.get("content") or {}
                    parts = content_obj.get("parts") or []
                    for part in parts:
                        text_val = part.get("text")
                        if text_val:
                            yield NormalizedStreamChunk(
                                delta=text_val,
                                rate_limit_headers=rate_headers,
                            )

                    raw_finish = first.get("finishReason")
                    if raw_finish:
                        mapped = _map_finish_reason(raw_finish)
                        if mapped == "content_filter":
                            raise ProviderAdapterError(
                                provider_id=self.provider_id,
                                category="content_safety_blocked",
                                message="Generation blocked by safety filter",
                                status_code=400,
                            )
                        last_finish_reason = mapped

                # Usage metadata (usually arrives in the terminal chunk)
                usage_meta = chunk_json.get("usageMetadata")
                if isinstance(usage_meta, dict):
                    prompt_tokens = int(usage_meta.get("promptTokenCount") or 0)
                    cand_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
                    total_tokens = int(
                        usage_meta.get("totalTokenCount") or (prompt_tokens + cand_tokens)
                    )
                    cached_tokens = int(usage_meta.get("cachedContentTokenCount") or 0)

                    yield NormalizedStreamChunk(
                        finish_reason=last_finish_reason or "stop",
                        usage=NormalizedUsage(
                            input_tokens=prompt_tokens,
                            output_tokens=cand_tokens,
                            total_tokens=total_tokens,
                            cached_tokens=cached_tokens,
                            reasoning_tokens=0,
                            usage_source="authoritative",
                        ),
                        resolved_model=clean_model,
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
        """Execute a non-streaming generation call via :generateContent."""
        clean_model = model_id.removeprefix("models/")
        url = f"{GEMINI_BASE_URL}/{clean_model}:generateContent"
        validate_upstream_url(url)

        headers = {
            "x-goog-api-key": raw_key,
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        payload = _build_gemini_payload(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            top_p=top_p,
        )

        client = get_http_client()
        response = await client.post(url, headers=headers, json=payload)

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

        feedback = body_data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderAdapterError(
                provider_id=self.provider_id,
                category="content_safety_blocked",
                message=f"Generation blocked by Gemini: {feedback.get('blockReason')}",
                status_code=400,
            )

        candidates = body_data.get("candidates") or []
        if not candidates:
            raise ProviderAdapterError(
                provider_id=self.provider_id,
                category="malformed_upstream_response",
                message="No candidate completions returned by Gemini",
                status_code=502,
            )

        first = candidates[0]
        content_obj = first.get("content") or {}
        parts = content_obj.get("parts") or []
        full_text = "".join(p.get("text", "") for p in parts)
        finish_reason = _map_finish_reason(first.get("finishReason"))

        usage_meta = body_data.get("usageMetadata") or {}
        prompt_tokens = int(usage_meta.get("promptTokenCount") or 0)
        cand_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
        total_tokens = int(usage_meta.get("totalTokenCount") or (prompt_tokens + cand_tokens))
        cached_tokens = int(usage_meta.get("cachedContentTokenCount") or 0)

        return NormalizedGenerationResult(
            content=full_text,
            finish_reason=finish_reason,
            usage=NormalizedUsage(
                input_tokens=prompt_tokens,
                output_tokens=cand_tokens,
                total_tokens=total_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=0,
                usage_source="authoritative" if usage_meta else "estimated",
            ),
            resolved_model=clean_model,
            rate_limit_headers=dict(response.headers),
        )
