"""Server-authoritative configuration for M4.6 Streaming Generation Reliability & Load Control."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationSettings(BaseSettings):
    """Centralized operational limits for distributed semaphores, leases, heartbeats, and timeouts."""

    model_config = SettingsConfigDict(
        env_prefix="RAGBAIT_",
        env_file=(".env", "../.env", "../../.env"),
        extra="ignore",
    )

    GENERATION_CONVERSATION_LIMIT: int = Field(default=1, ge=1, le=5)
    GENERATION_USER_LIMIT: int = Field(default=2, ge=1, le=10)
    GENERATION_GLOBAL_CLOUD_LIMIT: int = Field(default=10, ge=1, le=200)
    GENERATION_ADMISSION_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)
    GENERATION_LEASE_TTL_SECONDS: int = Field(default=60, ge=5, le=600)
    GENERATION_HEARTBEAT_SECONDS: int = Field(default=15, ge=1, le=300)
    GENERATION_KEEPALIVE_SECONDS: int = Field(default=10, ge=1, le=120)
    GENERATION_FIRST_TOKEN_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)
    GENERATION_IDLE_TIMEOUT_SECONDS: int = Field(default=30, ge=2, le=300)
    GENERATION_MAX_DURATION_SECONDS: int = Field(default=180, ge=5, le=1800)
    GENERATION_CANCEL_MARKER_TTL_SECONDS: int = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def validate_reliability_bounds(self) -> "GenerationSettings":
        """Reject unsafe or impossible timing/concurrency configurations at startup."""
        if self.GENERATION_HEARTBEAT_SECONDS >= self.GENERATION_LEASE_TTL_SECONDS:
            raise ValueError(
                "GENERATION_HEARTBEAT_SECONDS must be strictly less than GENERATION_LEASE_TTL_SECONDS"
            )
        if self.GENERATION_IDLE_TIMEOUT_SECONDS <= self.GENERATION_KEEPALIVE_SECONDS:
            raise ValueError(
                "GENERATION_IDLE_TIMEOUT_SECONDS must exceed GENERATION_KEEPALIVE_SECONDS"
            )
        if self.GENERATION_MAX_DURATION_SECONDS < self.GENERATION_IDLE_TIMEOUT_SECONDS:
            raise ValueError(
                "GENERATION_MAX_DURATION_SECONDS cannot be less than GENERATION_IDLE_TIMEOUT_SECONDS"
            )
        if self.GENERATION_GLOBAL_CLOUD_LIMIT < self.GENERATION_USER_LIMIT:
            raise ValueError("GENERATION_GLOBAL_CLOUD_LIMIT must be >= GENERATION_USER_LIMIT")
        return self


generation_settings = GenerationSettings()
