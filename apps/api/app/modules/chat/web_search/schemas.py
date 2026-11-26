"""Typed contracts for server-side web search results and outcomes."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class WebSearchOptions(BaseModel):
    """Execution bounds for a turn's server-side web search."""

    max_queries: int = Field(default=2, ge=1, le=3)
    max_results_per_query: int = Field(default=5, ge=1, le=10)
    fetch_top_pages: int = Field(default=0, ge=0, le=5)
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


class RawWebResult(BaseModel):
    """Normalized web search result or fetched page excerpt."""

    title: str
    url: str
    domain: str
    publisher: str | None = None
    published_at: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    excerpt: str
    content_hash: str
    snippet_only: bool = True
    retrieval_rank: int = 1
    relevance_score: float = 0.0
    query_used: str = ""


class WebSearchOutcome(BaseModel):
    """Structured outcome returned by WebSearchService."""

    status: Literal[
        "executed",
        "unavailable_unconfigured",
        "unavailable_rate_limited",
        "unavailable_error",
    ]
    provider: str = "none"
    queries_executed: list[str] = Field(default_factory=list)
    results: list[RawWebResult] = Field(default_factory=list)
    cache_hits: int = 0
    latency_ms: int = 0
    error_message: str | None = None
