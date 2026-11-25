"""Request and response contracts for M4.5 Grounded RAG Orchestration and Private Local."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.modules.chat.citations.schemas import MessageSourceRead
from app.modules.chat.context.schemas import TokenAccountingBreakdown
from app.modules.chat.schemas import MessageRead


def _clean_message_content(val: str) -> str:
    trimmed = val.strip() if val else ""
    if not trimmed:
        raise ValueError("Message content cannot be empty or whitespace-only")
    if len(trimmed) > 32768:
        raise ValueError("Message content exceeds maximum allowed length (32,768 characters)")
    return trimmed


class ChatMessageCreateRequest(BaseModel):
    """Synchronous M4.5 chat message submission request (non-streaming)."""

    content: str = Field(..., min_length=1, max_length=32768)
    idempotency_key: str = Field(
        default_factory=lambda: f"msg-{uuid4().hex}",
        min_length=4,
        max_length=128,
    )
    routing_mode: Literal["auto", "cloud_only", "provider_specific"] = "auto"
    provider_id: str | None = Field(default=None, min_length=2, max_length=64)
    model_id: str | None = Field(default=None, min_length=2, max_length=128)
    credential_id: UUID | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0, le=32768)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, v: Any) -> str:
        return _clean_message_content(str(v) if v is not None else "")


class OrchestrationTelemetry(BaseModel):
    """Non-sensitive execution telemetry recorded for M4.5 grounded generation."""

    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    generation_request_id: str
    retrieval_candidate_count: int
    selected_evidence_count: int
    evidence_omitted_count: int
    accounting: TokenAccountingBreakdown
    compaction_scheduled: bool = False
    citation_validation_status: str
    valid_citation_count: int = 0
    invalid_citation_count: int = 0
    repair_attempted: bool = False
    provider_id: str | None = None
    model_id: str | None = None
    resolved_model_id: str | None = None
    duration_ms: int = 0


class ChatTurnResponse(BaseModel):
    """Response returned upon completion of an M4.5 grounded RAG turn."""

    conversation_id: UUID
    user_message: MessageRead
    assistant_message: MessageRead
    sources: list[MessageSourceRead] = Field(default_factory=list)
    telemetry: OrchestrationTelemetry


class PrepareLocalRequest(BaseModel):
    """Request to retrieve context and assemble a Private Local Ollama prompt."""

    content: str = Field(..., min_length=1, max_length=32768)
    model_id: str | None = Field(default="llama3.2:3b", min_length=2, max_length=128)
    idempotency_key: str = Field(
        default_factory=lambda: f"local-{uuid4().hex}",
        min_length=4,
        max_length=128,
    )
    max_output_tokens: int | None = Field(default=None, gt=0, le=16384)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, v: Any) -> str:
        return _clean_message_content(str(v) if v is not None else "")


class PreparedSourceMetadata(BaseModel):
    """Safe Stage-3 source metadata returned to the browser during prepare-local."""

    citation_key: str
    document_id: UUID
    document_title: str
    page_number_start: int
    page_number_end: int
    relevance_score: float


class PrepareLocalResponse(BaseModel):
    """Payload equipping the student's browser to stream directly from localhost Ollama."""

    message_id: UUID
    user_message_id: UUID
    prepared_request_id: str
    conversation_id: UUID
    model_id: str
    expires_at: datetime
    prompt: str
    messages: list[dict[str, str]]
    sources: list[PreparedSourceMetadata] = Field(default_factory=list)
    accounting: TokenAccountingBreakdown
    evidence_omitted_count: int = 0


class FinalizeLocalRequest(BaseModel):
    """Payload posted by browser after completing local Ollama generation."""

    message_id: UUID
    prepared_request_id: str = Field(..., min_length=8, max_length=128)
    completion_text: str = Field(..., min_length=1, max_length=32768)
    finish_reason: str = Field(default="stop", max_length=64)
    token_usage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("completion_text", mode="before")
    @classmethod
    def validate_completion_text(cls, v: Any) -> str:
        return _clean_message_content(str(v) if v is not None else "")


class FinalizeLocalResponse(BaseModel):
    """Finalized confirmation after server-side citation reconciliation and persistence."""

    message_id: UUID
    status: Literal["completed"] = "completed"
    citations_reconciled: int
    valid_citations: list[str] = Field(default_factory=list)
    invalid_citations_stripped: list[str] = Field(default_factory=list)
    citation_validation_status: str
    sources: list[MessageSourceRead] = Field(default_factory=list)
