"""Schemas for LLM Providers, Models, Dynamic Quotas, and Registry Management."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
RoutingMode = Literal["auto", "cloud_only", "community_only", "provider_specific"]


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


# ==============================================================================
# Inference Gateway Schemas
# ==============================================================================
class ChatMessage(BaseModel):
    """Plain-text chat message."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=128000)


class GenerationParams(BaseModel):
    """Inference generation hyperparameters."""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)


class ChatCompletionRequest(BaseModel):
    """Unified request to dispatch text inference through the LLM gateway with intelligent routing."""

    routing_mode: RoutingMode = Field(
        default="provider_specific",
        description="Routing policy mode: 'auto', 'cloud_only', 'community_only', or 'provider_specific'",
    )
    provider_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        description="Target provider ID. Required when routing_mode is 'provider_specific'.",
    )
    model_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=128,
        description="Target model identifier. If omitted in 'auto' or 'cloud_only', server selects default.",
    )
    model: str | None = Field(
        default=None,
        description="Backward-compatible alias for model_id.",
    )
    credential_id: UUID | None = Field(
        default=None,
        description="Optional explicit credential ID. If omitted, user's active credential for this provider is used.",
    )
    community_opt_in: bool = Field(
        default=False,
        description="Explicit requester opt-in to consider peer community nodes for this request.",
    )
    community_consent_version: str | None = Field(
        default=None,
        description="Consent disclosure policy version (must match server version if community_opt_in is true).",
    )
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=100)
    params: GenerationParams = Field(default_factory=GenerationParams)
    stream: bool = Field(default=True, description="Whether to stream response via SSE")
    idempotency_key: str = Field(..., min_length=4, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def reconcile_routing_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Reconcile model and model_id
            if not data.get("model_id") and data.get("model"):
                data["model_id"] = data["model"]
            elif not data.get("model") and data.get("model_id"):
                data["model"] = data["model_id"]

            # Default routing mode determination
            if not data.get("routing_mode"):
                if data.get("provider_id"):
                    data["routing_mode"] = "provider_specific"
                else:
                    data["routing_mode"] = "auto"

            # Validate provider_specific requires provider_id
            if data.get("routing_mode") == "provider_specific" and not data.get("provider_id"):
                raise ValueError("provider_id is required when routing_mode is 'provider_specific'")

        return data


class ChatCompletionResponse(BaseModel):
    """Unified non-streaming generation response."""

    id: str
    request_id: str
    reservation_id: UUID
    provider_id: str
    model_id: str
    resolved_model_id: str
    upstream_provider: str
    upstream_generation_id: str | None = None
    content: str
    finish_reason: str = "stop"
    usage: dict[str, Any]
    cost_microcents: int = 0
    cost_cents: int = 0
    currency: str = "USD"
    created_at: datetime
