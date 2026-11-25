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
    SUMMARY_MAX_RETRIES: int = Field(default=3, ge=1, le=10)

    # Citation validation & bounded repair
    CITATION_REPAIR_ATTEMPTS: int = Field(default=1, ge=0, le=2)

    # Private Local prepared request TTL
    PREPARED_LOCAL_TTL_SECONDS: int = Field(default=600, ge=60, le=3600)


context_settings = ContextSettings()
