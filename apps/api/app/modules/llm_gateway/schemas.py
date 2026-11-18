"""Schemas for LLM Providers, Models, Dynamic Quotas, and Registry Management."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Types & Literals
ProviderRuntimeType = Literal["cloud_api", "local_runtime"]
ProviderAccountTier = Literal["free", "pay_as_you_go", "enterprise", "standard"]
ProviderBillingStatus = Literal["active", "exhausted", "delinquent", "unknown"]
ModelAvailabilityStatus = Literal["active", "disabled", "deprecated", "shutdown"]
ModelReleaseStage = Literal["ga", "preview", "experimental", "deprecated"]
QuotaScopeType = Literal["credential", "project", "organization", "account", "node"]
QuotaLimitType = Literal["rpm", "tpm", "rpd", "tpd", "concurrency", "credit_cents"]
QuotaConfidence = Literal["high", "medium", "low"]
QuotaProvenance = Literal[
    "account_confirmed", "provider_published", "observed", "estimated", "unknown"
]


# Provider Schemas
class LLMProviderBase(BaseModel):
    name: str
    runtime_type: ProviderRuntimeType = "cloud_api"
    description: str | None = None
    is_enabled: bool = True
    requires_account_scope: bool = False
    allows_community_sharing: bool = False
    account_tier: ProviderAccountTier = "standard"
    billing_status: ProviderBillingStatus = "active"


class LLMProviderCreate(LLMProviderBase):
    id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")


class LLMProviderUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_enabled: bool | None = None
    requires_account_scope: bool | None = None
    account_tier: ProviderAccountTier | None = None
    billing_status: ProviderBillingStatus | None = None


class LLMProviderResponse(LLMProviderBase):
    id: str
    created_at: datetime
    updated_at: datetime


# Model Schemas
class LLMModelBase(BaseModel):
    family: str
    display_name: str
    release_stage: ModelReleaseStage = "ga"
    is_routing_alias: bool = False
    alias_target: str | None = None
    free_tier_eligible: bool = False
    input_cost_per_million_cents: int = Field(default=0, ge=0)
    output_cost_per_million_cents: int = Field(default=0, ge=0)
    capabilities: list[str] = Field(default_factory=lambda: ["streaming"])
    context_window: int = Field(..., gt=0)
    max_output_tokens: int = Field(..., gt=0)
    modalities_input: list[str] = Field(default_factory=lambda: ["text"])
    modalities_output: list[str] = Field(default_factory=lambda: ["text"])
    source_documentation: str | None = None


class LLMModelCreate(LLMModelBase):
    id: str = Field(..., min_length=2, max_length=128)
    provider_id: str


class LLMModelUpdate(BaseModel):
    display_name: str | None = None
    family: str | None = None
    availability_status: ModelAvailabilityStatus | None = None
    release_stage: ModelReleaseStage | None = None
    is_routing_alias: bool | None = None
    alias_target: str | None = None
    free_tier_eligible: bool | None = None
    input_cost_per_million_cents: int | None = Field(default=None, ge=0)
    output_cost_per_million_cents: int | None = Field(default=None, ge=0)
    capabilities: list[str] | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    modalities_input: list[str] | None = None
    modalities_output: list[str] | None = None
    source_documentation: str | None = None
    shutdown_at: datetime | None = None
    replacement_model_id: str | None = None


class LLMModelResponse(LLMModelBase):
    id: str
    provider_id: str
    availability_status: ModelAvailabilityStatus
    is_active: bool
    last_verification_time: datetime
    shutdown_at: datetime | None = None
    replacement_model_id: str | None = None
    created_at: datetime
    updated_at: datetime


class LLMModelListResponse(BaseModel):
    items: list[LLMModelResponse]
    total: int


# Quota Schemas
class ProviderQuotaBase(BaseModel):
    scope_type: QuotaScopeType
    scope_id: str
    model_id: str | None = None
    limit_type: QuotaLimitType
    limit_value: int = Field(..., gt=0)
    source_documentation: str | None = None
    confidence: QuotaConfidence = "medium"
    provenance: QuotaProvenance = "provider_published"
    is_active: bool = True


class ProviderQuotaCreate(ProviderQuotaBase):
    provider_id: str


class ProviderQuotaUpdate(BaseModel):
    limit_value: int | None = Field(default=None, gt=0)
    source_documentation: str | None = None
    confidence: QuotaConfidence | None = None
    provenance: QuotaProvenance | None = None
    is_active: bool | None = None


class ProviderQuotaResponse(ProviderQuotaBase):
    id: UUID
    provider_id: str
    last_verification_time: datetime
    created_at: datetime
    updated_at: datetime


class ProviderQuotaListResponse(BaseModel):
    items: list[ProviderQuotaResponse]
    total: int


# Discovery & Synchronization
class ProviderSyncResult(BaseModel):
    provider_id: str
    models_discovered: int
    models_active: int
    models_deprecated: int
    unverified_discovered: list[str] = Field(default_factory=list)
    details: list[dict[str, Any]] = Field(default_factory=list)


class ProviderSyncRequest(BaseModel):
    discovered_models: list[dict[str, Any]] | None = None


class ResolvedModelInfo(BaseModel):
    requested_model_id: str
    is_routing_alias: bool
    resolved_model_id: str
    upstream_provider: str
