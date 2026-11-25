"""Transactional persistence helpers for chat turns, source snapshots, and Private Local state."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.modules.chat.citations.source_repository import insert_included_source_snapshots
from app.modules.chat.context.schemas import SelectedEvidenceSource

logger = logging.getLogger("ragbait.chat.orchestration.persistence")


async def lookup_existing_turn_by_idempotency(
    conn: asyncpg.Connection,
    *,
    conversation_id: UUID,
    idempotency_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch existing user message and paired assistant message matching an idempotency key."""
    user_row = await conn.fetchrow(
        """
        SELECT id, conversation_id, sequence_number, role, content, status,
               idempotency_key, created_at, updated_at
        FROM public.chat_messages
        WHERE conversation_id = $1
          AND role = 'user'
          AND idempotency_key = $2
        """,
        conversation_id,
        idempotency_key,
    )
    if not user_row:
        return None, None

    asst_row = await conn.fetchrow(
        """
        SELECT id, conversation_id, sequence_number, role, content, status,
               generation_request_id, idempotency_key, model_id, provider_id,
               token_usage, finish_reason, error_message, metadata, created_at, updated_at
        FROM public.chat_messages
        WHERE conversation_id = $1
          AND role = 'assistant'
          AND (
            idempotency_key = $2
            OR metadata->>'user_idempotency_key' = $2
            OR sequence_number = $3
          )
        ORDER BY sequence_number DESC
        LIMIT 1
        """,
        conversation_id,
        idempotency_key,
        int(user_row["sequence_number"]) + 1,
    )
    return dict(user_row), (dict(asst_row) if asst_row else None)


async def reserve_turn_messages(
    conn: asyncpg.Connection,
    *,
    conversation_id: UUID,
    user_id: UUID,
    user_content: str,
    idempotency_key: str,
    generation_request_id: str,
    mode: str,
    initial_status: str = "pending",
    model_id: str | None = None,
    provider_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    selected_evidence: list[SelectedEvidenceSource] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically reserve sequential user + pending assistant message pair.

    If `selected_evidence` is passed (for `prepare-local`), persists Stage-3 snapshots
    inside the exact same transaction.
    """
    conv = await conn.fetchrow(
        """
        SELECT id, user_id, subject_id, status, next_sequence_number
        FROM public.chat_conversations
        WHERE id = $1
        FOR UPDATE;
        """,
        conversation_id,
    )
    if not conv or conv["status"] == "deleted":
        raise NotFoundError("Conversation not found", code="CONVERSATION_NOT_FOUND")
    if conv["user_id"] != user_id:
        raise NotFoundError("Conversation not found", code="CONVERSATION_NOT_FOUND")
    if conv["status"] != "active":
        raise ConflictError(
            f"Cannot generate messages in an {conv['status']} conversation",
            code="CONVERSATION_NOT_ACTIVE",
        )

    # Check if user message already exists for this idempotency key (retry after prior failure)
    existing_user, existing_asst = await lookup_existing_turn_by_idempotency(
        conn, conversation_id=conversation_id, idempotency_key=idempotency_key
    )
    if existing_user:
        if existing_user["content"].strip() != user_content.strip():
            raise ConflictError(
                f"Idempotency key '{idempotency_key}' already used with different content",
                code="IDEMPOTENCY_KEY_CONFLICT",
            )
        if existing_asst and existing_asst["status"] == "completed":
            raise ConflictError(
                "Generation for this idempotency key is already completed",
                code="ALREADY_COMPLETED",
            )
        if existing_asst and existing_asst["status"] in ("pending", "streaming"):
            raise ConflictError(
                "Generation for this idempotency key is already in progress",
                code="GENERATION_IN_PROGRESS",
            )
        # Recoverable retry of a prior failed assistant turn without duplicating user message!
        next_seq = int(conv["next_sequence_number"])
        asst_meta = {
            "mode": mode,
            "user_idempotency_key": idempotency_key,
            "stage": "generating",
            **(extra_metadata or {}),
        }
        asst_row = await conn.fetchrow(
            """
            INSERT INTO public.chat_messages (
                conversation_id, sequence_number, role, content, status,
                generation_request_id, idempotency_key, model_id, provider_id, metadata
            )
            VALUES ($1, $2, 'assistant', '', $8::public.chat_message_status, $3, $4, $5, $6, $7::jsonb)
            RETURNING *;
            """,
            conversation_id,
            next_seq,
            generation_request_id,
            f"retry-{generation_request_id}",
            model_id,
            provider_id,
            json.dumps(asst_meta),
            initial_status,
        )
        await conn.execute(
            """
            UPDATE public.chat_conversations
            SET next_sequence_number = next_sequence_number + 1,
                message_count = message_count + 1,
                last_message_at = timezone('utc'::text, now()),
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            conversation_id,
        )
        if selected_evidence:
            await insert_included_source_snapshots(
                conn, message_id=asst_row["id"], selected_evidence=selected_evidence
            )
        return existing_user, dict(asst_row)

    seq_user = int(conv["next_sequence_number"])
    seq_asst = seq_user + 1

    try:
        user_row = await conn.fetchrow(
            """
            INSERT INTO public.chat_messages (
                conversation_id, sequence_number, role, content, status, idempotency_key
            )
            VALUES ($1, $2, 'user', $3, 'completed', $4)
            RETURNING *;
            """,
            conversation_id,
            seq_user,
            user_content,
            idempotency_key,
        )
    except asyncpg.UniqueViolationError as uve:
        raise ConflictError(
            "Generation for this idempotency key is already in progress",
            code="GENERATION_IN_PROGRESS",
        ) from uve

    asst_meta = {
        "mode": mode,
        "user_idempotency_key": idempotency_key,
        "stage": initial_status if mode == "cloud" else "prepared_local",
        **(extra_metadata or {}),
    }
    asst_row = await conn.fetchrow(
        """
        INSERT INTO public.chat_messages (
            conversation_id, sequence_number, role, content, status,
            generation_request_id, idempotency_key, model_id, provider_id, metadata
        )
        VALUES ($1, $2, 'assistant', '', $8::public.chat_message_status, $3, $4, $5, $6, $7::jsonb)
        RETURNING *;
        """,
        conversation_id,
        seq_asst,
        generation_request_id,
        idempotency_key,
        model_id,
        provider_id,
        json.dumps(asst_meta),
        initial_status,
    )

    await conn.execute(
        """
        UPDATE public.chat_conversations
        SET next_sequence_number = next_sequence_number + 2,
            message_count = message_count + 2,
            last_message_at = timezone('utc'::text, now()),
            updated_at = timezone('utc'::text, now())
        WHERE id = $1;
        """,
        conversation_id,
    )

    if selected_evidence:
        await insert_included_source_snapshots(
            conn, message_id=asst_row["id"], selected_evidence=selected_evidence
        )

    return dict(user_row), dict(asst_row)


async def complete_cloud_turn_atomically(
    conn: asyncpg.Connection,
    *,
    assistant_message_id: UUID,
    conversation_id: UUID,
    final_content: str,
    model_id: str,
    provider_id: str,
    token_usage: dict[str, Any],
    finish_reason: str,
    selected_evidence: list[SelectedEvidenceSource],
    referenced_keys: set[str],
    extra_metadata: dict[str, Any],
    expected_generation_request_id: str | None = None,
) -> dict[str, Any]:
    """Atomically persist Stage-3/4 source snapshots and complete the assistant message."""
    async with conn.transaction():
        msg_row = await conn.fetchrow(
            """
            UPDATE public.chat_messages
            SET content = $2,
                status = 'completed',
                model_id = $3,
                provider_id = $4,
                token_usage = $5::jsonb,
                finish_reason = $6,
                error_message = NULL,
                metadata = metadata || $7::jsonb,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1
              AND status IN ('pending', 'streaming')
              AND ($8::text IS NULL OR generation_request_id = $8)
            RETURNING *;
            """,
            assistant_message_id,
            final_content,
            model_id,
            provider_id,
            json.dumps(token_usage),
            finish_reason,
            json.dumps({"stage": "completed", **extra_metadata}),
            expected_generation_request_id,
        )
        if not msg_row:
            raise ConflictError(
                "Assistant message could not be finalized because it is no longer active or was cancelled",
                code="MESSAGE_NOT_ACTIVE",
            )

        await insert_included_source_snapshots(
            conn,
            message_id=assistant_message_id,
            selected_evidence=selected_evidence,
            referenced_keys=referenced_keys,
        )

        await conn.execute(
            """
            UPDATE public.chat_conversations
            SET last_message_at = timezone('utc'::text, now()),
                updated_at = timezone('utc'::text, now())
            WHERE id = $1;
            """,
            conversation_id,
        )
        return dict(msg_row)


async def mark_assistant_message_failed(
    conn: asyncpg.Connection,
    *,
    assistant_message_id: UUID,
    error_code: str,
    error_message: str,
    expected_generation_request_id: str | None = None,
) -> None:
    """Transition an active pending/streaming assistant message cleanly to failed."""
    safe_msg = (error_message or "Generation failed").strip()[:500]
    await conn.execute(
        """
        UPDATE public.chat_messages
        SET status = 'failed',
            error_message = $2,
            metadata = metadata || $3::jsonb,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
          AND status IN ('pending', 'streaming')
          AND ($4::text IS NULL OR generation_request_id = $4);
        """,
        assistant_message_id,
        safe_msg,
        json.dumps({"stage": "failed", "error_code": error_code}),
        expected_generation_request_id,
    )


async def mark_assistant_message_cancelled(
    conn: asyncpg.Connection,
    *,
    assistant_message_id: UUID,
    partial_content: str = "",
    reason: str = "user_requested",
    expected_generation_request_id: str | None = None,
) -> int:
    """Persist partial text cleanly as 'cancelled' without overwriting terminal completed/failed rows."""
    safe_partial = (partial_content or "")[:32768]
    row = await conn.fetchrow(
        """
        UPDATE public.chat_messages
        SET content = CASE
                WHEN char_length(trim(content)) > char_length(trim($2)) THEN content
                ELSE $2
            END,
            status = 'cancelled',
            finish_reason = 'cancelled',
            metadata = metadata || $3::jsonb,
            updated_at = timezone('utc'::text, now())
        WHERE id = $1
          AND status IN ('pending', 'streaming', 'cancelled')
          AND ($4::text IS NULL OR generation_request_id = $4)
        RETURNING char_length(content) AS retained_length;
        """,
        assistant_message_id,
        safe_partial,
        json.dumps({"stage": "cancelled", "cancel_reason": reason}),
        expected_generation_request_id,
    )
    return int(row["retained_length"] or 0) if row else 0


async def lock_and_verify_prepared_local_message(
    conn: asyncpg.Connection,
    *,
    message_id: UUID,
    conversation_id: UUID,
    user_id: UUID,
    prepared_request_id: str,
) -> dict[str, Any]:
    """Lock a prepared-local assistant placeholder and enforce ownership, active status, mode, token, and TTL."""
    import hmac

    row = await conn.fetchrow(
        """
        SELECT m.*, c.user_id AS owner_id, c.status AS conv_status,
               c.subject_id AS conv_subject_id, subj.is_active AS subject_active
        FROM public.chat_messages m
        JOIN public.chat_conversations c ON c.id = m.conversation_id
        JOIN public.kb_subjects subj ON subj.id = c.subject_id
        WHERE m.id = $1
          AND m.conversation_id = $2
          AND m.role = 'assistant'
        FOR UPDATE OF m;
        """,
        message_id,
        conversation_id,
    )
    if not row or row["conv_status"] == "deleted":
        raise NotFoundError("Prepared local request not found", code="PREPARED_REQUEST_NOT_FOUND")
    if row["owner_id"] != user_id:
        raise AuthorizationError("Access denied: you do not own this prepared local request")

    raw_meta = row["metadata"]
    meta: dict[str, Any] = (
        json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta or {})
    )
    if meta.get("mode") != "private_local":
        raise ConflictError(
            "Target message is not a Private Local prepared request",
            code="INVALID_PREPARED_MODE",
        )

    expected_prep_id = str(meta.get("prepared_request_id") or "")
    if not expected_prep_id or not hmac.compare_digest(expected_prep_id, prepared_request_id):
        raise ConflictError(
            "Prepared request identifier mismatch",
            code="PREPARED_REQUEST_MISMATCH",
        )

    if row["status"] == "completed":
        raise ConflictError(
            "Prepared local request has already been finalized",
            code="ALREADY_FINALIZED",
        )
    if row["status"] == "cancelled":
        raise ConflictError(
            "Prepared local request was cancelled and cannot be finalized",
            code="PREPARED_REQUEST_CANCELLED",
        )
    if row["status"] != "pending":
        raise ConflictError(
            f"Prepared local request cannot be finalized from status '{row['status']}'",
            code="INVALID_PREPARED_STATUS",
        )

    if row["conv_status"] != "active":
        raise ConflictError(
            f"Cannot finalize message in an {row['conv_status']} conversation",
            code="CONVERSATION_NOT_ACTIVE",
        )
    if not row["subject_active"]:
        raise AuthorizationError(
            "Cannot finalize message because the conversation subject is inactive",
            code="SUBJECT_INACTIVE",
        )

    expires_raw = meta.get("prepared_expires_at")
    if not expires_raw:
        raise ConflictError(
            "Prepared local request lacks a valid expiration lease",
            code="PREPARED_REQUEST_EXPIRED",
        )
    expires_dt = datetime.fromisoformat(str(expires_raw))
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=UTC)
    if datetime.now(UTC) > expires_dt:
        raise ConflictError(
            "Prepared local request has expired; please submit a new question",
            code="PREPARED_REQUEST_EXPIRED",
        )

    res = dict(row)
    res["metadata"] = meta
    return res


def compute_prepared_expiry(ttl_seconds: int) -> datetime:
    """Compute UTC expiration timestamp for a prepared-local context lease."""
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)


async def verify_conversation_ready_for_cloud(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    conversation_id: UUID,
) -> None:
    """Verify conversation ownership, active status, active subject, and no active cloud turn."""
    conv = await conn.fetchrow(
        """
        SELECT c.id, c.user_id, c.status, c.subject_id, subj.is_active AS subject_active
        FROM public.chat_conversations c
        JOIN public.kb_subjects subj ON subj.id = c.subject_id
        WHERE c.id = $1;
        """,
        conversation_id,
    )
    if not conv or conv["status"] == "deleted":
        raise NotFoundError("Conversation not found", code="CONVERSATION_NOT_FOUND")
    if conv["user_id"] != user_id:
        raise AuthorizationError("Access denied: you do not own this conversation.")
    if conv["status"] != "active":
        raise AuthorizationError(
            f"Cannot generate messages in an {conv['status']} conversation",
            code="CONVERSATION_NOT_ACTIVE",
        )
    if not conv["subject_active"]:
        raise AuthorizationError(
            "Cannot generate messages because the conversation subject is inactive",
            code="SUBJECT_INACTIVE",
        )

    active_cloud = await conn.fetchval(
        """
        SELECT id
        FROM public.chat_messages
        WHERE conversation_id = $1
          AND role = 'assistant'
          AND status IN ('pending', 'streaming')
          AND coalesce(metadata->>'mode', 'cloud') = 'cloud'
        LIMIT 1;
        """,
        conversation_id,
    )
    if active_cloud is not None:
        raise ConflictError(
            "Another response is currently generating in this conversation. Please wait for it to complete.",
            code="CONVERSATION_CONCURRENCY_LIMIT",
        )
