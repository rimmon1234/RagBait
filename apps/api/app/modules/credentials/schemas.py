"""Schemas for Encrypted Credential Vault."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CredentialStatus = Literal["active", "disabled", "revoked", "rate_limited", "quota_exhausted"]
CredentialSharingScope = Literal["owner_only", "community_shared"]


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
    status: CredentialStatus = "active"
    sharing_scope: CredentialSharingScope = "owner_only"
    shared_consent: bool = False
    sharing_consent_timestamp: datetime | None = None
    account_scope_id: str | None = None
    daily_request_limit: int = 500
    monthly_request_limit: int = 15000
    daily_spending_limit_cents: int | None = None
    monthly_spending_limit_cents: int | None = None
    created_at: datetime
    updated_at: datetime


class StoredCredentialResult(BaseModel):
    """Result returned after storing an encrypted credential (never contains raw key)."""

    id: UUID
    owner_id: UUID
    provider_id: str
    nickname: str
    masked_key: str
    key_fingerprint: str
    status: CredentialStatus
    sharing_scope: CredentialSharingScope
    shared_consent: bool
    account_scope_id: str | None = None
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
    data_encryption_version: int = 1
    kek_version: int = 1
    wrapping_context_version: int = 1

    @property
    def key_version(self) -> int:
        """Compatibility property for KEK version."""
        return self.kek_version
