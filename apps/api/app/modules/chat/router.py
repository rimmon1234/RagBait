"""FastAPI router for Phase 4 persistent conversations and message history."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import (
    get_current_user,
    rate_limit_general,
    require_approved_user,
    require_chat_entitlement,
)
from app.core.security import AuthenticatedUser
from app.modules.chat.schemas import (
    ConversationCreate,
    ConversationListResponse,
    ConversationRead,
    ConversationRename,
    MessageListResponse,
)
from app.modules.chat.service import ChatService
from app.modules.users.schemas import UserProfile

chat_router = APIRouter(prefix="/chat", tags=["chat"])


@chat_router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_general)],
    summary="Create a new subject-scoped conversation",
)
async def create_conversation(
    body: ConversationCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_chat_entitlement),
) -> ConversationRead:
    """Create a new independent conversation bound to an academic subject."""
    return await ChatService.create_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        subject_id=body.subject_id,
        title=body.title,
    )


@chat_router.get(
    "/conversations",
    response_model=ConversationListResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="List conversations with cursor pagination",
)
async def list_conversations(
    subject_id: UUID | None = Query(None, description="Filter by academic subject UUID"),
    status_filter: Literal["active", "archived"] = Query(
        "active", alias="status", description="Filter by lifecycle status"
    ),
    limit: int = Query(20, ge=1, le=100, description="Page limit"),
    cursor: str | None = Query(None, description="Opaque pagination cursor"),
    search: str | None = Query(None, description="Search term filtering conversation titles"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> ConversationListResponse:
    """List authenticated user's conversations."""
    return await ChatService.list_conversations(
        user_id=current_user.user_id,
        claims=current_user.claims,
        subject_id=subject_id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
        search=search,
    )


@chat_router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    dependencies=[Depends(rate_limit_general)],
    summary="Retrieve conversation details",
)
async def get_conversation(
    conversation_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> ConversationRead:
    """Retrieve metadata for an owned conversation."""
    return await ChatService.get_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
    )


@chat_router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    dependencies=[Depends(rate_limit_general)],
    summary="Rename an owned conversation",
)
async def rename_conversation(
    conversation_id: UUID,
    body: ConversationRename,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> ConversationRead:
    """Update title of an owned conversation."""
    return await ChatService.rename_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
        title=body.title,
    )


@chat_router.post(
    "/conversations/{conversation_id}/archive",
    response_model=ConversationRead,
    dependencies=[Depends(rate_limit_general)],
    summary="Archive an active conversation",
)
async def archive_conversation(
    conversation_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> ConversationRead:
    """Archive conversation thread, removing it from default active list."""
    return await ChatService.archive_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
    )


@chat_router.post(
    "/conversations/{conversation_id}/restore",
    response_model=ConversationRead,
    dependencies=[Depends(rate_limit_general)],
    summary="Restore an archived conversation",
)
async def restore_conversation(
    conversation_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> ConversationRead:
    """Restore conversation thread back to active list."""
    return await ChatService.restore_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
    )


@chat_router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
    summary="Delete a conversation and message history",
)
async def delete_conversation(
    conversation_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> None:
    """Permanently delete an owned conversation thread."""
    await ChatService.delete_conversation(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
    )


@chat_router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Retrieve paginated message history",
)
async def get_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    cursor: int | None = Query(
        None, ge=1, description="Sequence number cursor for loading older messages"
    ),
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_approved_user),
) -> MessageListResponse:
    """Load paginated messages for an owned conversation."""
    return await ChatService.get_conversation_messages(
        user_id=current_user.user_id,
        claims=current_user.claims,
        conversation_id=conversation_id,
        limit=limit,
        cursor=cursor,
    )
