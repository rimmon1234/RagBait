"""RagBait Application Configuration."""

from typing import Literal

from pydantic import Field, model_validator
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

    # Redis & Rate Limiting (M2.4 Shared Persistence)
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    COMMUNITY_REDIS_URL: str = Field(
        default="redis://127.0.0.1:6380/0",
        description="Dedicated volatile in-memory Redis URL for ephemeral community inference queues",
    )
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: int = 60
    RATE_LIMIT_HEARTBEAT: int = 1
    RATE_LIMIT_ADMIN_READ: int = 60
    RATE_LIMIT_ADMIN_MUTATE: int = 30
    RATE_LIMIT_PAIR_ATTEMPT: int = 5
    RATE_LIMIT_PAIR_CODE_CREATE: int = 10
    TRUSTED_PROXY_IPS: list[str] = Field(
        default_factory=list,
        description="List of trusted reverse proxy IP addresses for client IP resolution",
    )

    # Presence
    HEARTBEAT_INTERVAL_SECONDS: int = 60
    ACTIVE_THRESHOLD_SECONDS: int = 120

    # Admission Control & Gateway Safeguards (M2.4)
    LLM_USER_RPM: int = 10
    LLM_USER_MAX_CONCURRENT: int = 2
    LLM_CREDENTIAL_RPM: int = 5
    LLM_CREDENTIAL_MAX_CONCURRENT: int = 1
    LLM_RATE_WINDOW_SECONDS: int = 60
    LLM_RATE_BURST: int = 1
    LLM_RESERVATION_LEASE_SECONDS: int = 120
    LLM_RESERVATION_HEARTBEAT_SECONDS: int = 30
    LLM_MAX_RESERVATION_LIFETIME_SECONDS: int = 1800
    LLM_SWEEPER_INTERVAL_SECONDS: int = 15
    LLM_SWEEPER_BATCH_SIZE: int = 50

    # Credential Vault & Encryption (M2.2)
    VAULT_DATABASE_URL: str = Field(
        default="postgresql://ragbait_vault_user:vault_service_secret_pass@127.0.0.1:54322/postgres",
        description="Dedicated connection string for vault login user (member of ragbait_vault_service)",
    )
    VAULT_MASTER_KEY: str = Field(
        default="dev-master-vault-kek-secret-key-32chars!",
        description="Master key secret for local development KEK derivation",
    )
    VAULT_PEPPER_SECRET: str = Field(
        default="dev-vault-fingerprint-pepper-secret-32chars!",
        description="Independent HMAC pepper for credential fingerprinting",
    )
    KMS_TYPE: Literal["local_hkdf", "cloud_kms"] = "local_hkdf"
    VAULT_POOL_MIN: int = 1
    VAULT_POOL_MAX: int = 3
    VAULT_DATA_ENCRYPTION_VERSION: int = 1
    VAULT_KEK_VERSION: int = 1
    VAULT_WRAPPING_CONTEXT_VERSION: int = 1

    # Community Local Inference (M2.8)
    COMMUNITY_SERVER_SIGNING_KEY_HEX: str = Field(
        default="726167626169742d636f6d6d756e6974792d6465762d7369676e696e672d3332",
        description="Server Ed25519 32-byte private key in hex for signing job authorizations",
    )
    COMMUNITY_SERVER_VERIFYING_KEY_HEX: str = Field(
        default="8967fa465ee22988e81808173fbb2366072669837ac40055beb7483e5f7ad4c8",
        description="Server Ed25519 32-byte public key in hex",
    )
    COMMUNITY_KEY_ID: str = "k1"
    COMMUNITY_PAIRING_CODE_TTL_SECONDS: int = 300
    COMMUNITY_HEARTBEAT_INTERVAL_SECONDS: int = 15
    COMMUNITY_NODE_OFFLINE_THRESHOLD_SECONDS: int = 45
    COMMUNITY_JOB_LEASE_SECONDS: int = 30
    COMMUNITY_JOB_HARD_DEADLINE_SECONDS: int = 300
    COMMUNITY_SWEEPER_INTERVAL_SECONDS: int = 15
    COMMUNITY_PENDING_CLAIM_IDLE_SECONDS: int = 30
    COMMUNITY_CONSENT_POLICY_VERSION: str = "2026-02-community-v1"

    # Knowledge Base & Document Storage (Phase 3)
    KB_STORAGE_BUCKET: str = "study-materials"
    KB_MAX_FILE_SIZE_BYTES: int = 52_428_800  # 50 MB
    KB_SIGNED_UPLOAD_EXPIRY_SECONDS: int = 7200  # 2 hours
    KB_SIGNED_DOWNLOAD_EXPIRY_SECONDS: int = 300  # 5 minutes
    KB_USER_DAILY_UPLOAD_LIMIT: int = 20

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        """Validate critical cryptographic keys and environmental separation rules."""
        # 1. Byte-length validation on key material across all environments
        master_bytes = self.VAULT_MASTER_KEY.strip().encode("utf-8")
        if len(master_bytes) < 32:
            raise ValueError(
                f"VAULT_MASTER_KEY must contain at least 32 bytes, got {len(master_bytes)}"
            )

        pepper_bytes = self.VAULT_PEPPER_SECRET.strip().encode("utf-8")
        if len(pepper_bytes) < 32:
            raise ValueError(
                f"VAULT_PEPPER_SECRET must contain at least 32 bytes, got {len(pepper_bytes)}"
            )

        if self.VAULT_MASTER_KEY == self.VAULT_PEPPER_SECRET:
            raise ValueError("VAULT_MASTER_KEY and VAULT_PEPPER_SECRET must be distinct secrets")

        # 2. Admission control numeric constraints
        if self.LLM_USER_RPM <= 0:
            raise ValueError("LLM_USER_RPM must be a positive integer")
        if self.LLM_USER_MAX_CONCURRENT <= 0:
            raise ValueError("LLM_USER_MAX_CONCURRENT must be a positive integer")
        if self.LLM_CREDENTIAL_RPM <= 0:
            raise ValueError("LLM_CREDENTIAL_RPM must be a positive integer")
        if self.LLM_CREDENTIAL_MAX_CONCURRENT <= 0:
            raise ValueError("LLM_CREDENTIAL_MAX_CONCURRENT must be a positive integer")
        if self.LLM_RATE_WINDOW_SECONDS <= 0:
            raise ValueError("LLM_RATE_WINDOW_SECONDS must be a positive integer")
        if self.LLM_RATE_BURST < 0:
            raise ValueError("LLM_RATE_BURST cannot be negative")
        if self.LLM_RESERVATION_LEASE_SECONDS <= 0:
            raise ValueError("LLM_RESERVATION_LEASE_SECONDS must be a positive integer")
        if self.LLM_RESERVATION_HEARTBEAT_SECONDS <= 0:
            raise ValueError("LLM_RESERVATION_HEARTBEAT_SECONDS must be a positive integer")
        if self.LLM_MAX_RESERVATION_LIFETIME_SECONDS <= self.LLM_RESERVATION_LEASE_SECONDS:
            raise ValueError(
                "LLM_MAX_RESERVATION_LIFETIME_SECONDS must exceed LLM_RESERVATION_LEASE_SECONDS"
            )

        # 3. Strict checks for staging and production environments
        if self.ENVIRONMENT in ("staging", "production"):
            if self.VAULT_MASTER_KEY.startswith("dev-") or "dev-master" in self.VAULT_MASTER_KEY:
                raise ValueError(
                    f"Development default VAULT_MASTER_KEY is not permitted in {self.ENVIRONMENT}"
                )
            if (
                self.VAULT_PEPPER_SECRET.startswith("dev-")
                or "dev-vault" in self.VAULT_PEPPER_SECRET
            ):
                raise ValueError(
                    f"Development default VAULT_PEPPER_SECRET is not permitted in {self.ENVIRONMENT}"
                )

        # 3. Production-only hardening checks
        if self.ENVIRONMENT == "production":
            if self.KMS_TYPE == "local_hkdf":
                raise ValueError(
                    "LocalHKDFKEKProvider is strictly prohibited in production. "
                    "A production Cloud KMS provider (KMS_TYPE='cloud_kms') must be configured."
                )
            if "vault_service_secret_pass" in self.VAULT_DATABASE_URL:
                raise ValueError(
                    "Default development password in VAULT_DATABASE_URL is not permitted in production"
                )
            if self.SUPABASE_ANON_KEY.startswith("mock-"):
                raise ValueError("Mock SUPABASE_ANON_KEY is not permitted in production")
            if self.SUPABASE_SERVICE_ROLE_KEY.startswith("mock-"):
                raise ValueError("Mock SUPABASE_SERVICE_ROLE_KEY is not permitted in production")
            if self.SUPABASE_JWT_SECRET.startswith("mock-"):
                raise ValueError("Mock SUPABASE_JWT_SECRET is not permitted in production")
            if (
                self.COMMUNITY_SERVER_SIGNING_KEY_HEX
                == "726167626169742d636f6d6d756e6974792d6465762d7369676e696e672d3332"
            ):
                raise ValueError(
                    "Default development COMMUNITY_SERVER_SIGNING_KEY_HEX is not permitted in production"
                )
            if self.COMMUNITY_REDIS_URL == self.REDIS_URL:
                raise ValueError(
                    "COMMUNITY_REDIS_URL must use a dedicated volatile Redis instance distinct from REDIS_URL in production"
                )

        if len(self.COMMUNITY_SERVER_SIGNING_KEY_HEX) != 64:
            raise ValueError(
                "COMMUNITY_SERVER_SIGNING_KEY_HEX must be a 64-character hex string (32 bytes)"
            )
        if len(self.COMMUNITY_SERVER_VERIFYING_KEY_HEX) != 64:
            raise ValueError(
                "COMMUNITY_SERVER_VERIFYING_KEY_HEX must be a 64-character hex string (32 bytes)"
            )

        try:
            priv_bytes = bytes.fromhex(self.COMMUNITY_SERVER_SIGNING_KEY_HEX)
            pub_bytes = bytes.fromhex(self.COMMUNITY_SERVER_VERIFYING_KEY_HEX)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            priv_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
            derived_pub_hex = priv_key.public_key().public_bytes_raw().hex()
            if derived_pub_hex.lower() != self.COMMUNITY_SERVER_VERIFYING_KEY_HEX.lower():
                raise ValueError(
                    "COMMUNITY_SERVER_VERIFYING_KEY_HEX does not match the public key derived from COMMUNITY_SERVER_SIGNING_KEY_HEX"
                )
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid community cryptographic key configuration: {e}") from e

        return self


settings = Settings()
