"""Typed Pydantic contracts for M4.5 Context Management & Grounded Prompt Preparation."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import AppError


class PromptBudgetExceededError(AppError):
    """Raised when prompt/input requirements exceed the model context budget."""

    def __init__(
        self,
        message: str = "Input prompt exceeds available model context window",
        code: str = "PROMPT_BUDGET_EXCEEDED",
        details: Any = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=422,
            details=details,
        )


class ModelCapability(BaseModel):
    """Server-authoritative model capability and context limit record."""

    model_id: str
    provider_id: str
    family: str
    source_type: Literal["cloud", "local"]
    context_window: int = Field(..., gt=0)
    recommended_budget: int = Field(..., gt=0)
    max_output_tokens: int = Field(..., gt=0)
    default_output_reserve: int = Field(..., gt=0)
    safety_margin_tokens: int = Field(..., ge=0)
    token_counting_strategy: str
    exact_tokenizer_available: bool = False
    message_overhead_tokens: int = Field(default=4, ge=0)
    primer_overhead_tokens: int = Field(default=3, ge=0)
    supports_generation: bool = True
    supports_streaming: bool = True
    is_known_registry_model: bool = True


class TokenAccountingBreakdown(BaseModel):
    """Detailed audit breakdown of context budget allocation and actual usage."""

    model_effective_ceiling: int = Field(..., gt=0)
    reserved_output_tokens: int = Field(..., gt=0)
    system_prompt_tokens: int = Field(..., ge=0)
    guardrail_overhead_tokens: int = Field(..., ge=0)
    safety_margin_tokens: int = Field(..., ge=0)
    available_working_budget: int = Field(..., ge=0)
    current_question_tokens: int = Field(..., ge=0)
    evidence_budget_ceiling: int = Field(..., ge=0)
    evidence_token_count: int = Field(..., ge=0)
    summary_token_count: int = Field(..., ge=0)
    recent_history_token_count: int = Field(..., ge=0)
    total_input_token_count: int = Field(..., ge=0)
    total_reserved_tokens: int = Field(..., ge=0)
    utilization_ratio: float = Field(..., ge=0.0)


class SelectedEvidenceSource(BaseModel):
    """Evidence chunk selected by context budget manager with server-assigned citation key."""

    citation_key: str
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
    retrieval_rank: int
    rrf_score: float
    formatted_xml_block: str
    formatted_token_count: int
    included_in_context: bool = True


class OmittedEvidenceRecord(BaseModel):
    """Non-content telemetry entry for an evidence candidate omitted during budgeting."""

    chunk_id: UUID
    document_id: UUID
    retrieval_rank: int
    formatted_token_count: int
    reason: str


class HistoryTurnMessage(BaseModel):
    """Completed historical message included verbatim in recent conversation history."""

    id: UUID | None = None
    sequence_number: int
    role: Literal["user", "assistant"]
    content: str
    token_count: int = Field(..., ge=0)


class ConversationHistoryContext(BaseModel):
    """Loaded conversation summary and candidate uncompacted messages."""

    summary_text: str | None = None
    compacted_through_sequence: int = 0
    summary_version: int = 0
    summary_token_count: int = 0
    compaction_status: str = "idle"
    recent_messages: list[HistoryTurnMessage] = Field(default_factory=list)


class PreparedContext(BaseModel):
    """Validated, budgeted, injection-hardened prompt contract ready for generation."""

    conversation_id: UUID
    subject_id: UUID
    user_id: UUID
    model_capability: ModelCapability
    current_user_message: str
    system_prompt: str
    evidence_xml_block: str
    user_turn_prompt: str
    rendered_single_prompt: str
    llm_messages: list[dict[str, str]]
    conversation_summary: str | None = None
    compacted_through_sequence: int = 0
    recent_history: list[HistoryTurnMessage] = Field(default_factory=list)
    selected_evidence: list[SelectedEvidenceSource] = Field(default_factory=list)
    omitted_evidence_count: int = 0
    omitted_candidates: list[OmittedEvidenceRecord] = Field(default_factory=list)
    retrieval_candidate_count: int = 0
    accounting: TokenAccountingBreakdown
    model_fingerprint: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
