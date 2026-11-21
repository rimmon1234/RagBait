"""Configuration settings for the Knowledge Base background extraction worker."""

import os
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Validated configuration for durable extraction worker processes."""

    model_config = SettingsConfigDict(
        env_prefix="KB_WORKER_",
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Worker Identity
    WORKER_ID: str = Field(
        default_factory=lambda: f"worker-{os.getpid()}-{uuid4().hex[:8]}",
        description="Unique identifier for this worker instance",
    )

    # Database
    DATABASE_URL: str = Field(
        default="",
        description="Database URL for worker. If empty, falls back to application Settings.DATABASE_URL",
    )

    # Job Leases and Heartbeating
    JOB_LEASE_TTL_SECONDS: int = Field(
        default=120,
        ge=30,
        le=600,
        description="Duration in seconds before an unrenewed job lease expires",
    )
    HEARTBEAT_INTERVAL_SECONDS: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Interval in seconds between lease renewal heartbeats",
    )

    # Execution Deadlines and Retries
    MAX_EXTRACTION_DURATION_SECONDS: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Maximum wall-clock execution time allowed for document extraction",
    )
    MAX_ATTEMPTS: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum total execution attempts for transient failures",
    )
    BACKOFF_BASE_SECONDS: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Base multiplier for exponential retry backoff",
    )
    BACKOFF_MAX_SECONDS: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Maximum backoff ceiling in seconds",
    )

    # Concurrency and Polling
    CONCURRENCY: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Number of concurrent worker extraction tasks per process",
    )
    POLL_INTERVAL_SECONDS: float = Field(
        default=3.0,
        ge=0.5,
        le=30.0,
        description="Seconds to sleep when no jobs are available to claim",
    )

    # Resource and Payload Limits
    MAX_PDF_PAGES: int = Field(
        default=2000,
        ge=1,
        le=10000,
        description="Maximum allowed physical pages in a PDF document",
    )
    MAX_EXTRACTED_TEXT_BYTES: int = Field(
        default=26_214_400,  # 25 MiB
        ge=1024,
        le=104_857_600,
        description="Maximum extracted raw text payload size in bytes",
    )
    DOWNLOAD_CHUNK_SIZE: int = Field(
        default=65536,  # 64 KiB
        ge=4096,
        le=1_048_576,
        description="Chunk size for streaming file downloads from Supabase Storage",
    )

    @model_validator(mode="after")
    def validate_timings(self) -> "WorkerSettings":
        """Ensure heartbeat interval is safely shorter than lease TTL."""
        if self.HEARTBEAT_INTERVAL_SECONDS >= self.JOB_LEASE_TTL_SECONDS:
            raise ValueError(
                f"HEARTBEAT_INTERVAL_SECONDS ({self.HEARTBEAT_INTERVAL_SECONDS}) must be strictly "
                f"less than JOB_LEASE_TTL_SECONDS ({self.JOB_LEASE_TTL_SECONDS})"
            )
        return self

    def get_database_url(self) -> str:
        """Resolve database URL with fallback to core application settings."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        from app.core.config import settings

        return settings.DATABASE_URL


worker_settings = WorkerSettings()
