"""Service layer for chat persistence, conversation lifecycle, and message history."""

import base64
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.database import get_pool, get_user_db_session
from app.core.errors import BadRequestError, NotFoundError
from app.modules.chat.repository import ChatRepository
from app.modules.chat.schemas import (
    ConversationListResponse,
    ConversationRead,
    MessageListResponse,
    MessageRead,
)

logger = logging.getLogger("ragbait.chat")


def _encode_cursor(updated_at: datetime, conv_id: UUID) -> str:
    """Encode conversation pagination cursor into an opaque URL-safe string."""
    raw = f"{updated_at.isoformat()}|{conv_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor_str: str) -> tuple[datetime, UUID]:
    """Decode an opaque conversation pagination cursor."""
    try:
        decoded = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) != 2:
            raise ValueError("Malformed cursor payload")
        dt = datetime.fromisoformat(parts[0])
        cid = UUID(parts[1])
        return dt, cid
    except Exception as exc:
        raise BadRequestError(
            message="Invalid pagination cursor format",
            code="INVALID_CURSOR",
        ) from exc


class ChatService:
    """Orchestrates conversation and message business logic and authorization."""

    @staticmethod
    async def create_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        subject_id: UUID,
        title: str | None = None,
    ) -> ConversationRead:
        """Create a new conversation scoped to an active academic subject."""
        pool = get_pool()
        async with pool.acquire() as sys_conn:
            subject_meta = await ChatRepository.get_subject_active_status(sys_conn, subject_id)
            if not subject_meta:
                raise NotFoundError(
                    message="Target academic subject not found",
                    code="SUBJECT_NOT_FOUND",
                )

            is_active, subject_name, _ = subject_meta
            if not is_active:
                raise BadRequestError(
                    message="Cannot create a conversation in an inactive or archived subject",
                    code="SUBJECT_INACTIVE",
                )

        resolved_title = title
        if not resolved_title:
            resolved_title = f"New conversation — {subject_name}"

        async with get_user_db_session(user_id, claims) as conn:
            row = await ChatRepository.create_conversation(
                conn=conn,
                user_id=user_id,
                subject_id=subject_id,
                title=resolved_title,
            )
            return ConversationRead.model_validate(row)

    @staticmethod
    async def get_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
    ) -> ConversationRead:
        """Retrieve an owned conversation by ID."""
        async with get_user_db_session(user_id, claims) as conn:
            row = await ChatRepository.get_conversation(conn, conversation_id, user_id)
            if not row:
                raise NotFoundError(
                    message="Conversation not found",
                    code="CONVERSATION_NOT_FOUND",
                )
            return ConversationRead.model_validate(row)

    @staticmethod
    async def list_conversations(
        user_id: UUID,
        claims: dict[str, Any],
        subject_id: UUID | None = None,
        status: str = "active",
        limit: int = 20,
        cursor: str | None = None,
        search: str | None = None,
    ) -> ConversationListResponse:
        """List user-owned conversations with stable cursor pagination."""
        if limit < 1 or limit > 100:
            raise BadRequestError(
                message="Limit must be between 1 and 100",
                code="INVALID_PAGE_SIZE",
            )

        cursor_updated_at: datetime | None = None
        cursor_id: UUID | None = None
        if cursor:
            cursor_updated_at, cursor_id = _decode_cursor(cursor)

        async with get_user_db_session(user_id, claims) as conn:
            items, has_more, total_count = await ChatRepository.list_conversations(
                conn=conn,
                user_id=user_id,
                subject_id=subject_id,
                status=status,
                limit=limit,
                cursor_updated_at=cursor_updated_at,
                cursor_id=cursor_id,
                search=search,
            )

        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last["updated_at"], last["id"])

        return ConversationListResponse(
            items=[ConversationRead.model_validate(i) for i in items],
            next_cursor=next_cursor,
            has_more=has_more,
            total_count=total_count,
        )

    @staticmethod
    async def rename_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
        title: str,
    ) -> ConversationRead:
        """Rename an owned conversation."""
        async with get_user_db_session(user_id, claims) as conn:
            row = await ChatRepository.rename_conversation(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
                new_title=title,
            )
            if not row:
                raise NotFoundError(
                    message="Conversation not found",
                    code="CONVERSATION_NOT_FOUND",
                )
            return ConversationRead.model_validate(row)

    @staticmethod
    async def archive_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
    ) -> ConversationRead:
        """Archive an active owned conversation (idempotent if already archived)."""
        async with get_user_db_session(user_id, claims) as conn:
            row = await ChatRepository.archive_conversation(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if not row:
                # Check if it exists already in archived status
                existing = await ChatRepository.get_conversation(conn, conversation_id, user_id)
                if not existing:
                    raise NotFoundError(
                        message="Conversation not found",
                        code="CONVERSATION_NOT_FOUND",
                    )
                return ConversationRead.model_validate(existing)
            return ConversationRead.model_validate(row)

    @staticmethod
    async def restore_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
    ) -> ConversationRead:
        """Restore an archived conversation back to active (idempotent if already active)."""
        async with get_user_db_session(user_id, claims) as conn:
            row = await ChatRepository.restore_conversation(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if not row:
                existing = await ChatRepository.get_conversation(conn, conversation_id, user_id)
                if not existing:
                    raise NotFoundError(
                        message="Conversation not found",
                        code="CONVERSATION_NOT_FOUND",
                    )
                return ConversationRead.model_validate(existing)
            return ConversationRead.model_validate(row)

    @staticmethod
    async def delete_conversation(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
    ) -> None:
        """Permanently delete an owned conversation and its cascade messages."""
        async with get_user_db_session(user_id, claims) as conn:
            deleted = await ChatRepository.delete_conversation(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if not deleted:
                raise NotFoundError(
                    message="Conversation not found",
                    code="CONVERSATION_NOT_FOUND",
                )

    @staticmethod
    async def get_conversation_messages(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
        limit: int = 50,
        cursor: int | None = None,
    ) -> MessageListResponse:
        """Fetch paginated message history for an owned conversation."""
        if limit < 1 or limit > 100:
            raise BadRequestError(
                message="Limit must be between 1 and 100",
                code="INVALID_PAGE_SIZE",
            )
        if cursor is not None and cursor < 1:
            raise BadRequestError(
                message="Sequence cursor must be a positive integer",
                code="INVALID_CURSOR",
            )

        async with get_user_db_session(user_id, claims) as conn:
            res = await ChatRepository.get_messages(
                conn=conn,
                conversation_id=conversation_id,
                user_id=user_id,
                limit=limit,
                before_sequence=cursor,
            )
            if res is None:
                raise NotFoundError(
                    message="Conversation not found",
                    code="CONVERSATION_NOT_FOUND",
                )

            items, has_more, total_count = res

        next_cursor = None
        prev_cursor = None
        if items:
            prev_cursor = str(items[-1]["sequence_number"])
            if has_more:
                next_cursor = str(items[0]["sequence_number"])

        return MessageListResponse(
            items=[MessageRead.model_validate(m) for m in items],
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            total_count=total_count,
        )

    @staticmethod
    async def create_message_internal(
        user_id: UUID,
        claims: dict[str, Any],
        conversation_id: UUID,
        role: str,
        content: str,
        status: str = "completed",
    ) -> MessageRead:
        """Internal message-insertion helper with sequence allocation."""
        async with get_user_db_session(user_id, claims) as conn:
            try:
                row = await ChatRepository.insert_message_transactional(
                    conn=conn,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=role,
                    content=content,
                    status=status,
                )
                return MessageRead.model_validate(row)
            except ValueError as val_err:
                raise BadRequestError(
                    message=str(val_err),
                    code="MESSAGE_INSERT_ERROR",
                ) from val_err
