"""Presence tracking schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HeartbeatRequest(BaseModel):
    """Payload for presence heartbeat."""

    model_config = ConfigDict(extra="ignore")

    client_info: dict[str, Any] | None = Field(default_factory=dict)


class HeartbeatResponse(BaseModel):
    status: Literal["ok"]
    last_seen_at: datetime
    expires_at: datetime


class PresenceUserResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: str
    last_seen_at: datetime
    is_active: bool
    client_info: dict[str, Any]


class PresenceListResponse(BaseModel):
    active_users: list[PresenceUserResponse]
    total_active: int
