"""End-to-end integration tests for M4.5 Grounded RAG Orchestration, Private Local, & Revalidation (N6, N7, N8)."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_pool, init_db_pool
from app.core.redis import close_redis, init_redis
from app.main import app
from app.modules.chat.citations.schemas import UNAVAILABLE_SOURCE_PLACEHOLDER
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


async def _create_user(
    *,
    prefix: str = "rag_student",
    approval_status: str = "approved",
    role: str = "user",
) -> tuple[UUID, str]:
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
            "UPDATE public.profiles SET approval_status = $2, role = $3 WHERE id = $1;",
            uid,
            approval_status,
            role,
        )
    CREATED_USERS.append(uid)
    token = create_test_jwt(user_id=uid, email=email)
    return uid, token


async def _grant_contributor_credential(
    user_id: UUID,
    *,
    provider_id: str = "openrouter",
    billing_tier: str = "verified_free_only",
    sharing_scope: str = "community_shared",
    shared_consent: bool = True,
    status: str = "active",
    validation_status: str = "valid",
) -> UUID:
    cred_id = uuid4()
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
            VALUES (
                $1, $2, $3, $4, 'sk-or-...test', $5,
                $6, $7, $8, $9, now(), 'v1',
                $10, now(), 'test_fixture'
            );
            """,
            cred_id,
            user_id,
            provider_id,
            f"M4.5 Test Key {cred_id.hex[:6]}",
            uuid4().hex[:16],
            status,
            validation_status,
            sharing_scope,
            shared_consent,
            billing_tier,
        )
    return cred_id


async def _seed_subject_with_kb(
    *,
    subject_name: str,
    topic_name: str,
    doc_title: str,
    passage_text: str,
    page_number: int = 12,
    subject_active: bool = True,
    topic_active: bool = True,
    doc_archived: bool = False,
) -> dict[str, UUID]:
    sid = uuid4()
    tid = uuid4()
    did = uuid4()
    cid = uuid4()

    emb = embed_passages([passage_text])[0]
    vec_str = f"[{','.join(f'{x:.8f}' for x in emb.vector)}]"
    content_hash = f"sha256_{cid.hex}"

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.kb_subjects (id, name, slug, description, is_active) VALUES ($1, $2, $3, 'Test Subject', $4);",
            sid,
            f"{subject_name} {sid.hex[:6]}",
            f"subj-{sid.hex[:8]}",
            subject_active,
        )
        await conn.execute(
            "INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active) VALUES ($1, $2, $3, $4, $5);",
            tid,
            sid,
            f"{topic_name} {tid.hex[:6]}",
            f"topic-{tid.hex[:8]}",
            topic_active,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_documents (
                id, subject_id, topic_id, title, original_filename,
                storage_path, mime_type, file_size_bytes,
                upload_status, extraction_status, indexing_status,
                active_chunk_version, embedding_status, active_embedding_fingerprint,
                is_archived
            )
            VALUES (
                $1, $2, $3, $4, 'notes.pdf',
                $5, 'application/pdf', 4096,
                'uploaded', 'completed', 'indexed',
                1, 'embedded', $6,
                $7
            );
            """,
            did,
            sid,
            tid,
            doc_title,
            f"documents/{did}/source.pdf",
            emb.model_fingerprint,
            doc_archived,
        )
        spans = json.dumps(
            [{"page_number": page_number, "start_char": 0, "end_char": len(passage_text)}]
        )
        await conn.execute(
            """
            INSERT INTO public.kb_document_chunks (
                id, document_id, chunking_version, chunk_index,
                page_number_start, page_number_end, raw_text,
                character_count, token_count, page_spans, content_hash, is_active
            )
            VALUES ($1, $2, 1, 0, $3, $3, $4, length($4), 48, $5::jsonb, $6, true);
            """,
            cid,
            did,
            page_number,
            passage_text,
            spans,
            content_hash,
        )
        await conn.execute(
            """
            INSERT INTO public.kb_chunk_embeddings (
                chunk_id, document_id, chunk_version, model_id,
                model_fingerprint, dimensions, embedding, source_content_hash
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


async def _create_conversation(
    *,
    user_id: UUID,
    subject_id: UUID,
    status: str = "active",
    title: str = "Study Session",
) -> UUID:
    conv_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
            VALUES ($1, $2, $3, $4, $5);
            """,
            conv_id,
            user_id,
            subject_id,
            title,
            status,
        )
    return conv_id


def _make_cloud_completion(
    content: str,
    *,
    model_id: str = "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    provider_id: str = "openrouter",
) -> tuple[ChatCompletionResponse, bool, RouteDecision]:
    resp = ChatCompletionResponse(
        id=f"cmpl-{uuid4().hex[:8]}",
        request_id=f"req-{uuid4().hex[:8]}",
        reservation_id=uuid4(),
        provider_id=provider_id,
        model_id=model_id,
        resolved_model_id=model_id.removeprefix("openrouter/"),
        upstream_provider=provider_id,
        content=content,
        finish_reason="stop",
        usage={"input_tokens": 210, "output_tokens": 45, "total_tokens": 255},
        created_at=datetime.now(UTC),
    )
    decision = RouteDecision(
        routing_mode="cloud_only",
        selected_source="cloud",
        provider_id=provider_id,
        model_id=model_id,
        resolved_model_id=model_id.removeprefix("openrouter/"),
        decision_reason="mocked_cloud_route",
        created_at=datetime.now(UTC),
    )
    return resp, False, decision


# ------------------------------------------------------------------------------
# N6. End-to-End Cloud Grounded RAG, Idempotency & Failure Recovery
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_rag_turn_e2e_idempotency_and_failure_recovery():
    """Verify full cloud RAG turn, subject isolation, idempotency replay/conflict, and retry recovery."""
    student_id, token = await _create_user(prefix="cloud_e2e")
    await _grant_contributor_credential(student_id)

    kb_os = await _seed_subject_with_kb(
        subject_name="Operating Systems",
        topic_name="Memory Management",
        doc_title="Silberschatz OS Chapter 9.pdf",
        passage_text=(
            "Paging divides physical memory into fixed-sized frames and logical memory into equal "
            "pages to avoid external fragmentation. Segmentation divides memory into variable-length "
            "logical units such as code, stack, and heap segments."
        ),
        page_number=42,
    )
    await _seed_subject_with_kb(
        subject_name="Cell Biology",
        topic_name="Organelles",
        doc_title="Biology Textbook.pdf",
        passage_text="Mitochondria synthesize ATP via oxidative phosphorylation across the inner membrane.",
        page_number=7,
    )
    conv_id = await _create_conversation(user_id=student_id, subject_id=kb_os["subject_id"])

    captured_requests: list[object] = []

    async def fake_route_and_dispatch(*, user_id, req, correlation_id=None):
        captured_requests.append(req)
        return _make_cloud_completion(
            "Paging avoids external fragmentation with fixed frames [S1], whereas segmentation "
            "uses variable logical units [S1]."
        )

    idem_key = f"cloud-turn-{uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=fake_route_and_dispatch,
        ):
            resp = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "What is the difference between paging and segmentation?",
                    "idempotency_key": idem_key,
                    "routing_mode": "cloud_only",
                    "provider_id": "openrouter",
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(captured_requests) == 1
        sent_user_prompt = captured_requests[0].messages[-1].content  # type: ignore[attr-defined]
        assert '<study_context_evidence trust_level="untrusted_reference_data">' in sent_user_prompt
        assert 'id="S1"' in sent_user_prompt
        assert "Silberschatz OS Chapter 9.pdf" in sent_user_prompt
        assert "Mitochondria" not in sent_user_prompt

        assert data["user_message"]["status"] == "completed"
        assert data["assistant_message"]["status"] == "completed"
        assert (
            data["assistant_message"]["model_id"]
            == "openrouter/meta-llama/llama-3.3-70b-instruct:free"
        )
        assert data["assistant_message"]["provider_id"] == "openrouter"
        assert data["telemetry"]["selected_evidence_count"] >= 1
        assert data["telemetry"]["citation_validation_status"] == "valid_grounded"
        assert data["telemetry"]["repair_attempted"] is False

        assert len(data["sources"]) == 1
        src = data["sources"][0]
        assert src["citation_key"] == "S1"
        assert src["document_id"] == str(kb_os["document_id"])
        assert src["page_number_start"] == 42
        assert src["included_in_context"] is True
        assert src["referenced_in_answer"] is True
        assert src["is_available"] is True
        assert (
            f"/knowledge-base/document/{kb_os['document_id']}?page=42" == src["document_viewer_url"]
        )

        # 2. Idempotency replay returns persisted turn WITHOUT invoking LLM again
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=AssertionError("Replay must not call route_and_dispatch"),
        ):
            replay = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "What is the difference between paging and segmentation?",
                    "idempotency_key": idem_key,
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert replay.status_code == 200, replay.text
        replay_data = replay.json()
        assert replay_data["user_message"]["id"] == data["user_message"]["id"]
        assert replay_data["assistant_message"]["id"] == data["assistant_message"]["id"]
        assert len(replay_data["sources"]) == 1

        # 3. Same idempotency key with different prompt content -> 409 conflict
        conflict = await client.post(
            f"/api/v1/chat/conversations/{conv_id}/messages",
            headers=headers,
            json={
                "content": "Completely different question",
                "idempotency_key": idem_key,
                "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

        # 4. Upstream failure marks assistant message 'failed'; retrying same key recovers cleanly
        retry_key = f"retry-turn-{uuid4().hex[:8]}"
        retry_q = "Explain fixed-sized frames in paging."
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=RuntimeError("Transient upstream provider timeout"),
        ):
            with pytest.raises(RuntimeError, match="Transient upstream provider timeout"):
                await client.post(
                    f"/api/v1/chat/conversations/{conv_id}/messages",
                    headers=headers,
                    json={
                        "content": retry_q,
                        "idempotency_key": retry_key,
                        "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                    },
                )

        pool = get_pool()
        async with pool.acquire() as conn:
            failed_rows = await conn.fetch(
                "SELECT role, status, error_message FROM public.chat_messages WHERE conversation_id = $1 AND sequence_number >= 3 ORDER BY sequence_number ASC;",
                conv_id,
            )
        assert [r["role"] for r in failed_rows] == ["user", "assistant"]
        assert failed_rows[0]["status"] == "completed"
        assert failed_rows[1]["status"] == "failed"

        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=fake_route_and_dispatch,
        ):
            recovered = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": retry_q,
                    "idempotency_key": retry_key,
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert recovered.status_code == 200, recovered.text
        rec_data = recovered.json()
        assert rec_data["assistant_message"]["status"] == "completed"
        assert len(rec_data["sources"]) == 1

        async with pool.acquire() as conn:
            user_count = await conn.fetchval(
                "SELECT count(*) FROM public.chat_messages WHERE conversation_id = $1 AND role = 'user' AND idempotency_key = $2;",
                conv_id,
                retry_key,
            )
        assert user_count == 1


@pytest.mark.asyncio
async def test_citation_repair_and_failure_status_transition():
    """Verify single bounded citation repair pass and clean 422 failure when repair still hallucinates."""
    student_id, token = await _create_user(prefix="repair_student")
    await _grant_contributor_credential(student_id)
    kb = await _seed_subject_with_kb(
        subject_name="Distributed Systems",
        topic_name="Consensus",
        doc_title="Raft Paper.pdf",
        passage_text="Raft achieves consensus via leader election, log replication, and safety invariants.",
        page_number=4,
    )
    conv_id = await _create_conversation(user_id=student_id, subject_id=kb["subject_id"])
    headers = {"Authorization": f"Bearer {token}"}

    # Case A: Initial hallucination [S99] -> repaired cleanly to [S1] in 1 repair pass
    call_counter = 0

    async def repairable_route(*, user_id, req, correlation_id=None):
        nonlocal call_counter
        call_counter += 1
        if call_counter == 1:
            return _make_cloud_completion("Raft achieves consensus through leader election [S99].")
        return _make_cloud_completion("Raft achieves consensus through leader election [S1].")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=repairable_route,
        ):
            res_ok = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "How does Raft achieve consensus?",
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert res_ok.status_code == 200, res_ok.text
        body_ok = res_ok.json()
        assert call_counter == 2
        assert body_ok["telemetry"]["repair_attempted"] is True
        assert body_ok["telemetry"]["citation_validation_status"] == "valid_grounded"
        assert "[S1]" in body_ok["assistant_message"]["content"]

        # Case B: Both initial and bounded repair cite unknown [S77] -> 422 and failed DB status
        unrepairable_calls = 0

        async def unrepairable_route(*, user_id, req, correlation_id=None):
            nonlocal unrepairable_calls
            unrepairable_calls += 1
            return _make_cloud_completion("Leader terms increase monotonically [S77].")

        fail_key = f"unrep-{uuid4().hex[:8]}"
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch",
            side_effect=unrepairable_route,
        ):
            res_fail = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "How do Raft terms behave?",
                    "idempotency_key": fail_key,
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert res_fail.status_code == 422
        assert res_fail.json()["error"]["code"] == "CITATION_VALIDATION_FAILED"
        assert unrepairable_calls == 2  # Initial + max 1 bounded repair pass

    pool = get_pool()
    async with pool.acquire() as conn:
        asst_row = await conn.fetchrow(
            "SELECT status, metadata->>'error_code' AS err_code FROM public.chat_messages WHERE conversation_id = $1 AND role = 'assistant' ORDER BY sequence_number DESC LIMIT 1;",
            conv_id,
        )
    assert asst_row is not None
    assert asst_row["status"] == "failed"
    assert asst_row["err_code"] == "CITATION_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_authorization_and_entitlement_rejections_before_llm_calls():
    """Verify pending, suspended, non-contributor, cross-user, archived conv, and inactive subject fail before LLM I/O."""
    owner_id, owner_token = await _create_user(prefix="auth_owner", approval_status="approved")
    await _grant_contributor_credential(owner_id)

    pending_id, pending_token = await _create_user(prefix="auth_pend", approval_status="pending")
    suspended_id, suspended_token = await _create_user(
        prefix="auth_susp", approval_status="suspended"
    )

    no_key_id, no_key_token = await _create_user(prefix="auth_nokey", approval_status="approved")
    paid_key_id, paid_key_token = await _create_user(prefix="auth_paid", approval_status="approved")
    await _grant_contributor_credential(paid_key_id, billing_tier="paid")

    other_contrib_id, other_token = await _create_user(
        prefix="auth_other", approval_status="approved"
    )
    await _grant_contributor_credential(other_contrib_id)

    kb_active = await _seed_subject_with_kb(
        subject_name="Algorithms",
        topic_name="Graphs",
        doc_title="CLRS.pdf",
        passage_text="Dijkstra finds shortest paths with non-negative weights.",
    )
    kb_inactive = await _seed_subject_with_kb(
        subject_name="Archived Course",
        topic_name="Legacy",
        doc_title="Legacy.pdf",
        passage_text="Legacy content should not execute generation.",
        subject_active=False,
    )

    conv_active = await _create_conversation(user_id=owner_id, subject_id=kb_active["subject_id"])
    conv_archived = await _create_conversation(
        user_id=owner_id, subject_id=kb_active["subject_id"], status="archived"
    )
    conv_inactive_subj = await _create_conversation(
        user_id=owner_id, subject_id=kb_inactive["subject_id"]
    )

    spy_llm = AsyncMock(
        side_effect=AssertionError("LLM gateway must NEVER be invoked for rejected requests")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.modules.chat.orchestration.generation.route_and_dispatch", new=spy_llm):
            cases = [
                (pending_token, conv_active, 403),
                (suspended_token, conv_active, 403),
                (no_key_token, conv_active, 403),
                (paid_key_token, conv_active, 403),
                (other_token, conv_active, 403),
                (owner_token, conv_archived, 403),
                (owner_token, conv_inactive_subj, 403),
                (owner_token, uuid4(), 404),
            ]
            for tok, target_conv, expected_status in cases:
                r = await client.post(
                    f"/api/v1/chat/conversations/{target_conv}/messages",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={
                        "content": "Explain shortest paths",
                        "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                    },
                )
                assert r.status_code == expected_status, (
                    f"Expected {expected_status}, got {r.status_code}: {r.text}"
                )

            # Also ensure non-contributors/unapproved fail prepare-local & cross-user fails finalize
            r_prep_nokey = await client.post(
                f"/api/v1/chat/conversations/{conv_active}/messages/prepare-local",
                headers={"Authorization": f"Bearer {no_key_token}"},
                json={"content": "Explain shortest paths"},
            )
            assert r_prep_nokey.status_code == 403

            r_prep_pend = await client.post(
                f"/api/v1/chat/conversations/{conv_active}/messages/prepare-local",
                headers={"Authorization": f"Bearer {pending_token}"},
                json={"content": "Explain shortest paths"},
            )
            assert r_prep_pend.status_code == 403

    assert spy_llm.call_count == 0


# ------------------------------------------------------------------------------
# N7. Private Local Prepare & Finalize Flow (Zero Cloud Leakage)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_local_prepare_and_finalize_lifecycle():
    """Verify Private Local dual-endpoint flow, forged source rejection, replay protection, and TTL expiry."""
    student_id, student_token = await _create_user(prefix="local_student")
    await _grant_contributor_credential(student_id)
    other_id, other_token = await _create_user(prefix="local_other")
    await _grant_contributor_credential(other_id)

    kb = await _seed_subject_with_kb(
        subject_name="Computer Networks",
        topic_name="Transport Layer",
        doc_title="Kurose Ross Ch3.pdf",
        passage_text="TCP uses a three-way handshake (SYN, SYN-ACK, ACK) to establish reliable connections.",
        page_number=211,
    )
    conv_id = await _create_conversation(user_id=student_id, subject_id=kb["subject_id"])
    headers = {"Authorization": f"Bearer {student_token}"}

    spy_cloud = AsyncMock(
        side_effect=AssertionError(
            "Cloud route_and_dispatch must NEVER execute during Private Local flow"
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.modules.chat.orchestration.generation.route_and_dispatch", new=spy_cloud):
            # 1. prepare-local retrieves evidence & emits XML prompt
            prep = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
                headers=headers,
                json={"content": "How does TCP establish a connection?", "model_id": "llama3.2:3b"},
            )
            assert prep.status_code == 200, prep.text
            p_data = prep.json()
            assert "<study_context_evidence>" in p_data["prompt"]
            assert p_data["model_id"] == "llama3.2:3b"
            assert len(p_data["sources"]) == 1
            assert p_data["sources"][0]["citation_key"] == "S1"
            assert p_data["accounting"]["reserved_output_tokens"] > 0
            msg_id = p_data["message_id"]
            prep_req_id = p_data["prepared_request_id"]

            # 2. Cross-user finalize attempt blocked
            cross_fin = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers={"Authorization": f"Bearer {other_token}"},
                json={
                    "message_id": msg_id,
                    "prepared_request_id": prep_req_id,
                    "completion_text": "Attempted hijack [S1].",
                },
            )
            assert cross_fin.status_code == 403

            # 3. Student finalizes with valid [S1] + hallucinated [S99] + forged client source field
            fin = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": msg_id,
                    "prepared_request_id": prep_req_id,
                    "completion_text": "TCP opens connections via SYN, SYN-ACK, ACK [S1] and magic [S99].",
                    "finish_reason": "stop",
                    "token_usage": {
                        "prompt_tokens": 180,
                        "completion_tokens": 30,
                        "total_tokens": 210,
                    },
                    "sources": [{"citation_key": "S99", "excerpt": "Forged client source"}],
                },
            )
            assert fin.status_code == 200, fin.text
            f_data = fin.json()
            assert f_data["status"] == "completed"
            assert f_data["valid_citations"] == ["S1"]
            assert f_data["invalid_citations_stripped"] == ["S99"]
            assert f_data["citation_validation_status"] == "repaired_stripped_unverified"
            assert len(f_data["sources"]) == 1
            assert f_data["sources"][0]["citation_key"] == "S1"
            assert f_data["sources"][0]["referenced_in_answer"] is True

            # Verify [S99] stripped in stored DB content
            pool = get_pool()
            async with pool.acquire() as conn:
                stored = await conn.fetchrow(
                    "SELECT content, status, provider_id FROM public.chat_messages WHERE id = $1;",
                    UUID(msg_id),
                )
            assert stored["status"] == "completed"
            assert stored["provider_id"] == "ollama"
            assert "[S99]" not in stored["content"]
            assert "[S1]" in stored["content"]

            # 4. Single-use finalize replay blocked cleanly with 409 ALREADY_FINALIZED
            replay_fin = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": msg_id,
                    "prepared_request_id": prep_req_id,
                    "completion_text": "Replay attempt [S1].",
                },
            )
            assert replay_fin.status_code == 409
            assert replay_fin.json()["error"]["code"] == "ALREADY_FINALIZED"

            # 5. Expired prepared request rejected & marked failed
            prep_exp = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
                headers=headers,
                json={"content": "Explain TCP teardown.", "model_id": "llama3.2:3b"},
            )
            assert prep_exp.status_code == 200
            exp_id = UUID(prep_exp.json()["message_id"])
            past_iso = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE public.chat_messages
                    SET metadata = jsonb_set(metadata, '{prepared_expires_at}', to_jsonb($2::text))
                    WHERE id = $1;
                    """,
                    exp_id,
                    past_iso,
                )

            fin_exp = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                headers=headers,
                json={
                    "message_id": str(exp_id),
                    "prepared_request_id": prep_exp.json()["prepared_request_id"],
                    "completion_text": "FIN-ACK teardown [S1].",
                },
            )
            assert fin_exp.status_code == 409
            assert fin_exp.json()["error"]["code"] == "PREPARED_REQUEST_EXPIRED"

            async with pool.acquire() as conn:
                exp_row = await conn.fetchrow(
                    "SELECT status, metadata->>'error_code' AS err FROM public.chat_messages WHERE id = $1;",
                    exp_id,
                )
            assert exp_row["status"] == "failed"
            assert exp_row["err"] == "PREPARED_REQUEST_EXPIRED"

    assert spy_cloud.call_count == 0


# ------------------------------------------------------------------------------
# N8. Historical Source Visibility Revalidation & Moderation Masking
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_source_visibility_revalidation_and_masking():
    """Verify live 3-way catalog revalidation masks archived/inactive/deleted docs while preserving re-indexed chunks."""
    owner_id, owner_token = await _create_user(prefix="hist_owner")
    await _grant_contributor_credential(owner_id)
    other_id, other_token = await _create_user(prefix="hist_other")

    passage = "Virtual memory separates logical address space from physical DRAM pages using a page table."
    kb = await _seed_subject_with_kb(
        subject_name="Systems Engineering",
        topic_name="Virtual Memory",
        doc_title="Tanenbaum Ch3.pdf",
        passage_text=passage,
        page_number=195,
    )
    conv_id = await _create_conversation(user_id=owner_id, subject_id=kb["subject_id"])

    async def ok_cloud(*, user_id, req, correlation_id=None):
        return _make_cloud_completion("Page tables map virtual addresses to physical DRAM [S1].")

    headers = {"Authorization": f"Bearer {owner_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch(
            "app.modules.chat.orchestration.generation.route_and_dispatch", side_effect=ok_cloud
        ):
            turn = await client.post(
                f"/api/v1/chat/conversations/{conv_id}/messages",
                headers=headers,
                json={
                    "content": "What does a page table do?",
                    "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                },
            )
        assert turn.status_code == 200
        asst_id = turn.json()["assistant_message"]["id"]

        # 1. Cross-user query -> 404
        r_cross = await client.get(
            f"/api/v1/chat/messages/{asst_id}/sources",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r_cross.status_code == 404

        # 2. Available initially
        r_avail = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        assert r_avail.status_code == 200
        s0 = r_avail.json()["sources"][0]
        assert s0["is_available"] is True
        assert passage in s0["excerpt"]
        assert s0["document_viewer_url"] is not None

        pool = get_pool()
        # 3. Document archived -> masked
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_documents SET is_archived = true WHERE id = $1;",
                kb["document_id"],
            )
        r_arch = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        s_arch = r_arch.json()["sources"][0]
        assert s_arch["is_available"] is False
        assert s_arch["excerpt"] == UNAVAILABLE_SOURCE_PLACEHOLDER
        assert s_arch["document_viewer_url"] is None
        assert s_arch["document_title"] == "Tanenbaum Ch3.pdf"

        # Restore document -> restored cleanly
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_documents SET is_archived = false WHERE id = $1;",
                kb["document_id"],
            )
        r_rest = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        assert r_rest.json()["sources"][0]["is_available"] is True

        # 4. Topic deactivated -> masked
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_topics SET is_active = false WHERE id = $1;", kb["topic_id"]
            )
        r_topic = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        assert r_topic.json()["sources"][0]["is_available"] is False
        assert r_topic.json()["sources"][0]["excerpt"] == UNAVAILABLE_SOURCE_PLACEHOLDER
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_topics SET is_active = true WHERE id = $1;", kb["topic_id"]
            )

        # 5. Subject deactivated -> masked
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_subjects SET is_active = false WHERE id = $1;", kb["subject_id"]
            )
        r_subj = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        assert r_subj.json()["sources"][0]["is_available"] is False
        assert r_subj.json()["sources"][0]["document_viewer_url"] is None
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_subjects SET is_active = true WHERE id = $1;", kb["subject_id"]
            )

        # 6. Extraction status becomes failed -> masked
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_documents SET extraction_status = 'failed' WHERE id = $1;",
                kb["document_id"],
            )
        r_ext = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        assert r_ext.json()["sources"][0]["is_available"] is False
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.kb_documents SET extraction_status = 'completed' WHERE id = $1;",
                kb["document_id"],
            )

        # 7. Chunk deleted during document re-indexing -> snapshot & document link STILL preserved & available!
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.kb_document_chunks WHERE id = $1;", kb["chunk_id"]
            )
        r_reindex = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        s_re = r_reindex.json()["sources"][0]
        assert s_re["is_available"] is True
        assert passage in s_re["excerpt"]
        assert s_re["document_viewer_url"] is not None

        # 8. Document permanently deleted -> snapshot row survives (ON DELETE SET NULL), excerpt masked safely!
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM public.kb_documents WHERE id = $1;", kb["document_id"])
        r_del = await client.get(f"/api/v1/chat/messages/{asst_id}/sources", headers=headers)
        s_del = r_del.json()["sources"][0]
        assert s_del["is_available"] is False
        assert s_del["excerpt"] == UNAVAILABLE_SOURCE_PLACEHOLDER
        assert s_del["document_viewer_url"] is None
        assert s_del["document_title"] == "Tanenbaum Ch3.pdf"
        assert s_del["document_id"] == str(kb["document_id"])
