"""Provider adapter interface and normalized data structures."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, Field

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
