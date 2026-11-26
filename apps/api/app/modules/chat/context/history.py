"""History loader fetching committed summaries and recent completed conversation turns."""

from uuid import UUID

import asyncpg

from app.modules.chat.context.config import context_settings
from app.modules.chat.context.schemas import ConversationHistoryContext, HistoryTurnMessage
from app.modules.chat.context.token_counter import ModelTokenCounter


async def load_conversation_history_context(
    conn: asyncpg.Connection,
    conversation_id: UUID,
    token_counter: ModelTokenCounter,
    *,
    exclude_message_id: UUID | None = None,
    recent_limit: int | None = None,
) -> ConversationHistoryContext:
    """Load the latest committed conversation summary and uncompacted completed messages.

    Rules:
    - Never deletes original messages from public.chat_messages.
    - Reads only completed messages (failed/pending/cancelled turns are excluded).
    - Always fetches at least up to `CONTEXT_RECENT_MESSAGES` (K=6) recent messages cleanly,
      and loads uncompacted completed messages strictly after `compacted_through_sequence`.
    """
    eff_limit = recent_limit or context_settings.CONTEXT_HISTORY_FETCH_LIMIT

    sum_row = await conn.fetchrow(
        """
        SELECT summary_text, compacted_through_sequence, summary_version, token_count, compaction_status
        FROM public.chat_conversation_summaries
        WHERE conversation_id = $1
        """,
        conversation_id,
    )

    compacted_seq = int(sum_row["compacted_through_sequence"]) if sum_row else 0
    summary_text = (
        sum_row["summary_text"]
        if (sum_row and compacted_seq > 0 and sum_row["summary_text"])
        else None
    )
    summary_ver = int(sum_row["summary_version"]) if sum_row else 0
    summary_tok = int(sum_row["token_count"]) if sum_row else 0
    compaction_status = sum_row["compaction_status"] if sum_row else "idle"

    if exclude_message_id is not None:
        rows = await conn.fetch(
            """
            SELECT id, sequence_number, role, content
            FROM public.chat_messages
            WHERE conversation_id = $1
              AND status = 'completed'
              AND sequence_number > $2
              AND id != $3
              AND char_length(trim(content)) > 0
            ORDER BY sequence_number DESC
            LIMIT $4
            """,
            conversation_id,
            compacted_seq,
            exclude_message_id,
            eff_limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id, sequence_number, role, content
            FROM public.chat_messages
            WHERE conversation_id = $1
              AND status = 'completed'
              AND sequence_number > $2
              AND char_length(trim(content)) > 0
            ORDER BY sequence_number DESC
            LIMIT $3
            """,
            conversation_id,
            compacted_seq,
            eff_limit,
        )

    # Reverse into ascending chronological order
    recent_msgs = [
        HistoryTurnMessage(
            id=r["id"],
            sequence_number=r["sequence_number"],
            role=r["role"],
            content=r["content"],
            token_count=token_counter.count_message(r["role"], r["content"]),
        )
        for r in reversed(rows)
    ]

    return ConversationHistoryContext(
        summary_text=summary_text,
        compacted_through_sequence=compacted_seq,
        summary_version=summary_ver,
        summary_token_count=summary_tok,
        compaction_status=compaction_status,
        recent_messages=recent_msgs,
    )
