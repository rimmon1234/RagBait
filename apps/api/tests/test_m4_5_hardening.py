"""M4.5 Post-Implementation Hardening Tests covering Audits A1, A2, A3, and A4."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.main import app
from app.modules.chat.citations.schemas import UNAVAILABLE_SOURCE_PLACEHOLDER
from app.modules.chat.citations.source_repository import mark_referenced_citations
from app.modules.chat.citations.validator import validate_citations
from app.modules.chat.context.compaction import (
    _partition_messages_into_segments,
    compact_conversation,
    recover_pending_compactions,
    schedule_compaction_if_needed,
)
from app.modules.chat.context.compaction_worker import ConversationCompactionWorker
from app.modules.chat.context.config import context_settings
from app.modules.knowledge_base.embeddings import embed_passages
from app.modules.llm_gateway.schemas import ChatCompletionResponse
from app.modules.routing.schemas import RouteDecision
from tests.conftest import create_test_jwt

CREATED_USERS: list[UUID] = []
CREATED_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def _setup_and_cleanup():
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()
    pool = get_pool()
    async with pool.acquire() as conn:
        if CREATED_USERS:
            await conn.execute(
                "DELETE FROM public.chat_conversations WHERE user_id = ANY($1::uuid[])",
                CREATED_USERS,
            )
            await conn.execute(
                "DELETE FROM public.api_key_metadata WHERE owner_id = ANY($1::uuid[])",
                CREATED_USERS,
            )
        if CREATED_SUBJECTS:
            await conn.execute(
                "DELETE FROM public.kb_documents WHERE subject_id = ANY($1::uuid[])",
                CREATED_SUBJECTS,
            )
            await conn.execute(
                "DELETE FROM public.kb_topics WHERE subject_id = ANY($1::uuid[])",
                CREATED_SUBJECTS,
            )
            await conn.execute(
                "DELETE FROM public.kb_subjects WHERE id = ANY($1::uuid[])",
                CREATED_SUBJECTS,
            )
        if CREATED_USERS:
            await conn.execute(
                "DELETE FROM auth.users WHERE id = ANY($1::uuid[])",
                CREATED_USERS,
            )
    CREATED_USERS.clear()
    CREATED_SUBJECTS.clear()
    await close_redis()


async def _create_user(*, prefix: str = "harden", status: str = "approved") -> tuple[UUID, str]:
    uid = uuid4()
    email = f"{prefix}_{uid.hex[:8]}@test.edu"
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb);
            """,
            uid,
            email,
        )
        await conn.execute(
            "UPDATE public.profiles SET approval_status = $2 WHERE id = $1;",
            uid,
            status,
        )
    CREATED_USERS.append(uid)
    return uid, create_test_jwt(user_id=uid, email=email)


async def _grant_contributor(user_id: UUID) -> UUID:
    cid = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                sharing_consent_timestamp, consent_version,
                billing_tier, billing_verified_at, billing_verification_source
            )
            VALUES ($1, $2, 'openrouter', $3, 'sk-or-...test', $4,
                    'active', 'valid', 'community_shared', true, now(), 'v1',
                    'verified_free_only', now(), 'test_fixture');
            """,
            cid,
            user_id,
            f"Hardening Key {cid.hex[:6]}",
            uuid4().hex[:16],
        )
    return cid


async def _seed_kb(passage: str) -> dict[str, UUID]:
    sid = uuid4()
    tid = uuid4()
    did = uuid4()
    cid = uuid4()
    emb = embed_passages([passage])[0]
    vec_str = f"[{','.join(f'{x:.8f}' for x in emb.vector)}]"
    content_hash = f"sha256_{cid.hex}"

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.kb_subjects (id, name, slug, description, is_active) VALUES ($1, $2, $3, 'Hardening Subject', true);",
            sid,
            f"Hardening Subj {sid.hex[:6]}",
            f"harden-subj-{sid.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active) VALUES ($1, $2, $3, $4, true);",
            tid,
            sid,
            f"Hardening Topic {tid.hex[:6]}",
            f"harden-topic-{tid.hex[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, title, original_filename, storage_path,
                mime_type, file_size_bytes, upload_status, extraction_status, indexing_status,
                active_chunk_version, embedding_status, active_embedding_fingerprint, is_archived
            )
            VALUES ($1, $2, $3, 'Hardening Notes.pdf', 'source.pdf', $4, 'application/pdf', 2048,
                    'uploaded', 'completed', 'indexed', 1, 'embedded', $5, false);
            """,
            did,
            sid,
            tid,
            f"documents/{did}/source.pdf",
            emb.model_fingerprint,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_document_chunks (
                id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
                raw_text, character_count, token_count, page_spans, content_hash, is_active
            )
            VALUES ($1, $2, 1, 0, 5, 5, $3, length($3), 32, '[{"page_number": 5, "start_char": 0, "end_char": 50}]'::jsonb, $4, true);
            """,
            cid,
            did,
            passage,
            content_hash,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_chunk_embeddings (
                chunk_id, document_id, chunk_version, model_id, model_fingerprint,
                dimensions, embedding, source_content_hash
            )
            VALUES ($1, $2, 1, 'intfloat/multilingual-e5-small', $3, 384, $4::text::extensions.vector, $5);
            """,
            cid,
            did,
            emb.model_fingerprint,
            vec_str,
            content_hash,
        )
    CREATED_SUBJECTS.append(sid)
    return {"subject_id": sid, "topic_id": tid, "document_id": did, "chunk_id": cid}


async def _create_conv_with_msgs(
    message_count: int, *, mode: str = "cloud", subject_id: UUID | None = None
) -> tuple[UUID, UUID]:
    pool = get_pool()
    uid, _ = await _create_user(prefix="compact_h")
    if subject_id is None:
        sid = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO public.kb_subjects (id, name, slug, is_active) VALUES ($1, $2, $3, true)",
                sid,
                f"Subj {sid.hex[:6]}",
                f"sub-{sid.hex[:6]}",
            )
        CREATED_SUBJECTS.append(sid)
    else:
        sid = subject_id

    cid = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status, message_count, next_sequence_number)
            VALUES ($1, $2, $3, 'Hardening Conv', 'active', $4, $5)
            """,
            cid,
            uid,
            sid,
            message_count,
            message_count + 1,
        )
        for seq in range(1, message_count + 1):
            role = "user" if seq % 2 == 1 else "assistant"
            meta = json.dumps({"mode": mode})
            await conn.execute(
                """
                INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status, metadata)
                VALUES ($1, $2, $3, $4, 'completed', $5::jsonb)
                """,
                cid,
                seq,
                role,
                f"Turn {seq}: virtual memory paging frames and page table entries overview.",
                meta,
            )
    return uid, cid


def _make_resp(
    text: str, *, usage_source: str = "authoritative", in_tok: int = 100, out_tok: int = 25
):
    resp = ChatCompletionResponse(
        id=f"cmpl-{uuid4().hex[:8]}",
        request_id=f"req-{uuid4().hex[:8]}",
        reservation_id=uuid4(),
        provider_id="openrouter",
        model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        resolved_model_id="meta-llama/llama-3.3-70b-instruct:free",
        upstream_provider="openrouter",
        content=text,
        finish_reason="stop",
        usage={
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "usage_source": usage_source,
        },
        created_at=datetime.now(UTC),
    )
    decision = RouteDecision(
        routing_mode="cloud_only",
        selected_source="cloud",
        provider_id="openrouter",
        model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
        resolved_model_id="meta-llama/llama-3.3-70b-instruct:free",
        decision_reason="mock_route",
        created_at=datetime.now(UTC),
    )
    return resp, False, decision


# ------------------------------------------------------------------------------
# Audit A1: Controlled Provenance Transitions
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1_source_provenance_transitions_enforced():
    """Backend rejects marking non-included/unknown citation keys as referenced_in_answer."""
    uid, _ = await _create_user(prefix="prov_a1")
    kb = await _seed_kb("Semaphores coordinate mutual exclusion and bounded buffering.")
    pool = get_pool()
    conv_id = uuid4()
    asst_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, 'A1', 'active')",
            conv_id,
            uid,
            kb["subject_id"],
        )
        await conn.execute(
            "INSERT INTO public.chat_messages (id, conversation_id, sequence_number, role, content, status) VALUES ($1, $2, 1, 'assistant', 'Answer [S1]', 'completed')",
            asst_id,
            conv_id,
        )
        await conn.execute(
            """
            INSERT INTO public.chat_message_sources (
                message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
                document_title_snapshot, page_number_start, page_number_end,
                excerpt, content_hash, chunk_version, retrieval_rank, relevance_score,
                included_in_context, referenced_in_answer
            )
            VALUES
              ($1, 'S1', $2, $3, $2, $3, 'Doc', 5, 5, 'Included text', 'hash1', 1, 1, 0.9, true, false),
              ($1, 'S2', $2, $3, $2, $3, 'Doc', 5, 5, 'Excluded text', 'hash2', 1, 2, 0.4, false, false);
            """,
            asst_id,
            kb["chunk_id"],
            kb["document_id"],
        )

        # Transitioning excluded S2 MUST raise ValueError before mutating DB
        with pytest.raises(ValueError, match="unincluded or unknown citations"):
            await mark_referenced_citations(conn, message_id=asst_id, referenced_keys=["S2"])

        # Valid included S1 succeeds cleanly via approved DB transition function
        updated = await mark_referenced_citations(conn, message_id=asst_id, referenced_keys=["S1"])
        assert updated == 1


# ------------------------------------------------------------------------------
# Audit A2: Durable Compaction, Crash Recovery, Lease Loss, Backoff & No-Cloud Local
# ------------------------------------------------------------------------------


def test_a2_turn_partitioning_keeps_qa_pairs_intact():
    """Segment partitioning must never split a (user, assistant) pair across segments."""
    msgs = [
        {"sequence_number": 1, "role": "user", "content": "Q1 " * 40},
        {"sequence_number": 2, "role": "assistant", "content": "A1 " * 40},
        {"sequence_number": 3, "role": "user", "content": "Q2 " * 40},
        {"sequence_number": 4, "role": "assistant", "content": "A2 " * 40},
    ]
    segs = _partition_messages_into_segments(msgs, max_segment_tokens=160)
    assert len(segs) == 2
    assert [m["sequence_number"] for m in segs[0]] == [1, 2]
    assert [m["sequence_number"] for m in segs[1]] == [3, 4]


@pytest.mark.asyncio
async def test_a2_crashed_running_worker_recovery_lease_expiry_and_backoff():
    """Verify crashed running worker recovery, stale commit blocking, backoff, and Redis restart resilience."""
    pool = get_pool()
    _, cid = await _create_conv_with_msgs(12)

    # 1. Durable scheduling persists pending row
    async with pool.acquire() as conn:
        scheduled = await schedule_compaction_if_needed(conn, cid)
    assert scheduled is True

    # 2. Simulate worker crash mid-execution: status='running', expired locked_until in past!
    stale_lease = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.chat_conversation_summaries
            SET compaction_status = 'running',
                worker_id = 'dead-worker-01',
                lease_token = $2,
                locked_until = timezone('utc'::text, now()) - interval '120 seconds',
                pending_target_sequence = 6
            WHERE conversation_id = $1
            """,
            cid,
            stale_lease,
        )

    # Restarted ConversationCompactionWorker discovers and recovers abandoned 'running' row
    calls = 0

    async def healthy_sum(uid: UUID, msgs: list[dict[str, str]]):
        nonlocal calls
        calls += 1
        return "Recovered summary after worker crash.", "mock-model", "gemini"

    worker = ConversationCompactionWorker(
        worker_id="restarted-worker-02",
        once=True,
        summarizer=healthy_sum,
    )
    await worker.run()
    assert calls == 1

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT compaction_status, compacted_through_sequence, summary_version, lease_token FROM public.chat_conversation_summaries WHERE conversation_id = $1",
            cid,
        )
    assert row["compaction_status"] == "completed"
    assert row["compacted_through_sequence"] == 6
    assert row["summary_version"] == 1
    assert row["lease_token"] is None

    # 3. Redis restart mid-flight + stale worker losing DB lease BEFORE commit
    _, cid_race = await _create_conv_with_msgs(14)

    async def slow_stale_sum(uid: UUID, msgs: list[dict[str, str]]):
        # Simulate Redis restart (wiping lock key) AND another worker reclaiming expired DB lease
        redis_conn = get_redis()
        if redis_conn is not None:
            await redis_conn.delete(f"lock:summarize:{cid_race}")
        async with pool.acquire() as c2:
            await c2.execute(
                """
                UPDATE public.chat_conversation_summaries
                SET worker_id = 'newer-worker',
                    lease_token = gen_random_uuid(),
                    summary_text = 'Newer committed summary',
                    compacted_through_sequence = 8,
                    summary_version = 1,
                    compaction_status = 'completed'
                WHERE conversation_id = $1
                """,
                cid_race,
            )
        return "Stale worker output that MUST NOT commit", "mock", "gemini"

    race_res = await compact_conversation(cid_race, summarizer=slow_stale_sum, force=True)
    assert race_res["status"] == "conflict_discarded"

    async with pool.acquire() as conn:
        txt = await conn.fetchval(
            "SELECT summary_text FROM public.chat_conversation_summaries WHERE conversation_id = $1",
            cid_race,
        )
    assert txt == "Newer committed summary"

    # 4. Retry backoff respected by worker, exhausted after SUMMARY_MAX_RETRIES, and Private Local never calls cloud!
    _, cid_fail = await _create_conv_with_msgs(12)

    async def failing_sum(uid: UUID, msgs: list[dict[str, str]]):
        raise RuntimeError("Transient summarization error")

    for _ in range(context_settings.SUMMARY_MAX_RETRIES):
        await compact_conversation(cid_fail, summarizer=failing_sum, force=True)

    # With respect_backoff=True, row is either waiting on next_retry_at OR exhausted (>= SUMMARY_MAX_RETRIES)
    rec_none = await recover_pending_compactions(
        limit=10, summarizer=healthy_sum, respect_backoff=True
    )
    assert not any(r["conversation_id"] == str(cid_fail) for r in rec_none)

    # 5. Private Local conversation even without explicit flag refuses cloud fallback during recovery!
    _, cid_local = await _create_conv_with_msgs(12, mode="private_local")
    async with pool.acquire() as conn:
        await schedule_compaction_if_needed(conn, cid_local, is_private_local=False)

    with patch("app.modules.chat.context.compaction.route_and_dispatch") as spy_cloud:
        res_loc = await compact_conversation(cid_local, is_private_local=False, force=True)
        spy_cloud.assert_not_called()
    assert res_loc["status"] == "deferred_local"


# ------------------------------------------------------------------------------
# Audit A3: Private Local Security, Expiry, Replay, Concurrency & Cancel Guards
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_private_local_security_replay_concurrency_and_cancel():
    """Verify strict prepared_request_id binding, concurrency races, forged inputs, and cancel guard."""
    owner_id, owner_token = await _create_user(prefix="a3_owner")
    await _grant_contributor(owner_id)
    other_id, other_token = await _create_user(prefix="a3_other")
    await _grant_contributor(other_id)

    kb = await _seed_kb("Zero cloud leakage keeps local Ollama prompts on device.")
    kb_other = await _seed_kb("Separate subject data must never leak via client payload.")
    pool = get_pool()
    conv_id = uuid4()
    other_conv = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
            VALUES ($1, $2, $3, 'Local Security Chat', 'active'),
                   ($4, $2, $3, 'Other Thread Same User', 'active');
            """,
            conv_id,
            owner_id,
            kb["subject_id"],
            other_conv,
        )

    headers = {"Authorization": f"Bearer {owner_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=AssertionError("No cloud dispatch allowed in Private Local flow"),
        ):
            # 1. Forged subject_id in prepare-local body is ignored (always uses conversation subject)
            idem = f"loc-a3-{uuid4().hex[:8]}"
            prep = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
                headers=headers,
                json={
                    "content": "Explain local Ollama isolation.",
                    "idempotency_key": idem,
                    "subject_id": str(kb_other["subject_id"]),
                },
            )
            assert prep.status_code == 200
            p = prep.json()
            assert "Zero cloud leakage" in p["prompt"]
            assert "Separate subject data" not in p["prompt"]

            # 2. Same user + wrong conversation_id -> 404
            wrong_conv = await client.post(
                f"/api/v1/chat/conversations/{other_conv}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": p["message_id"],
                    "prepared_request_id": p["prepared_request_id"],
                    "completion_text": "Wrong thread [S1].",
                },
            )
            assert wrong_conv.status_code == 404

            # 3. Missing or wrong prepared_request_id -> 422 / 409
            missing_tok = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={"message_id": p["message_id"], "completion_text": "Missing token [S1]."},
            )
            assert missing_tok.status_code == 422

            bad_tok = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": p["message_id"],
                    "prepared_request_id": "prep-forged-token-0000",
                    "completion_text": "Forged token [S1].",
                },
            )
            assert bad_tok.status_code == 409
            assert bad_tok.json()["error"]["code"] == "PREPARED_REQUEST_MISMATCH"

            # 4. Archiving source between prepare-local and finalize-local masks excerpt cleanly!
            async with pool.acquire() as arch_conn:
                await arch_conn.execute(
                    "UPDATE public.kb_documents SET is_archived = true WHERE id = $1",
                    kb["document_id"],
                )

            # 5. Concurrent duplicate finalizations -> exactly ONE succeeds (200), other rejected (409)
            payload = {
                "message_id": p["message_id"],
                "prepared_request_id": p["prepared_request_id"],
                "completion_text": "Local prompts remain on device [S1] and forged [S88].",
                "sources": [{"citation_key": "S88", "excerpt": "Forged excerpt"}],
            }
            r1, r2 = await asyncio.gather(
                client.post(
                    f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                    headers=headers,
                    json=payload,
                ),
                client.post(
                    f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                    headers=headers,
                    json=payload,
                ),
            )
            statuses = sorted([r1.status_code, r2.status_code])
            assert statuses == [200, 409]
            winner = r1 if r1.status_code == 200 else r2
            w_data = winner.json()
            assert w_data["valid_citations"] == ["S1"]
            assert w_data["invalid_citations_stripped"] == ["S88"]
            assert w_data["sources"][0]["is_available"] is False
            assert w_data["sources"][0]["excerpt"] == UNAVAILABLE_SOURCE_PLACEHOLDER

            # 6. Cancelled prepared-local request cannot later be finalized as completed!
            prep2 = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
                headers=headers,
                json={"content": "Explain local privacy."},
            )
            assert prep2.status_code == 200
            p2 = prep2.json()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE public.chat_messages SET status = 'cancelled' WHERE id = $1",
                    UUID(p2["message_id"]),
                )
            fin_cancelled = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": p2["message_id"],
                    "prepared_request_id": p2["prepared_request_id"],
                    "completion_text": "Partial text after cancel [S1].",
                },
            )
            assert fin_cancelled.status_code == 409
            assert fin_cancelled.json()["error"]["code"] == "PREPARED_REQUEST_CANCELLED"


# ------------------------------------------------------------------------------
# Audit A4: Citation Repair Revalidation, Empty Guard & Honest Token Labels
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_citation_repair_empty_guard_and_token_labels():
    """Verify empty repair output fails, repair usage accumulates, and token counts carry explicit labels."""
    # 1. Empty or whitespace output cannot pass citation validation
    empty_res = validate_citations("   \n  ", ["S1"])
    assert empty_res.is_valid is False
    assert empty_res.status == "invalid_empty_response"

    uid, token = await _create_user(prefix="a4_user")
    await _grant_contributor(uid)
    kb = await _seed_kb("Locks and condition variables synchronize shared memory access cleanly.")
    pool = get_pool()
    conv_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, 'A4 Citations', 'active')",
            conv_id,
            uid,
            kb["subject_id"],
        )

    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Case 1: Repair emits whitespace-only text -> must fail 422, never complete empty answer
        step_calls = 0

        async def empty_repair(*, user_id, req, correlation_id=None):
            nonlocal step_calls
            step_calls += 1
            if step_calls == 1:
                return _make_resp("Mutexes serialize threads [S99].")
            return _make_resp("    ")

        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=empty_repair,
        ):
            r_empty = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "What do condition variables do?",
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert r_empty.status_code == 422
        assert r_empty.json()["error"]["code"] == "CITATION_VALIDATION_FAILED"

        # Verify NO source snapshot persisted for failed message
        async with pool.acquire() as conn:
            failed_msg_id = await conn.fetchval(
                "SELECT id FROM public.chat_messages WHERE conversation_id = $1 AND role = 'assistant' ORDER BY sequence_number DESC LIMIT 1",
                conv_id,
            )
            src_cnt = await conn.fetchval(
                "SELECT count(*) FROM public.chat_message_sources WHERE message_id = $1",
                failed_msg_id,
            )
        assert src_cnt == 0

        # Case 2: Successful repair accumulates token counts and preserves accurate labels
        rep_calls = 0

        async def good_repair(*, user_id, req, correlation_id=None):
            nonlocal rep_calls
            rep_calls += 1
            if rep_calls == 1:
                return _make_resp("Synchronization uses locks [S99].", in_tok=100, out_tok=20)
            return _make_resp("Synchronization uses locks [S1].", in_tok=140, out_tok=25)

        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=good_repair,
        ):
            r_ok = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "How are locks synchronized?",
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert r_ok.status_code == 200, r_ok.text
        data = r_ok.json()
        assert data["telemetry"]["accounting"]["count_source"] == "local_estimate"
        assert data["telemetry"]["accounting"]["is_approximate"] is True

        async with pool.acquire() as conn:
            tok_row = await conn.fetchval(
                "SELECT token_usage FROM public.chat_messages WHERE id = $1",
                UUID(data["assistant_message"]["id"]),
            )
        tok_dict = json.loads(tok_row) if isinstance(tok_row, str) else tok_row
        assert tok_dict["count_label"] == "exact_provider_reported"
        assert tok_dict["is_approximate"] is False
        assert tok_dict["input_tokens"] == 240
        assert tok_dict["output_tokens"] == 45
        assert tok_dict["total_tokens"] == 285
