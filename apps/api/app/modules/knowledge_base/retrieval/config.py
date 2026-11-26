"""Configuration settings for M4.4 Hybrid Retrieval and Reciprocal Rank Fusion."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class RetrievalSettings(BaseSettings):
    """Centralized retrieval and RRF parameters."""

    model_config = SettingsConfigDict(
        env_prefix="RETRIEVAL_",
        env_file=".env",
        extra="ignore",
    )

    # Candidate and result count bounds
    default_match_count: int = 5
    max_match_count: int = 50
    default_candidate_limit: int = 30
    max_candidate_limit: int = 100

    # RRF fusion parameters (M4.0 initial baseline)
    default_rrf_k: int = 60
    default_vector_weight: float = 0.60
    default_fts_weight: float = 0.40

    # Query constraints
    max_query_tokens: int = 128


retrieval_settings = RetrievalSettings()
