"""Configuration settings for M4.5 Context Manager, Compaction, and Citation Validation."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    """Centralized, server-authoritative configuration for RAG context management."""

    model_config = SettingsConfigDict(
        env_prefix="RAGBAIT_",
        env_file=(".env", "../.env", "../../.env"),
        extra="ignore",
    )

    # Context window budgeting & reserves
    CONTEXT_SAFETY_MARGIN: int = Field(default=512, ge=0, le=8192)
    CONTEXT_OUTPUT_RESERVE: int = Field(default=2048, ge=128, le=32768)
    CONTEXT_EVIDENCE_RATIO: float = Field(default=0.60, gt=0.0, le=0.90)
    CONTEXT_MAX_EVIDENCE_TOKENS: int = Field(default=4000, ge=256, le=32000)
    CONTEXT_RECENT_MESSAGES: int = Field(default=6, ge=2, le=20)
    CONTEXT_HISTORY_FETCH_LIMIT: int = Field(default=40, ge=6, le=100)

    # Retrieval pool limits
    RETRIEVAL_CANDIDATE_POOL_LIMIT: int = Field(default=15, ge=5, le=30)

    # Conversation summary & compaction
    SUMMARY_RETAIN_MESSAGES: int = Field(default=6, ge=2, le=20)
    SUMMARY_MAX_TOKENS: int = Field(default=500, ge=100, le=2000)
    SUMMARY_TRIGGER_MIN_MESSAGES: int = Field(default=10, ge=6, le=100)
    SUMMARY_TRIGGER_TOKEN_THRESHOLD: int = Field(default=1500, ge=200, le=16000)
    SUMMARY_SEGMENT_MAX_TOKENS: int = Field(default=1200, ge=300, le=8000)
    SUMMARY_LOCK_TTL_SECONDS: int = Field(default=30, ge=5, le=300)
    SUMMARY_WORKER_LEASE_SECONDS: int = Field(default=60, ge=10, le=600)
    SUMMARY_RETRY_BASE_SECONDS: float = Field(default=5.0, ge=0.0, le=300.0)
    SUMMARY_RETRY_MAX_SECONDS: float = Field(default=120.0, ge=1.0, le=3600.0)
    SUMMARY_MAX_RETRIES: int = Field(default=3, ge=1, le=10)

    # Citation validation & bounded repair
    CITATION_REPAIR_ATTEMPTS: int = Field(default=1, ge=0, le=2)

    # Adaptive answering & Advanced RAG settings (M4.9)
    DEFAULT_ANSWER_POLICY: str = Field(default="note_first_adaptive")
    ENABLE_LLM_QUERY_REWRITE: bool = Field(default=True)
    ENABLE_LLM_QUERY_EXPANSION: bool = Field(default=True)
    ENABLE_LLM_RERANKING: bool = Field(default=True)
    ADVANCED_RAG_LLM_TIMEOUT_SECONDS: float = Field(default=6.0, ge=1.0, le=30.0)
    ADVANCED_RAG_MAX_EXPANSIONS: int = Field(default=2, ge=0, le=4)
    ADVANCED_RAG_RERANK_TOP_K: int = Field(default=8, ge=2, le=20)
    WEB_EVIDENCE_MAX_RATIO: float = Field(default=0.25, gt=0.0, le=0.50)
    WEB_EVIDENCE_MAX_TOKENS: int = Field(default=1600, ge=200, le=8000)

    # Web search & SSRF-hardened fetching settings (server-side only)
    WEB_SEARCH_ENABLED: bool = Field(default=True)
    WEB_SEARCH_PROVIDER: str = Field(default="none")  # none | tavily | brave
    WEB_SEARCH_API_KEY: str = Field(default="")
    WEB_SEARCH_TIMEOUT_SECONDS: float = Field(default=8.0, ge=1.0, le=30.0)
    WEB_SEARCH_MAX_QUERIES_PER_TURN: int = Field(default=2, ge=1, le=3)
    WEB_SEARCH_MAX_RESULTS_PER_QUERY: int = Field(default=5, ge=1, le=10)
    WEB_SEARCH_MAX_PAGE_FETCHES: int = Field(default=2, ge=0, le=5)
    WEB_FETCH_MAX_BYTES: int = Field(default=524_288, ge=4096, le=2_097_152)
    WEB_SEARCH_CACHE_TTL_SECONDS: int = Field(default=900, ge=0, le=86400)
    WEB_SEARCH_USER_RPM: int = Field(default=6, ge=1, le=60)
    WEB_SEARCH_GLOBAL_RPM: int = Field(default=60, ge=1, le=600)

    # Private Local prepared request TTL
    PREPARED_LOCAL_TTL_SECONDS: int = Field(default=600, ge=60, le=3600)


context_settings = ContextSettings()
