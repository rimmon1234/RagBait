"""Schemas and data structures for Unified Intelligent Inference Routing (M2.9)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

RoutingMode = Literal["auto", "cloud_only", "community_only", "provider_specific"]
SourceType = Literal["cloud", "community"]


class RoutingCandidate(BaseModel):
    """Unified internal candidate representation across cloud and community sources."""

    source_type: SourceType
    provider_id: str
    model_id: str
    display_name: str
    family: str
    release_stage: str = "ga"

    # Capabilities & Context
    capabilities: list[str] = Field(default_factory=list)
    context_window: int
    max_output_tokens: int
    is_routing_alias: bool = False
    alias_target: str | None = None

    # Cloud Properties (Safe Metadata ONLY - NO Secrets!)
    credential_id: UUID | None = None
    credential_owner_id: UUID | None = None
    is_shared_credential: bool = False
    pricing_input_cents_per_million: int | None = None
    pricing_output_cents_per_million: int | None = None
    pricing_provenance: str = "unknown"
    pricing_updated_at: datetime | None = None
    free_tier_eligible: bool = False

    # Community Properties (Safe Metadata ONLY - NO Contributor PII!)
    node_id: UUID | None = None
    node_display_name: str | None = None
    max_concurrent_jobs: int = 1
    active_concurrency: int = 0
    max_execution_duration: int = 300

    # Live Health & Telemetry
    is_cooling_down: bool = False
    cooldown_remaining_seconds: int = 0
    has_quota_capacity: bool = True


class CandidateFilterResult(BaseModel):
    """Result of hard filtering on an individual routing candidate."""

    candidate: RoutingCandidate
    eligible: bool
    rejection_code: str | None = None
    rejection_reason: str | None = None


class RouteDecision(BaseModel):
    """Authoritative decision metadata produced by the routing engine."""

    routing_mode: RoutingMode
    selected_source: SourceType
    provider_id: str
    model_id: str
    resolved_model_id: str | None = None
    credential_id: UUID | None = None
    node_id: UUID | None = None
    decision_reason: str
    estimated_cost_microcents: int | None = None
    attempt_count: int = 1
    created_at: datetime


class RoutePreviewResponse(BaseModel):
    """Safe client preview response describing prospective route selection."""

    routing_mode: RoutingMode
    selected_candidate: RoutingCandidate | None = None
    candidates_considered: int = 0
    candidates_eligible: int = 0
    decision_reason: str
    rejected_reasons_summary: dict[str, int] = Field(default_factory=dict)
