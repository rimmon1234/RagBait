"""M4.5 Context Manager, Token Budgeting, Prompt Builder, and Compaction package."""

from app.modules.chat.context.budget import allocate_context_budget
from app.modules.chat.context.compaction import (
    SummaryRedisLock,
    compact_conversation,
    recover_pending_compactions,
    schedule_compaction_if_needed,
)
from app.modules.chat.context.config import context_settings
from app.modules.chat.context.context_service import ContextService
from app.modules.chat.context.formatter import (
    format_evidence_envelope,
    format_source_block,
    sanitize_xml_text,
)
from app.modules.chat.context.model_capabilities import (
    get_model_capability,
    resolve_cloud_model_capability,
)
from app.modules.chat.context.schemas import (
    ConversationHistoryContext,
    HistoryTurnMessage,
    ModelCapability,
    OmittedEvidenceRecord,
    PreparedContext,
    PromptBudgetExceededError,
    SelectedEvidenceSource,
    TokenAccountingBreakdown,
)
from app.modules.chat.context.token_counter import ModelTokenCounter, build_token_counter

__all__ = [
    "ContextService",
    "ConversationHistoryContext",
    "HistoryTurnMessage",
    "ModelCapability",
    "ModelTokenCounter",
    "OmittedEvidenceRecord",
    "PreparedContext",
    "PromptBudgetExceededError",
    "SelectedEvidenceSource",
    "SummaryRedisLock",
    "TokenAccountingBreakdown",
    "allocate_context_budget",
    "build_token_counter",
    "compact_conversation",
    "context_settings",
    "format_evidence_envelope",
    "format_source_block",
    "get_model_capability",
    "recover_pending_compactions",
    "resolve_cloud_model_capability",
    "sanitize_xml_text",
    "schedule_compaction_if_needed",
]
