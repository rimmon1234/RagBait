"""M4.5 Citation Parsing, Validation, and Source Snapshot Repository package."""

from app.modules.chat.citations.parser import parse_citations
from app.modules.chat.citations.schemas import (
    UNAVAILABLE_SOURCE_PLACEHOLDER,
    CitationParseResult,
    CitationValidationError,
    CitationValidationResult,
    MessageSourceRead,
    MessageSourcesResponse,
)
from app.modules.chat.citations.source_repository import (
    get_message_sources_with_revalidation,
    insert_included_source_snapshots,
    load_server_included_keys,
    mark_referenced_citations,
)
from app.modules.chat.citations.validator import (
    build_citation_repair_messages,
    strip_invalid_citations,
    validate_citations,
)

__all__ = [
    "UNAVAILABLE_SOURCE_PLACEHOLDER",
    "CitationParseResult",
    "CitationValidationError",
    "CitationValidationResult",
    "MessageSourceRead",
    "MessageSourcesResponse",
    "build_citation_repair_messages",
    "get_message_sources_with_revalidation",
    "insert_included_source_snapshots",
    "load_server_included_keys",
    "mark_referenced_citations",
    "parse_citations",
    "strip_invalid_citations",
    "validate_citations",
]
