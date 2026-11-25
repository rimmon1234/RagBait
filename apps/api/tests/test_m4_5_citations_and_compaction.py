"""Unit & integration tests for M4.5 Citation Validation and Summary Compaction Engine (N4, N5)."""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.chat.citations.parser import parse_citations
from app.modules.chat.citations.validator import validate_citations
from app.modules.chat.context.compaction import (
    SummaryRedisLock,
    compact_conversation,
    recover_pending_compactions,
    validate_summary_text,
)

CREATED_USERS: list[UUID] = []
CREATED_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def _init_pools_and_cleanup():
    await init_db_pool()
    await init_redis()
    yield
    pool = get_pool()
    async with pool.acquire() as conn:
        if CREATED_USERS:
            await conn.execute(
                "DELETE FROM public.chat_conversations WHERE user_id = ANY($1::uuid[])",
                CREATED_USERS,
            )
            await conn.execute("DELETE FROM auth.users WHERE id = ANY($1::uuid[])", CREATED_USERS)
        if CREATED_SUBJECTS:
            await conn.execute(
                "DELETE FROM public.kb_subjects WHERE id = ANY($1::uuid[])", CREATED_SUBJECTS
            )
    CREATED_USERS.clear()
    CREATED_SUBJECTS.clear()
    await close_redis()


async def _create_conv_with_messages(message_count: int) -> tuple[UUID, UUID]:
    pool = get_pool()
    uid = uuid4()
    sid = uuid4()
    cid = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb)
            """,
            uid,
            f"compact_{uid.hex[:8]}@test.edu",
        )
        await conn.execute(
            "UPDATE public.profiles SET approval_status = 'approved' WHERE id = $1", uid
        )
        await conn.execute(
            "INSERT INTO public.kb_subjects (id, name, slug, is_active) VALUES ($1, $2, $3, true)",
            sid,
            f"Compaction Subj {sid.hex[:6]}",
            f"comp-subj-{sid.hex[:6]}",
        )
        await conn.execute(
            """
            INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status, message_count, next_sequence_number)
            VALUES ($1, $2, $3, 'Long Session', 'active', $4, $5)
            """,
            cid,
            uid,
            sid,
            message_count,
            message_count + 1,
        )
        for seq in range(1, message_count + 1):
            role = "user" if seq % 2 == 1 else "assistant"
            content = (
                f"Turn message #{seq}: Detailed academic explanation regarding paging, "
                f"page tables, TLB caching, and virtual memory frames in operating systems."
            )
            await conn.execute(
                """
                INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status)
                VALUES ($1, $2, $3, $4, 'completed')
                """,
                cid,
                seq,
                role,
                content,
            )
    CREATED_USERS.append(uid)
    CREATED_SUBJECTS.append(sid)
    return uid, cid


# ------------------------------------------------------------------------------
# N4. Citation Parser & Validation Tests
# ------------------------------------------------------------------------------


def test_citation_parser_precision_and_edge_cases():
    """Verify exact parsing for [S1], [S2], multi-digit [S10], adjacent tags, duplicates, and noise."""
    text = (
        "Paging eliminates external fragmentation [S1][S2] while segmentation "
        "supports logical protection [S10]. Repeated citation [S1] is allowed, "
        'while code `arr[S1]`, numeric reference [1], and quoted "S2" are ignored.'
    )
    parsed = parse_citations(text)
    assert parsed.unique_keys == ["S1", "S2", "S10"]
    assert parsed.duplicate_keys == ["S1"]
    assert parsed.total_occurrences == 4
    assert parsed.malformed_tokens == []
    # Confirm [S10] was NOT misparsed as [S1]
    assert "[S10]" in parsed.raw_tokens


def test_citation_validator_rejects_unknown_malformed_and_excluded():
    """Validator must catch unknown [S99], excluded candidate [S3], malformed [SOURCE1], and abstain empty."""
    included = {"S1", "S2"}

    # 1. Valid multi-citation answer
    ok_res = validate_citations("Fact A [S1] and Fact B [S2] repeated [S1].", included)
    assert ok_res.is_valid is True
    assert ok_res.valid_keys == ["S1", "S2"]
    assert ok_res.status == "valid_grounded"

    # 2. Unknown hallucinated [S99] & excluded candidate [S3]
    bad_res = validate_citations("Fact A [S1], excluded [S3], and hallucinated [S99].", included)
    assert bad_res.is_valid is False
    assert bad_res.valid_keys == ["S1"]
    assert bad_res.unknown_keys == ["S3", "S99"]
    assert "[S99]" not in bad_res.sanitized_text
    assert "[S3]" not in bad_res.sanitized_text
    assert "[S1]" in bad_res.sanitized_text

    # 3. Malformed [SOURCE1], [s1], [S0]
    malformed_res = validate_citations("Bad tags [SOURCE1] and [s1] and [S0].", included)
    assert malformed_res.is_valid is False
    assert malformed_res.malformed_tokens == ["[SOURCE1]", "[s1]", "[S0]"]
    assert malformed_res.status == "invalid_malformed_citations"

    # 4. Empty evidence abstention (no citations valid vs fabricated [S1] invalid)
    empty_ok = validate_citations(
        "The uploaded study materials do not contain information on this topic.", set()
    )
    assert empty_ok.is_valid is True
    assert empty_ok.status == "valid_no_sources"

    empty_bad = validate_citations("Textbook defines this [S1].", set())
    assert empty_bad.is_valid is False
    assert empty_bad.unknown_keys == ["S1"]


# ------------------------------------------------------------------------------
# N5. Conversation Summary Compaction & Concurrency Tests
# ------------------------------------------------------------------------------


def test_summary_validation_sanitization():
    """Summary validator must strip secret-like tokens and accidental XML tags."""
    raw = "<conversation_summary>Reviewed OS keys sk-1234567890abcdef and paging.</conversation_summary>"
    clean = validate_summary_text(raw)
    assert "<conversation_summary>" not in clean
    assert "sk-1234567890abcdef" not in clean
    assert "[REDACTED_CREDENTIAL]" in clean


@pytest.mark.asyncio
async def test_compaction_retention_window_and_original_transcript_preserved():
    """Conversations <= 6 skip compaction; > 6 compact only older messages and never delete originals."""
    # 1. Exactly 6 messages -> skipped
    _, cid_short = await _create_conv_with_messages(6)
    calls = 0

    async def fake_summarizer(uid: UUID, msgs: list[dict[str, str]]):
        nonlocal calls
        calls += 1
        return "Summary of early turns discussing paging and OS concepts.", "mock-lite", "gemini"

    res_short = await compact_conversation(cid_short, summarizer=fake_summarizer, force=True)
    assert res_short["status"] == "skipped"
    assert res_short["reason"] == "below_retain_window"
    assert calls == 0

    # 2. 12 messages -> messages 1..6 compacted, messages 7..12 (last K=6) stay verbatim!
    _, cid_long = await _create_conv_with_messages(12)
    res_long = await compact_conversation(cid_long, summarizer=fake_summarizer, force=True)
    assert res_long["status"] == "completed"
    assert res_long["compacted_through_sequence"] == 6
    assert res_long["summary_version"] == 1
    assert res_long["messages_compacted"] == 6

    # Verify ALL 12 original messages remain intact in public.chat_messages!
    pool = get_pool()
    async with pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT count(*)::int FROM public.chat_messages WHERE conversation_id = $1", cid_long
        )
        assert cnt == 12


@pytest.mark.asyncio
async def test_compaction_concurrent_arrival_and_optimistic_cas():
    """Concurrent new messages arriving during summarization remain intact; stale CAS discards cleanly."""
    _, cid = await _create_conv_with_messages(10)
    pool = get_pool()

    async def slow_summarizer_with_concurrent_race(uid: UUID, msgs: list[dict[str, str]]):
        # Simulate another worker committing summary_version=1 & sequence=5 while we ran!
        async with pool.acquire() as race_conn:
            await race_conn.execute(
                """
                UPDATE public.chat_conversation_summaries
                SET summary_text = 'Newer concurrent summary committed first.',
                    compacted_through_sequence = 5,
                    summary_version = 1,
                    token_count = 12,
                    compaction_status = 'completed'
                WHERE conversation_id = $1
                """,
                cid,
            )
            # Also simulate concurrent new turn #11 arriving!
            await race_conn.execute(
                """
                INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status)
                VALUES ($1, 11, 'user', 'Concurrent incoming question 11', 'completed')
                """,
                cid,
            )
        return "Stale summary candidate that must NOT overwrite", "mock", "gemini"

    res = await compact_conversation(
        cid, summarizer=slow_summarizer_with_concurrent_race, force=True
    )
    assert res["status"] == "conflict_discarded"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT summary_text, compacted_through_sequence, summary_version FROM public.chat_conversation_summaries WHERE conversation_id = $1",
            cid,
        )
        assert row["summary_text"] == "Newer concurrent summary committed first."
        assert row["compacted_through_sequence"] == 5
        assert row["summary_version"] == 1
        # Message 11 untouched
        m11 = await conn.fetchval(
            "SELECT content FROM public.chat_messages WHERE conversation_id = $1 AND sequence_number = 11",
            cid,
        )
        assert m11 == "Concurrent incoming question 11"


@pytest.mark.asyncio
async def test_redis_mutex_lease_token_prevents_stale_unlock():
    """Stale worker with expired/different UUID4 token cannot delete another worker's Redis lock."""
    redis_conn = get_redis()
    if redis_conn is None:
        pytest.skip("Redis not running")

    conv_id = uuid4()
    lock_a = SummaryRedisLock(conv_id, ttl_seconds=10)
    lock_b = SummaryRedisLock(conv_id, ttl_seconds=10)

    try:
        assert await lock_a.acquire() is True
        # lock_b cannot acquire while lock_a holds it
        assert await lock_b.acquire() is False

        # Simulate lock_a expiry & re-acquisition by overwriting token in Redis directly
        await redis_conn.set(lock_b.key, lock_b.lease_token, ex=10)

        # Stale lock_a calling release() MUST return 0 and preserve lock_b!
        assert await lock_a.release() is False
        assert await redis_conn.get(lock_b.key) == lock_b.lease_token

        # Legitimate owner lock_b CAN release
        assert await lock_b.release() is True
        assert await redis_conn.get(lock_b.key) is None
    finally:
        await redis_conn.delete(f"lock:summarize:{conv_id}")


@pytest.mark.asyncio
async def test_compaction_durable_failure_recovery_and_private_local_deferral():
    """Verify durable 'failed' recovery via recover_pending_compactions() and Private Local no-cloud rule."""
    # 1. Private Local NEVER calls cloud inference
    _, cid_local = await _create_conv_with_messages(10)
    with patch("app.modules.chat.context.compaction.route_and_dispatch") as mock_cloud:
        res_local = await compact_conversation(cid_local, is_private_local=True, force=True)
        mock_cloud.assert_not_called()

    assert res_local["status"] == "deferred_local"
    pool = get_pool()
    async with pool.acquire() as conn:
        st = await conn.fetchval(
            "SELECT compaction_status FROM public.chat_conversation_summaries WHERE conversation_id = $1",
            cid_local,
        )
        assert st == "deferred_local"

    # 2. Durable scheduling + crash recovery
    _, cid_crash = await _create_conv_with_messages(12)

    async def crashing_summarizer(uid: UUID, msgs: list[dict[str, str]]):
        raise RuntimeError("Simulated worker crash mid-summarization")

    res_fail = await compact_conversation(cid_crash, summarizer=crashing_summarizer, force=True)
    assert res_fail["status"] == "failed"

    async def healthy_summarizer(uid: UUID, msgs: list[dict[str, str]]):
        return "Recovered durable summary covering early OS turns.", "mock", "gemini"

    rec_results = await recover_pending_compactions(limit=10, summarizer=healthy_summarizer)
    assert any(
        r["conversation_id"] == str(cid_crash) and r["status"] == "completed" for r in rec_results
    )
