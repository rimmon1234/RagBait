"""Administrative management schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.users.schemas import ApprovalStatus, UserRole


class OverviewResponse(BaseModel):
    """Platform overview metric counters."""

    total_users: int
    pending_requests: int
    approved_users: int
    suspended_users: int
    rejected_users: int
    active_users_now: int
    active_users_recent: int


class RoleUpdateRequest(BaseModel):
    """Schema for updating a user's role."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole


class StatusUpdateRequest(BaseModel):
    """Schema for updating a user's account approval status."""

    model_config = ConfigDict(extra="forbid")

    approval_status: ApprovalStatus
    reason: str | None = Field(None, max_length=500)


class AuditLogResponse(BaseModel):
    """Representation of an administrative audit record."""

    id: UUID
    actor_id: UUID | None = None
    action: str
    target_type: str
    target_id: UUID | None = None
    payload: dict[str, Any]
    created_at: datetime


class PaginatedAuditLogsResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
