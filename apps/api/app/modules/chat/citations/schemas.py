"""Pydantic contracts for 4-stage evidence provenance and citation validation."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import AppError

UNAVAILABLE_SOURCE_PLACEHOLDER = (
    "[Source Unavailable — Document Archived or Moderated by Administrator]"
)


class CitationValidationError(AppError):
    """Raised when generated text retains invalid or hallucinated citations after repair."""

    def __init__(
        self,
        message: str = "Generated response contains unsupported citation references",
        code: str = "CITATION_VALIDATION_FAILED",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=422,
            details=details,
        )


class CitationParseResult(BaseModel):
    """Structured extraction of bracketed citation markers from assistant output."""

    raw_tokens: list[str] = Field(default_factory=list)
    unique_keys: list[str] = Field(default_factory=list)
    duplicate_keys: list[str] = Field(default_factory=list)
    malformed_tokens: list[str] = Field(default_factory=list)
    total_occurrences: int = 0


class CitationValidationResult(BaseModel):
    """Result of validating extracted citations against the server-supplied evidence registry."""

    is_valid: bool
    status: Literal[
        "valid_grounded",
        "valid_no_sources",
        "invalid_unknown_citations",
        "invalid_malformed_citations",
    ]
    valid_keys: list[str] = Field(default_factory=list)
    unknown_keys: list[str] = Field(default_factory=list)
    duplicate_keys: list[str] = Field(default_factory=list)
    malformed_tokens: list[str] = Field(default_factory=list)
    sanitized_text: str
    total_occurrences: int = 0


class MessageSourceRead(BaseModel):
    """Public evidence snapshot returned for a persisted chat message after live KB revalidation."""

    id: UUID
    citation_key: str
    document_id: UUID
    document_title: str
    file_type: str | None = None
    page_number_start: int
    page_number_end: int
    excerpt: str
    relevance_score: float
    included_in_context: bool
    referenced_in_answer: bool
    is_available: bool
    document_viewer_url: str | None = None


class MessageSourcesResponse(BaseModel):
    """Response envelope for GET /api/v1/chat/messages/{message_id}/sources."""

    message_id: UUID
    conversation_id: UUID
    sources: list[MessageSourceRead] = Field(default_factory=list)
