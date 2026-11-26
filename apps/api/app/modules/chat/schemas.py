"""Pydantic schemas for Phase 4 Chat Persistence & Conversation Management."""

import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_clean_title(val: str) -> str:
    """Validate and sanitize a user-supplied conversation title."""
    if val is None:
        return val
    cleaned = val.strip()
    if not cleaned:
        raise ValueError("Conversation title cannot be empty or whitespace only")
    if len(cleaned) > 255:
        raise ValueError("Conversation title cannot exceed 255 characters")
    # Reject non-printable control characters except standard space
    for ch in cleaned:
        if ord(ch) < 32 and ch not in (" ", "\t"):
            raise ValueError("Conversation title contains invalid control characters")
        if unicodedata.category(ch).startswith("C") and ch not in (" ", "\t"):
            raise ValueError("Conversation title contains invalid control characters")
    return cleaned


class ConversationCreate(BaseModel):
    """Payload for creating a new conversation scoped to a subject."""

    subject_id: UUID = Field(..., description="Target academic subject UUID")
    title: str | None = Field(
        default=None,
        description="Optional initial title. Defaults to 'New conversation' if omitted.",
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_clean_title(v)


class ConversationRename(BaseModel):
    """Payload for renaming an existing conversation."""

    title: str = Field(..., description="New title for the conversation")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        return _validate_clean_title(v)


class ConversationStatusUpdate(BaseModel):
    """Payload for updating conversation status directly."""

    status: Literal["active", "archived"] = Field(
        ..., description="Target lifecycle status (active or archived)"
    )


class ConversationRead(BaseModel):
    """Public read projection for a conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    subject_id: UUID
    subject_name: str | None = None
    subject_slug: str | None = None
    title: str
    status: Literal["active", "archived", "deleted"]
    message_count: int
    last_message_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    items: list[ConversationRead]
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int


class MessageRead(BaseModel):
    """Public read projection for a persistent conversation message."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    sequence_number: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["pending", "streaming", "completed", "failed", "cancelled"]
    model_id: str | None = None
    provider_id: str | None = None
    finish_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageListResponse(BaseModel):
    """Paginated message history response."""

    items: list[MessageRead]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_more: bool = False
    total_count: int
