"""M4.6 controlled load and concurrency saturation tests (5, 10, and 20-stream burst)."""

import asyncio
import json
import statistics
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.main import app
from app.modules.chat.orchestration.config import generation_settings
from app.modules.chat.orchestration.semaphores import (
    GLOBAL_CLOUD_SEMAPHORE_KEY,
    GLOBAL_CLOUD_WAITERS_KEY,
)
from app.modules.knowledge_base.embeddings import embed_passages
from app.modules.llm_gateway.interfaces import GatewayStreamEvent, NormalizedUsage
from app.modules.routing.schemas import RouteDecision
from tests.conftest import create_test_jwt

CREATED_USERS: list[UUID] = []
CREATED_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def _setup_and_cleanup():
    await init_db_pool()
    await init_redis()
    redis_conn = get_redis()
    if redis_conn is not None:
        await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
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
    redis_conn = get_redis()
    if redis_conn is not None:
        await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
    await close_redis()


async def _create_load_cohort(
    n_students: int,
) -> list[tuple[UUID, str, UUID]]:
    """Provision n distinct students each with an approved credential and active conversation."""
    passage = "Semaphores coordinate concurrent streams safely without race conditions."
    emb = embed_passages([passage])[0]
    vec_str = f"[{','.join(f'{x:.8f}' for x in emb.vector)}]"
    sid, tid, did, cid = uuid4(), uuid4(), uuid4(), uuid4()
    spans = json.dumps([{"page_number": 1, "start_char": 0, "end_char": len(passage)}])
    content_hash = f"sha256_{cid.hex}"

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.kb_subjects (id, name, slug, description, is_active) VALUES ($1, $2, $3, 'Load', true);",
            sid,
            f"Load Subject {sid.hex[:6]}",
            f"load-{sid.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active) VALUES ($1, $2, 'Concurrency', $3, true);",
            tid,
            sid,
            f"topic-{tid.hex[:8]}",
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
                $1, $2, $3, 'Load Notes', 'notes.pdf',
                $4, 'application/pdf', 1024,
                'uploaded', 'completed', 'indexed',
                1, 'embedded', $5, false
            );
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
                id, document_id, chunking_version, chunk_index,
                page_number_start, page_number_end, raw_text,
                character_count, token_count, page_spans, content_hash, is_active
            )
            VALUES ($1, $2, 1, 0, 1, 1, $3, length($3), 24, $4::jsonb, $5, true);
            """,
            cid,
            did,
            passage,
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

    cohort: list[tuple[UUID, str, UUID]] = []
    async with pool.acquire() as conn:
        for i in range(n_students):
            uid = uuid4()
            email = f"load_{i}_{uid.hex[:6]}@test.edu"
            await conn.execute(
                """
                INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
                VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb);
                """,
                uid,
                email,
            )
            await conn.execute(
                "UPDATE public.profiles SET approval_status = 'approved', role = 'user' WHERE id = $1;",
                uid,
            )
            CREATED_USERS.append(uid)

            cred_id = uuid4()
            await conn.execute(
                """
                INSERT INTO public.api_key_metadata (
                    id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                    status, validation_status, sharing_scope, shared_consent,
                    sharing_consent_timestamp, consent_version,
                    billing_tier, billing_verified_at, billing_verification_source
                )
                VALUES (
                    $1, $2, 'openrouter', $3, 'sk-or-...load', $4,
                    'active', 'valid', 'community_shared', true,
                    now(), 'v1', 'verified_free_only', now(), 'load_fixture'
                );
                """,
                cred_id,
                uid,
                f"LoadKey {i}",
                uuid4().hex[:16],
            )

            conv_id = uuid4()
            await conn.execute(
                "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, $4, 'active');",
                conv_id,
                uid,
                sid,
                f"Load Chat {i}",
            )
            cohort.append((uid, create_test_jwt(user_id=uid, email=email), conv_id))
    return cohort


def _extract_terminal_outcome(raw: str) -> tuple[bool, bool]:
    """Return (completed, waited_in_queue)."""
    waited = "event: admission_waiting" in raw
    completed = "event: generation_completed" in raw
    return completed, waited


async def _run_controlled_burst(
    cohort: list[tuple[UUID, str, UUID]],
    *,
    stream_hold_seconds: float,
) -> dict[str, Any]:
    redis_conn = get_redis()
    assert redis_conn is not None
    peak_global = 0
    stop_sampler = asyncio.Event()

    async def _sample_redis_peak() -> None:
        nonlocal peak_global
        while not stop_sampler.is_set():
            cnt = await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY)
            if cnt > peak_global:
                peak_global = cnt
            await asyncio.sleep(0.02)

    async def _sim_stream(**kwargs: Any) -> tuple[AsyncIterator[GatewayStreamEvent], RouteDecision]:
        async def _gen() -> AsyncIterator[GatewayStreamEvent]:
            yield GatewayStreamEvent(event_type="started", provider_id="openrouter")
            await asyncio.sleep(stream_hold_seconds * 0.4)
            yield GatewayStreamEvent(event_type="delta", delta="Grounded ")
            await asyncio.sleep(stream_hold_seconds * 0.6)
            yield GatewayStreamEvent(event_type="delta", delta="response [S1].")
            yield GatewayStreamEvent(
                event_type="completed",
                finish_reason="stop",
                usage=NormalizedUsage(
                    input_tokens=100,
                    output_tokens=10,
                    total_tokens=110,
                    usage_source="authoritative",
                ),
            )

        return _gen(), RouteDecision(
            routing_mode="cloud_only",
            selected_source="cloud",
            provider_id="openrouter",
            model_id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
            resolved_model_id="meta-llama/llama-3.3-70b-instruct:free",
            decision_reason="load_test",
            created_at=datetime.now(UTC),
        )

    sampler = asyncio.create_task(_sample_redis_peak())
    transport = ASGITransport(app=app)
    latencies_ms: list[float] = []
    outcomes: list[tuple[bool, bool]] = []

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "app.modules.chat.orchestration.streaming_service.stream_grounded_cloud_generation",
                side_effect=_sim_stream,
            ):

                async def _one(jwt_tok: str, cid: UUID) -> tuple[float, bool, bool]:
                    t0 = time.perf_counter()
                    async with client.stream(
                        "POST",
                        f"/api/v1/chat/conversations/{cid}/messages",
                        headers={"Authorization": f"Bearer {jwt_tok}"},
                        json={
                            "content": "Explain semaphores",
                            "model_id": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                        },
                    ) as resp:
                        assert resp.status_code == 200
                        raw = (await resp.aread()).decode()
                    dt = (time.perf_counter() - t0) * 1000.0
                    comp, wait = _extract_terminal_outcome(raw)
                    return dt, comp, wait

                results = await asyncio.gather(*(_one(tok, cid) for _, tok, cid in cohort))
                for dt, comp, wait in results:
                    latencies_ms.append(dt)
                    outcomes.append((comp, wait))
    finally:
        stop_sampler.set()
        await sampler

    completed_count = sum(1 for c, _ in outcomes if c)
    queued_count = sum(1 for _, w in outcomes if w)
    rejected_count = len(outcomes) - completed_count
    sorted_lat = sorted(latencies_ms)
    p50 = statistics.median(sorted_lat)
    p95 = sorted_lat[min(len(sorted_lat) - 1, int(0.95 * len(sorted_lat)))]

    leaked_global = await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY)
    leaked_waiters = await redis_conn.zcard(GLOBAL_CLOUD_WAITERS_KEY)
    return {
        "streams": len(cohort),
        "completed": completed_count,
        "queued": queued_count,
        "rejected": rejected_count,
        "peak_global_active": peak_global,
        "leaked_global": leaked_global,
        "leaked_waiters": leaked_waiters,
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
    }


class TestControlledStreamLoadProfiles:
    """Measure controlled load profiles (5, 10, and 20 concurrent streams) against real Redis & PG."""

    async def test_5_10_and_20_concurrent_stream_profiles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cohort = await _create_load_cohort(20)

        # Profile 1: 5 concurrent streams -> all 5 complete immediately with 0 queuing
        m5 = await _run_controlled_burst(cohort[:5], stream_hold_seconds=0.15)
        assert m5["completed"] == 5
        assert m5["rejected"] == 0
        assert m5["peak_global_active"] <= 5
        assert m5["leaked_global"] == 0

        # Profile 2: 10 concurrent streams -> reaches 10 cap, all 10 complete, 0 leaked
        m10 = await _run_controlled_burst(cohort[:10], stream_hold_seconds=0.15)
        assert m10["completed"] == 10
        assert m10["rejected"] == 0
        assert m10["peak_global_active"] <= 10
        assert m10["leaked_global"] == 0

        # Profile 3a: 20 concurrent burst with brief holds & 5s wait -> first 10 execute while next 10 queue & all 20 complete cleanly
        monkeypatch.setattr(generation_settings, "GENERATION_ADMISSION_TIMEOUT_SECONDS", 5)
        m20_admit = await _run_controlled_burst(cohort, stream_hold_seconds=0.35)
        assert m20_admit["completed"] == 20
        assert m20_admit["queued"] >= 10
        assert m20_admit["peak_global_active"] <= 10
        assert m20_admit["leaked_global"] == 0
        assert m20_admit["leaked_waiters"] == 0

        # Profile 3b: 20 concurrent burst with long stream hold > 1s wait -> excess 10 reject cleanly as RATE_LIMITED
        monkeypatch.setattr(generation_settings, "GENERATION_ADMISSION_TIMEOUT_SECONDS", 1)
        m20_cap = await _run_controlled_burst(cohort, stream_hold_seconds=1.35)
        assert m20_cap["completed"] == 10
        assert m20_cap["rejected"] == 10
        assert m20_cap["peak_global_active"] <= 10
        assert m20_cap["leaked_global"] == 0
        assert m20_cap["leaked_waiters"] == 0

        print(
            f"LOAD_METRICS={json.dumps({'p5': m5, 'p10': m10, 'p20_queue': m20_admit, 'p20_timeout': m20_cap})}"
        )
