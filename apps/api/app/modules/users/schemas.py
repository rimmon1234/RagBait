"""User domain schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

UserRole = Literal["user", "admin", "super_admin"]
ApprovalStatus = Literal["pending", "approved", "rejected", "suspended"]


class UserProfile(BaseModel):
    """Domain model representing a user's database profile."""

    id: UUID
    email: str
    display_name: str
    role: UserRole
    approval_status: ApprovalStatus
    created_at: datetime
    updated_at: datetime


class ProfileUpdateRequest(BaseModel):
    """Schema for updating mutable profile fields.

    Strictly forbids modifying role, approval_status, email, or id.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=100)


class UserAdminResponse(BaseModel):
    """Sanitized user representation for administrative management."""

    id: UUID
    email: str
    display_name: str
    role: UserRole
    approval_status: ApprovalStatus
    created_at: datetime
    updated_at: datetime


class PaginatedUsersResponse(BaseModel):
    items: list[UserAdminResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
