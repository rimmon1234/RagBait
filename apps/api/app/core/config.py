"""RagBait Application Configuration."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    PROJECT_NAME: str = "RagBait API"
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000", "http://127.0.0.1:3000"])

    # Database
    DATABASE_URL: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        description="asyncpg PostgreSQL connection string",
    )
    DATABASE_POOL_MIN: int = 2
    DATABASE_POOL_MAX: int = 10
    DATABASE_COMMAND_TIMEOUT: float = 10.0

    # Supabase Auth
    SUPABASE_URL: str = Field(
        default="http://127.0.0.1:54321",
        description="Base Supabase URL",
    )
    SUPABASE_ANON_KEY: str = Field(
        default="mock-anon-key-for-local-dev-at-least-32-chars",
        description="Public anonymous API key",
    )
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        default="mock-service-role-key-for-local-dev-at-least-32-chars",
        description="Confidential service role key",
    )
    SUPABASE_JWT_SECRET: str = Field(
        default="mock-jwt-secret-for-local-dev-at-least-32-chars",
        description="Secret key for JWT verification if symmetric",
    )

    # Redis & Rate Limiting
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: int = 60
    RATE_LIMIT_HEARTBEAT: int = 1
    RATE_LIMIT_ADMIN_READ: int = 60
    RATE_LIMIT_ADMIN_MUTATE: int = 30

    # Presence
    HEARTBEAT_INTERVAL_SECONDS: int = 60
    ACTIVE_THRESHOLD_SECONDS: int = 120


settings = Settings()
