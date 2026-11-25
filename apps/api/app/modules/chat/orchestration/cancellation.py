"""Distributed cross-process cancellation signaling and authorization helpers."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.redis import get_redis
from app.modules.chat.orchestration.config import generation_settings

logger = logging.getLogger("ragbait.chat.orchestration.cancellation")


def cancel_marker_key(message_id: UUID) -> str:
    """Redis key storing the active generation_request_id slated for cancellation."""
    return f"chat:cancel:{message_id}"


def cancel_pubsub_channel(message_id: UUID) -> str:
    """Redis Pub/Sub channel for immediate cross-process cancellation wakeups."""
    return f"chat:cancel:ch:{message_id}"


async def is_generation_cancelled_in_redis(
    message_id: UUID,
    generation_request_id: str,
) -> bool:
    """Return True strictly if Redis cancellation marker matches this exact generation_request_id."""
    redis_conn = get_redis()
    if redis_conn is None:
        return False
    try:
        val = await redis_conn.get(cancel_marker_key(message_id))
        return val is not None and (str(val) == generation_request_id or str(val) == "*")
    except Exception:
        return False


async def set_redis_cancel_marker(message_id: UUID, generation_request_id: str) -> None:
    """Broadcast distributed cancel marker + Pub/Sub signal across API worker processes."""
    redis_conn = get_redis()
    if redis_conn is None:
        return
    ttl = generation_settings.GENERATION_CANCEL_MARKER_TTL_SECONDS
    with suppress(Exception):
        await redis_conn.set(cancel_marker_key(message_id), generation_request_id, ex=ttl)
        await redis_conn.publish(cancel_pubsub_channel(message_id), generation_request_id)


async def request_generation_cancellation(
    *,
    user_id: UUID,
    message_id: UUID,
    conversation_id: UUID | None = None,
    conn: asyncpg.Connection | None = None,
) -> dict[str, object]:
    """Authorize ownership, emit distributed cancel marker, and transition active generation cleanly.

    Never transitions 'completed' or 'failed' messages into 'cancelled'.
    """

    async def _run(db_conn: asyncpg.Connection) -> dict[str, object]:
        row = await db_conn.fetchrow(
            """
            SELECT m.id, m.conversation_id, m.role, m.content, m.status,
                   m.generation_request_id, m.metadata,
                   c.user_id AS owner_id, c.status AS conv_status
            FROM public.chat_messages m
            JOIN public.chat_conversations c ON c.id = m.conversation_id
            WHERE m.id = $1
            """,
            message_id,
        )
        if not row or row["conv_status"] == "deleted" or row["owner_id"] != user_id:
            raise NotFoundError("Message not found", code="MESSAGE_NOT_FOUND")
        if conversation_id is not None and row["conversation_id"] != conversation_id:
            raise NotFoundError("Message not found", code="MESSAGE_NOT_FOUND")
        if row["role"] != "assistant":
            raise ConflictError(
                "Only assistant generation messages can be cancelled",
                code="INVALID_CANCEL_TARGET",
            )

        status_val: str = str(row["status"])
        gen_req_id: str = str(row["generation_request_id"] or "*")
        retained_len = len(row["content"] or "")

        if status_val == "completed":
            raise AppError(
                message="Generation has already completed and cannot be cancelled",
                code="GENERATION_ALREADY_COMPLETED",
                status_code=409,
                details={"message_id": str(message_id), "status": "completed"},
            )
        if status_val == "failed":
            raise AppError(
                message="Generation has already failed",
                code="GENERATION_ALREADY_FAILED",
                status_code=409,
                details={"message_id": str(message_id), "status": "failed"},
            )
        if status_val == "cancelled":
            return {
                "message_id": message_id,
                "conversation_id": row["conversation_id"],
                "status": "cancelled",
                "already_cancelled": True,
                "retained_length": retained_len,
            }

        # Active state ('pending' or 'streaming'): publish distributed marker BEFORE conditional DB write
        await set_redis_cancel_marker(message_id, gen_req_id)

        raw_meta = row["metadata"]
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta or {})
        updated_meta = {
            **meta,
            "stage": "cancelled",
            "cancel_reason": "user_requested",
            "cancelled_at": datetime.now(UTC).isoformat(),
        }

        updated = await db_conn.fetchrow(
            """
            UPDATE public.chat_messages
            SET status = 'cancelled',
                finish_reason = 'cancelled',
                metadata = $2::jsonb,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1
              AND status IN ('pending', 'streaming')
            RETURNING status, char_length(content) AS retained_len;
            """,
            message_id,
            json.dumps(updated_meta),
        )
        if updated:
            return {
                "message_id": message_id,
                "conversation_id": row["conversation_id"],
                "status": "cancelled",
                "already_cancelled": False,
                "retained_length": int(updated["retained_len"] or 0),
            }

        # Re-read exact winner if concurrent completion/failure/cancel occurred mid-flight
        latest = await db_conn.fetchrow(
            "SELECT status, char_length(content) AS retained_len FROM public.chat_messages WHERE id = $1",
            message_id,
        )
        latest_st = str(latest["status"]) if latest else "unknown"
        if latest_st == "cancelled":
            return {
                "message_id": message_id,
                "conversation_id": row["conversation_id"],
                "status": "cancelled",
                "already_cancelled": True,
                "retained_length": int(latest["retained_len"] or 0) if latest else 0,
            }
        if latest_st == "completed":
            raise AppError(
                message="Generation completed concurrently before cancellation took effect",
                code="GENERATION_ALREADY_COMPLETED",
                status_code=409,
                details={"message_id": str(message_id), "status": "completed"},
            )
        raise AppError(
            message=f"Generation cannot be cancelled from state '{latest_st}'",
            code="GENERATION_NOT_CANCELLABLE",
            status_code=409,
            details={"message_id": str(message_id), "status": latest_st},
        )

    if conn is not None:
        return await _run(conn)
    pool = get_pool()
    async with pool.acquire() as db_conn:
        return await _run(db_conn)


async def watch_cancellation_and_disconnect(
    *,
    message_id: UUID,
    generation_request_id: str,
    cancel_event: asyncio.Event,
    disconnect_event: asyncio.Event,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    stop_event: asyncio.Event,
    poll_interval: float = 0.25,
) -> None:
    """Monitor Redis Pub/Sub, cancel key, and client TCP disconnect concurrently."""
    redis_conn = get_redis()
    pubsub = None

    if redis_conn is not None:
        with suppress(Exception):
            pubsub = redis_conn.pubsub()
            await pubsub.subscribe(cancel_pubsub_channel(message_id))

    try:
        while not stop_event.is_set() and not cancel_event.is_set():
            # 1. Check client socket disconnect
            if is_disconnected is not None:
                with suppress(Exception):
                    if await is_disconnected():
                        disconnect_event.set()
                        cancel_event.set()
                        break

            # 2. Check Pub/Sub wakeups
            if pubsub is not None:
                with suppress(Exception):
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
                    if msg and str(msg.get("data") or "") in (generation_request_id, "*"):
                        cancel_event.set()
                        break

            # 3. Safety net check against Redis cancel key (handles pubsub reconnects)
            if await is_generation_cancelled_in_redis(message_id, generation_request_id):
                cancel_event.set()
                break

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                break
            except TimeoutError:
                pass
    except asyncio.CancelledError:
        pass
    finally:
        if pubsub is not None:
            with suppress(Exception):
                await pubsub.unsubscribe(cancel_pubsub_channel(message_id))
                await pubsub.aclose()  # type: ignore[no-untyped-call]
