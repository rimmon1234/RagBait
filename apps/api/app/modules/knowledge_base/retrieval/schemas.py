"""Typed schemas for internal hybrid retrieval and ranking results."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """Ranked retrieved passage with physical page and document provenance."""

    chunk_id: UUID
    document_id: UUID
    subject_id: UUID
    topic_id: UUID | None = None
    document_title: str
    raw_text: str
    page_number_start: int
    page_number_end: int
    page_spans: list[dict[str, Any]]
    content_hash: str
    chunk_version: int
    model_fingerprint: str
    vector_rank: int | None = None
    fts_rank: int | None = None
    vector_distance: float | None = None
    fts_score: float | None = None
    rrf_score: float
    final_rank: int
    source_kind: str = "text_layer"


class RetrievalQuery(BaseModel):
    """Internal retrieval query parameters."""

    conversation_id: UUID
    query_text: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


class RetrievalResult(BaseModel):
    """Result of an authorized subject-scoped hybrid retrieval execution."""

    conversation_id: UUID
    subject_id: UUID
    query_text: str
    model_fingerprint: str
    chunks: list[RetrievedChunk]
    total_retrieved: int
