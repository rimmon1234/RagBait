"""Server-side web search, URL safety, and bounded external evidence retrieval."""

from app.modules.chat.web_search.adapters import (
    BaseWebSearchAdapter,
    BraveSearchAdapter,
    TavilySearchAdapter,
)
from app.modules.chat.web_search.schemas import (
    RawWebResult,
    WebSearchOptions,
    WebSearchOutcome,
)
from app.modules.chat.web_search.security import (
    fetch_web_page,
    redact_search_query,
    validate_safe_public_http_url,
)
from app.modules.chat.web_search.service import WebSearchService

__all__ = [
    "BaseWebSearchAdapter",
    "BraveSearchAdapter",
    "RawWebResult",
    "TavilySearchAdapter",
    "WebSearchOptions",
    "WebSearchOutcome",
    "WebSearchService",
    "fetch_web_page",
    "redact_search_query",
    "validate_safe_public_http_url",
]
