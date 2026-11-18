"""Schemas for Admission Control, Concurrency Leases, and Usage Ledger."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReservationStatus = Literal[
    "reserved",
    "dispatched",
    "settled",
    "released",
    "outcome_unknown",
    "reconciliation_required",
    "expired",
]

UsageEventType = Literal[
    "reservation_created",
    "dispatch_committed",
    "reservation_released",
    "usage_settled",
    "outcome_unknown",
    "usage_reconciled",
    "reservation_expired",
]


class AdmissionRequest(BaseModel):
    """Input parameters to request inference admission."""

    user_id: UUID
    credential_id: UUID
    provider_id: str
    model_id: str
    estimated_input_tokens: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    idempotency_key: str = Field(..., min_length=4, max_length=128)
    request_id: str = Field(..., min_length=4, max_length=128)
    account_scope_id: str | None = None


class AdmissionReservation(BaseModel):
    """Result of a successful admission check and multi-tier reservation."""

    reservation_id: UUID
    lease_token: str
    lease_expires_at: datetime
    user_id: UUID
    credential_id: UUID
    provider_id: str
    model_id: str
    status: ReservationStatus
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_cents: int
    currency: str = "USD"
    created_at: datetime


class LeaseRenewalResult(BaseModel):
    """Result of extending a concurrency lease."""

    reservation_id: UUID
    lease_token: str
    lease_expires_at: datetime
    is_active: bool


class SettlementRequest(BaseModel):
    """Actual usage metrics supplied upon operation completion."""

    actual_input_tokens: int = Field(..., ge=0)
    actual_output_tokens: int = Field(..., ge=0)
    actual_cost_cents: int | None = Field(default=None, ge=0)
    error_category: str | None = None


class SettlementResult(BaseModel):
    """Result of settling usage and releasing temporary budget reservations."""

    reservation_id: UUID
    status: ReservationStatus
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cost_cents: int
    currency: str = "USD"
    settled_at: datetime


class ReleaseResult(BaseModel):
    """Result of cancelling/releasing an uncommitted reservation."""

    reservation_id: UUID
    status: ReservationStatus
    released_at: datetime
    reason: str


class ReservationView(BaseModel):
    """Safe, masked view of an individual reservation for contributor inspection."""

    id: UUID
    request_id: str
    provider_id: str
    model_id: str
    status: ReservationStatus
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_cents: int
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    actual_cost_cents: int | None = None
    currency: str = "USD"
    created_at: datetime
    dispatched_at: datetime | None = None
    settled_at: datetime | None = None


class UsageSummaryResponse(BaseModel):
    """Aggregated usage summary for the authenticated contributor."""

    total_dispatched_requests: int
    total_settled_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_actual_cost_cents: int
    total_reserved_cost_cents: int
    daily_request_usage: int
    daily_request_limit: int
    monthly_request_usage: int
    monthly_request_limit: int
    daily_spending_cents: int
    daily_spending_budget_cents: int | None
    monthly_spending_cents: int
    monthly_spending_budget_cents: int | None
    unknown_outcome_count: int
    recent_reservations: list[ReservationView] = Field(default_factory=list)
