"""Provider adapter interface and normalized data structures."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.llm_gateway.errors import ProviderAdapterError, map_http_status_to_category

UsageSourceType = Literal["authoritative", "derived", "estimated", "unknown"]


class NormalizedUsage(BaseModel):
    """Provider-independent normalized token usage representation."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    usage_source: UsageSourceType = "authoritative"


class NormalizedStreamChunk(BaseModel):
    """Incremental chunk emitted by a provider streaming adapter."""

    delta: str = ""
    finish_reason: str | None = None
    usage: NormalizedUsage | None = None
    resolved_model: str | None = None
    upstream_generation_id: str | None = None
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)


class NormalizedGenerationResult(BaseModel):
    """Complete non-streaming generation result from a provider adapter."""

    content: str
    finish_reason: str = "stop"
    usage: NormalizedUsage
    resolved_model: str
    upstream_generation_id: str | None = None
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)


class GatewayStreamEvent(BaseModel):
    """Typed internal streaming event emitted by gateway and routing stream pipelines."""

    event_type: Literal["started", "delta", "completed", "cancelled", "error"]
    delta: str = ""
    finish_reason: str | None = None
    usage: NormalizedUsage | None = None
    resolved_model: str | None = None
    provider_id: str | None = None
    upstream_generation_id: str | None = None
    reservation_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    cost_microcents: int = 0
    cost_cents: int = 0
    retryable: bool = False


def raise_for_stream_error_payload(provider_id: str, chunk_json: dict[str, Any]) -> None:
    """Inspect a parsed upstream SSE JSON payload and raise sanitized ProviderAdapterError if errored."""
    err_obj = chunk_json.get("error")
    if not err_obj:
        return

    raw_status = 502
    if isinstance(err_obj, dict):
        code_val = err_obj.get("code") or err_obj.get("status")
        if isinstance(code_val, int) and 400 <= code_val < 600:
            raw_status = code_val
        elif isinstance(code_val, str) and code_val.isdigit() and 400 <= int(code_val) < 600:
            raw_status = int(code_val)

    category, safe_message, http_status = map_http_status_to_category(raw_status, chunk_json)
    raise ProviderAdapterError(
        provider_id=provider_id,
        category=category,
        message=safe_message,
        status_code=http_status,
    )


class BaseProviderAdapter(ABC):
    """Abstract base class for all upstream provider inference adapters."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id

    @abstractmethod
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
        """Stream normalized generation chunks from the upstream provider."""
        yield  # type: ignore[misc]

    @abstractmethod
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
        """Execute a non-streaming chat generation call."""
        raise NotImplementedError
