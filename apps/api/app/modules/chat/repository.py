"""Database access layer for conversations and messages."""

from datetime import datetime
from uuid import UUID

import asyncpg


class ChatRepository:
    """Asyncpg repository for persistent chat conversations and message transcripts."""

    @staticmethod
    async def get_subject_active_status(
        conn: asyncpg.Connection, subject_id: UUID
    ) -> tuple[bool, str, str] | None:
        """Fetch subject existence, is_active flag, name, and slug."""
        row = await conn.fetchrow(
            """
            SELECT is_active, name, slug
            FROM public.kb_subjects
            WHERE id = $1
            """,
            subject_id,
        )
        if not row:
            return None
        return (row["is_active"], row["name"], row["slug"])

    @staticmethod
    async def create_conversation(
        conn: asyncpg.Connection,
        user_id: UUID,
        subject_id: UUID,
        title: str,
    ) -> dict:
        """Create a new subject-scoped conversation atomically."""
        row = await conn.fetchrow(
            """
            WITH inserted AS (
                INSERT INTO public.chat_conversations (
                    user_id,
                    subject_id,
                    title,
                    status
                )
                VALUES ($1, $2, $3, 'active')
                RETURNING *
            )
            SELECT 
                i.id,
                i.user_id,
                i.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                i.title,
                i.status,
                i.message_count,
                i.last_message_at,
                i.archived_at,
                i.created_at,
                i.updated_at
            FROM inserted i
            JOIN public.kb_subjects s ON s.id = i.subject_id;
            """,
            user_id,
            subject_id,
            title,
        )
        return dict(row)

    @staticmethod
    async def get_conversation(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
    ) -> dict | None:
        """Retrieve conversation by ID enforcing user ownership."""
        row = await conn.fetchrow(
            """
            SELECT 
                c.id,
                c.user_id,
                c.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                c.title,
                c.status,
                c.message_count,
                c.last_message_at,
                c.archived_at,
                c.created_at,
                c.updated_at
            FROM public.chat_conversations c
            JOIN public.kb_subjects s ON s.id = c.subject_id
            WHERE c.id = $1
              AND c.user_id = $2
              AND c.status != 'deleted';
            """,
            conversation_id,
            user_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def list_conversations(
        conn: asyncpg.Connection,
        user_id: UUID,
        subject_id: UUID | None = None,
        status: str = "active",
        limit: int = 20,
        cursor_updated_at: datetime | None = None,
        cursor_id: UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], bool, int]:
        """List user-owned conversations with cursor pagination."""
        conditions = ["c.user_id = $1", "c.status = $2"]
        params: list[object] = [user_id, status]
        p_idx = 3

        if subject_id is not None:
            conditions.append(f"c.subject_id = ${p_idx}")
            params.append(subject_id)
            p_idx += 1

        if search:
            conditions.append(f"c.title ILIKE ${p_idx}")
            params.append(f"%{search}%")
            p_idx += 1

        # Count total for the given filters
        count_query = f"""
            SELECT count(*)::integer 
            FROM public.chat_conversations c
            WHERE {' AND '.join(conditions)};
        """
        total_count = await conn.fetchval(count_query, *params)

        if cursor_updated_at is not None and cursor_id is not None:
            conditions.append(f"(c.updated_at, c.id) < (${p_idx}, ${p_idx + 1})")
            params.append(cursor_updated_at)
            params.append(cursor_id)
            p_idx += 2

        query = f"""
            SELECT 
                c.id,
                c.user_id,
                c.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                c.title,
                c.status,
                c.message_count,
                c.last_message_at,
                c.archived_at,
                c.created_at,
                c.updated_at
            FROM public.chat_conversations c
            JOIN public.kb_subjects s ON s.id = c.subject_id
            WHERE {' AND '.join(conditions)}
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ${p_idx};
        """
        params.append(limit + 1)
        rows = await conn.fetch(query, *params)

        has_more = len(rows) > limit
        items = [dict(r) for r in rows[:limit]]
        return items, has_more, total_count or 0

    @staticmethod
    async def rename_conversation(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
        new_title: str,
    ) -> dict | None:
        """Rename an existing conversation enforcing ownership."""
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE public.chat_conversations
                SET title = $3,
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND status != 'deleted'
                RETURNING *
            )
            SELECT 
                u.id,
                u.user_id,
                u.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                u.title,
                u.status,
                u.message_count,
                u.last_message_at,
                u.archived_at,
                u.created_at,
                u.updated_at
            FROM updated u
            JOIN public.kb_subjects s ON s.id = u.subject_id;
            """,
            conversation_id,
            user_id,
            new_title,
        )
        return dict(row) if row else None

    @staticmethod
    async def archive_conversation(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
    ) -> dict | None:
        """Mark an active conversation as archived."""
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE public.chat_conversations
                SET status = 'archived',
                    archived_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND status = 'active'
                RETURNING *
            )
            SELECT 
                u.id,
                u.user_id,
                u.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                u.title,
                u.status,
                u.message_count,
                u.last_message_at,
                u.archived_at,
                u.created_at,
                u.updated_at
            FROM updated u
            JOIN public.kb_subjects s ON s.id = u.subject_id;
            """,
            conversation_id,
            user_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def restore_conversation(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
    ) -> dict | None:
        """Restore an archived conversation back to active."""
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE public.chat_conversations
                SET status = 'active',
                    archived_at = NULL,
                    updated_at = NOW()
                WHERE id = $1
                  AND user_id = $2
                  AND status = 'archived'
                RETURNING *
            )
            SELECT 
                u.id,
                u.user_id,
                u.subject_id,
                s.name AS subject_name,
                s.slug AS subject_slug,
                u.title,
                u.status,
                u.message_count,
                u.last_message_at,
                u.archived_at,
                u.created_at,
                u.updated_at
            FROM updated u
            JOIN public.kb_subjects s ON s.id = u.subject_id;
            """,
            conversation_id,
            user_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def delete_conversation(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Permanently delete an owned conversation (cascades to messages)."""
        res = await conn.execute(
            """
            DELETE FROM public.chat_conversations
            WHERE id = $1
              AND user_id = $2;
            """,
            conversation_id,
            user_id,
        )
        return res.endswith("1")

    @staticmethod
    async def get_messages(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> tuple[list[dict], bool, int] | None:
        """Retrieve paginated messages for a conversation in ascending order.
        
        Returns None if conversation does not exist or user is not the owner.
        """
        # Validate conversation ownership and get message count
        conv = await conn.fetchrow(
            """
            SELECT id, message_count
            FROM public.chat_conversations
            WHERE id = $1
              AND user_id = $2
              AND status != 'deleted';
            """,
            conversation_id,
            user_id,
        )
        if not conv:
            return None

        total_count = conv["message_count"]

        if before_sequence is not None:
            query = """
                SELECT id, conversation_id, sequence_number, role, content, status, created_at, updated_at
                FROM public.chat_messages
                WHERE conversation_id = $1
                  AND sequence_number < $2
                ORDER BY sequence_number DESC
                LIMIT $3;
            """
            rows = await conn.fetch(query, conversation_id, before_sequence, limit + 1)
        else:
            query = """
                SELECT id, conversation_id, sequence_number, role, content, status, created_at, updated_at
                FROM public.chat_messages
                WHERE conversation_id = $1
                ORDER BY sequence_number DESC
                LIMIT $2;
            """
            rows = await conn.fetch(query, conversation_id, limit + 1)

        has_more = len(rows) > limit
        trimmed = rows[:limit]
        # Reverse to chronological order (ascending)
        items = [dict(r) for r in reversed(trimmed)]
        return items, has_more, total_count

    @staticmethod
    async def insert_message_transactional(
        conn: asyncpg.Connection,
        conversation_id: UUID,
        user_id: UUID,
        role: str,
        content: str,
        status: str = "completed",
    ) -> dict:
        """Atomically allocate sequence number and insert message."""
        conv = await conn.fetchrow(
            """
            SELECT id, next_sequence_number, status
            FROM public.chat_conversations
            WHERE id = $1
              AND user_id = $2
            FOR UPDATE;
            """,
            conversation_id,
            user_id,
        )
        if not conv:
            raise ValueError("Conversation not found or not owned by caller")
        if conv["status"] != "active":
            raise ValueError(f"Cannot add messages to conversation in status '{conv['status']}'")

        allocated_seq = conv["next_sequence_number"]

        msg_row = await conn.fetchrow(
            """
            INSERT INTO public.chat_messages (
                conversation_id,
                sequence_number,
                role,
                content,
                status
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, conversation_id, sequence_number, role, content, status, created_at, updated_at;
            """,
            conversation_id,
            allocated_seq,
            role,
            content,
            status,
        )

        await conn.execute(
            """
            UPDATE public.chat_conversations
            SET next_sequence_number = next_sequence_number + 1,
                message_count = message_count + 1,
                last_message_at = NOW(),
                updated_at = NOW()
            WHERE id = $1;
            """,
            conversation_id,
        )

        return dict(msg_row)
