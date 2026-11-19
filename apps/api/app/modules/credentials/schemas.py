"""Schemas for Encrypted Credential Vault and Management."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CredentialStatus = Literal["active", "disabled", "revoked", "rate_limited", "quota_exhausted"]
CredentialSharingScope = Literal["owner_only", "community_shared"]
ValidationStatus = Literal[
    "unvalidated",
    "valid",
    "invalid_credential",
    "insufficient_permission",
    "rate_limited",
    "provider_unavailable",
    "unsupported",
    "validation_error",
]


class EncryptedSecretRecord(BaseModel):
    """Internal model for encrypted payload stored in private.api_key_secrets."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID
    encrypted_dek: bytes
    dek_nonce: bytes
    dek_auth_tag: bytes
    ciphertext: bytes
    nonce: bytes
    data_encryption_version: int = 1
    kek_version: int = 1
    wrapping_context_version: int = 1
    created_at: datetime
    updated_at: datetime


class ApiKeyMetadataRecord(BaseModel):
    """Internal model for credential metadata stored in public.api_key_metadata."""

    id: UUID
    owner_id: UUID
    provider_id: str
    nickname: str
    masked_key: str
    key_fingerprint: str
    status: CredentialStatus = "disabled"
    sharing_scope: CredentialSharingScope = "owner_only"
    shared_consent: bool = False
    sharing_consent_timestamp: datetime | None = None
    consent_version: str | None = None
    validation_status: ValidationStatus = "unvalidated"
    last_validated_at: datetime | None = None
    validation_error_code: str | None = None
    account_scope_id: str | None = None
    account_scope_verified: bool = False
    account_scope_source: str | None = None
    account_scope_verified_at: datetime | None = None
    daily_request_limit: int = 500
    monthly_request_limit: int = 15000
    daily_spending_limit_cents: int | None = None
    monthly_spending_limit_cents: int | None = None
    created_at: datetime
    updated_at: datetime


class StoredCredentialResult(BaseModel):
    """Result returned after storing an encrypted credential in vault."""

    id: UUID
    owner_id: UUID
    provider_id: str
    nickname: str
    masked_key: str
    key_fingerprint: str
    status: CredentialStatus
    sharing_scope: CredentialSharingScope
    shared_consent: bool
    sharing_consent_timestamp: datetime | None = None
    consent_version: str | None = None
    validation_status: ValidationStatus = "unvalidated"
    last_validated_at: datetime | None = None
    validation_error_code: str | None = None
    account_scope_id: str | None = None
    account_scope_verified: bool = False
    account_scope_source: str | None = None
    account_scope_verified_at: datetime | None = None
    daily_request_limit: int
    monthly_request_limit: int
    daily_spending_limit_cents: int | None = None
    monthly_spending_limit_cents: int | None = None
    data_encryption_version: int = 1
    kek_version: int = 1
    created_at: datetime
    updated_at: datetime


class DecryptedCredential(BaseModel):
    """Decrypted credential secret passed strictly to authorized internal adapters."""

    id: UUID
    owner_id: UUID
    provider_id: str
    raw_key: str = Field(repr=False)  # Never print in __repr__
    masked_key: str
    status: CredentialStatus
    sharing_scope: CredentialSharingScope
    shared_consent: bool
    account_scope_id: str | None = None
    account_scope_verified: bool = False
    account_scope_source: str | None = None
    account_scope_verified_at: datetime | None = None
    data_encryption_version: int = 1
    kek_version: int = 1
    wrapping_context_version: int = 1

    @property
    def key_version(self) -> int:
        """Compatibility property for KEK version."""
        return self.kek_version


# ==============================================================================
# User-Facing Request & Response Schemas (M2.3)
# ==============================================================================


class CredentialCreateRequest(BaseModel):
    """Request payload to submit a new API key."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(..., min_length=1, max_length=64, description="Provider identifier")
    nickname: str = Field(
        ..., min_length=1, max_length=64, description="User-friendly key nickname"
    )
    api_key: str = Field(
        ..., min_length=1, max_length=512, description="Plaintext API key material", repr=False
    )
    sharing_scope: CredentialSharingScope = Field(
        default="owner_only", description="Sharing preference (owner_only or community_shared)"
    )
    shared_consent: bool = Field(
        default=False, description="Explicit consent if community_shared is selected"
    )
    daily_request_limit: int = Field(
        default=500, ge=1, le=1000000, description="Configured daily request limit"
    )
    monthly_request_limit: int = Field(
        default=15000, ge=1, le=50000000, description="Configured monthly request limit"
    )
    daily_spending_limit_cents: int | None = Field(
        default=None, ge=0, le=10000000, description="Optional daily spending budget in cents"
    )
    monthly_spending_limit_cents: int | None = Field(
        default=None, ge=0, le=100000000, description="Optional monthly spending budget in cents"
    )


class CredentialUpdateRequest(BaseModel):
    """Request payload to update permitted credential metadata and limits."""

    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(
        default=None, min_length=1, max_length=64, description="Updated nickname"
    )
    sharing_scope: CredentialSharingScope | None = Field(
        default=None, description="Updated sharing preference"
    )
    shared_consent: bool | None = Field(
        default=None, description="Updated explicit community consent"
    )
    daily_request_limit: int | None = Field(
        default=None, ge=1, le=1000000, description="Updated daily request limit"
    )
    monthly_request_limit: int | None = Field(
        default=None, ge=1, le=50000000, description="Updated monthly request limit"
    )
    daily_spending_limit_cents: int | None = Field(
        default=None, ge=0, le=10000000, description="Updated daily spending budget in cents"
    )
    monthly_spending_limit_cents: int | None = Field(
        default=None, ge=0, le=100000000, description="Updated monthly spending budget in cents"
    )


class CredentialResponse(BaseModel):
    """Safe public representation of an API credential metadata record.

    NEVER discloses plaintext key, ciphertext, nonces, tags, DEK, or fingerprint.
    """

    id: UUID
    owner_id: UUID
    provider_id: str
    provider_name: str | None = None
    nickname: str
    masked_key: str
    status: CredentialStatus
    sharing_scope: CredentialSharingScope
    shared_consent: bool
    sharing_consent_timestamp: datetime | None = None
    consent_version: str | None = None
    validation_status: ValidationStatus
    last_validated_at: datetime | None = None
    validation_error_code: str | None = None
    account_scope_id: str | None = None
    account_scope_verified: bool = False
    account_scope_source: str | None = None
    account_scope_verified_at: datetime | None = None
    daily_request_limit: int
    monthly_request_limit: int
    daily_spending_limit_cents: int | None = None
    monthly_spending_limit_cents: int | None = None
    created_at: datetime
    updated_at: datetime


class CredentialListResponse(BaseModel):
    """List of credentials owned by the authenticated contributor."""

    items: list[CredentialResponse]
    total: int


class ValidationResultResponse(BaseModel):
    """Result of an on-demand provider authentication probe."""

    credential_id: UUID
    provider_id: str
    outcome: ValidationStatus
    safe_code: str
    message: str
    validated_at: datetime
