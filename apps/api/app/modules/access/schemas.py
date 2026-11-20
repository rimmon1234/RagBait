"""Access control schemas and state definitions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.users.schemas import ApprovalStatus, UserRole

EffectiveState = Literal[
    "unverified",
    "pending",
    "rejected",
    "suspended",
    "contribution_required",
    "admin",
]


class AccessStateResponse(BaseModel):
    """Effective access state returned to the client for routing."""

    effective_state: EffectiveState
    email_verified: bool
    role: UserRole
    approval_status: ApprovalStatus
    has_pending_request: bool


class AccessRequestResponse(BaseModel):
    """Access request record representation."""

    id: UUID
    user_id: UUID
    email: str | None = None
    display_name: str | None = None
    status: Literal["pending", "approved", "rejected"]
    decision_reason: str | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AccessDecisionRequest(BaseModel):
    """Schema for approving or rejecting an access request."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=500)


class PaginatedAccessRequestsResponse(BaseModel):
    items: list[AccessRequestResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
