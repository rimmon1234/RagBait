"""Typed Pydantic contracts for M4.5 Context Management & Grounded Prompt Preparation."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import AppError

AnswerPolicy = Literal["note_first_adaptive", "notes_only"]

QuestionType = Literal[
    "academic_conceptual",
    "course_specific",
    "factual_static",
    "factual_current",
    "mathematical_procedural",
    "comparative",
    "explicit_web_search",
    "ambiguous_or_private",
]

EvidenceSufficiencyStatus = Literal[
    "notes_sufficient",
    "notes_partially_sufficient",
    "notes_insufficient",
    "notes_conflicting",
    "external_verification_recommended",
]

AttributionMode = Literal[
    "notes_only",
    "hybrid_notes_and_general",
    "general_knowledge",
    "web_augmented",
    "hybrid_all_sources",
    "abstention",
]


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
    count_source: Literal["exact_tokenizer", "local_estimate"] = "local_estimate"
    counting_strategy: str = "conservative_multilingual_upper_bound_v2"
    is_approximate: bool = True


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


class SelectedWebSource(BaseModel):
    """Web search result or fetched page excerpt selected for <web_context_evidence>."""

    citation_key: str  # W1, W2, ...
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
    formatted_xml_block: str = ""
    formatted_token_count: int = 0
    included_in_context: bool = True


class AdvancedRagTrace(BaseModel):
    """Non-sensitive trace of M4.9 query rewriting, query expansion, and LLM re-ranking."""

    original_query: str
    rewritten_query: str
    expanded_queries: list[str] = Field(default_factory=list)
    used_llm_query_rewrite: bool = False
    used_llm_query_expansion: bool = False
    used_llm_reranking: bool = False
    raw_candidate_count: int = 0
    reranked_selected_count: int = 0
    planner_model_id: str | None = None


class AnswerPlan(BaseModel):
    """Server-side M4.9 adaptive answering plan combining retrieval sufficiency and policy."""

    answer_policy: AnswerPolicy = "note_first_adaptive"
    question_type: QuestionType = "academic_conceptual"
    sufficiency: EvidenceSufficiencyStatus = "notes_insufficient"
    requires_web_search: bool = False
    explicit_web_request: bool = False
    use_general_knowledge: bool = True
    should_abstain: bool = False
    abstain_reason: str | None = None
    web_search_queries: list[str] = Field(default_factory=list)
    web_search_status: Literal[
        "not_needed",
        "executed",
        "unavailable_unconfigured",
        "unavailable_rate_limited",
        "unavailable_error",
        "skipped_notes_only",
        "skipped_private_local",
    ] = "not_needed"
    expected_attribution_mode: AttributionMode = "notes_only"
    planning_notes: str = ""


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
    selected_web_sources: list[SelectedWebSource] = Field(default_factory=list)
    answer_plan: AnswerPlan = Field(default_factory=AnswerPlan)
    advanced_rag_trace: AdvancedRagTrace | None = None
    omitted_evidence_count: int = 0
    omitted_candidates: list[OmittedEvidenceRecord] = Field(default_factory=list)
    retrieval_candidate_count: int = 0
    accounting: TokenAccountingBreakdown
    model_fingerprint: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
