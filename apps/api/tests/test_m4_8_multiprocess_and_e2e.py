"""M4.8 Real-Service E2E, SSE Lifecycle, Cancellation, Security & Multi-Process Redis Tests."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.redis import close_redis, get_redis, init_redis
from app.modules.chat.citations.schemas import UNAVAILABLE_SOURCE_PLACEHOLDER
from app.modules.chat.orchestration.semaphores import (
    GLOBAL_CLOUD_SEMAPHORE_KEY,
    GLOBAL_CLOUD_WAITERS_KEY,
    release_cloud_permits,
    try_acquire_cloud_permits,
)
from app.modules.knowledge_base.embeddings import embed_passages
from tests.conftest import create_test_jwt

API_ROOT = Path(__file__).resolve().parent.parent
CLEANUP_USERS: list[UUID] = []
CLEANUP_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def _isolated_m48_lifecycle():
    await init_db_pool()
    await init_redis()
    redis_conn = get_redis()
    if redis_conn is not None:
        await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
    yield
    pool = get_pool()
    async with pool.acquire() as conn:
        if CLEANUP_USERS:
            await conn.execute(
                "DELETE FROM public.chat_conversations WHERE user_id = ANY($1::uuid[])",
                CLEANUP_USERS,
            )
            await conn.execute(
                "DELETE FROM public.api_key_metadata WHERE owner_id = ANY($1::uuid[])",
                CLEANUP_USERS,
            )
        if CLEANUP_SUBJECTS:
            await conn.execute(
                "DELETE FROM public.kb_documents WHERE subject_id = ANY($1::uuid[])",
                CLEANUP_SUBJECTS,
            )
            await conn.execute(
                "DELETE FROM public.kb_topics WHERE subject_id = ANY($1::uuid[])", CLEANUP_SUBJECTS
            )
            await conn.execute(
                "DELETE FROM public.kb_subjects WHERE id = ANY($1::uuid[])", CLEANUP_SUBJECTS
            )
        if CLEANUP_USERS:
            await conn.execute("DELETE FROM auth.users WHERE id = ANY($1::uuid[])", CLEANUP_USERS)
    CLEANUP_USERS.clear()
    CLEANUP_SUBJECTS.clear()
    redis_conn = get_redis()
    if redis_conn is not None:
        await redis_conn.delete(GLOBAL_CLOUD_SEMAPHORE_KEY, GLOBAL_CLOUD_WAITERS_KEY)
    await close_redis()
    await close_db_pool()


async def _provision_student(
    *,
    prefix: str = "m48_e2e",
    approval_status: str = "approved",
    role: str = "user",
    contributor: bool = True,
) -> tuple[UUID, str]:
    uid = uuid4()
    email = f"{prefix}_{uid.hex[:8]}@test.edu"
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb)
            """,
            uid,
            email,
        )
        await conn.execute(
            "UPDATE public.profiles SET approval_status = $2, role = $3 WHERE id = $1",
            uid,
            approval_status,
            role,
        )
        if contributor:
            await conn.execute(
                """
                INSERT INTO public.api_key_metadata (
                    id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                    status, validation_status, sharing_scope, shared_consent,
                    sharing_consent_timestamp, consent_version,
                    billing_tier, billing_verified_at, billing_verification_source
                )
                VALUES ($1, $2, 'openrouter', 'M4.8 E2E Free Key', 'sk-or-...free', $3,
                        'active', 'valid', 'community_shared', true, now(), 'v1',
                        'verified_free_only', now(), 'm48_e2e')
                """,
                uuid4(),
                uid,
                f"fp_{uid.hex[:16]}",
            )
    CLEANUP_USERS.append(uid)
    return uid, create_test_jwt(user_id=uid, email=email)


async def _provision_subject_kb() -> dict[str, UUID]:
    sid, tid, did, cid = uuid4(), uuid4(), uuid4(), uuid4()
    passage = (
        "Paging divides physical memory into fixed 4 KB frames and virtual addresses into equal pages, "
        "eliminating external fragmentation. Effective Access Time with 90% TLB hit rate is 120 ns."
    )
    emb = embed_passages([passage])[0]
    vec_str = f"[{','.join(f'{x:.8f}' for x in emb.vector)}]"
    content_hash = f"m48_{cid.hex}"
    spans = json.dumps([{"page_number": 12, "start_char": 0, "end_char": len(passage)}])

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.kb_subjects (id, name, slug, description, is_active) VALUES ($1, $2, $3, 'M4.8 E2E Subject', true)",
            sid,
            f"Operating Systems {sid.hex[:6]}",
            f"m48-e2e-{sid.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active) VALUES ($1, $2, 'Memory Management', $3, true)",
            tid,
            sid,
            f"m48-topic-{tid.hex[:8]}",
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
            VALUES ($1, $2, $3, 'OS Textbook E2E', 'os_e2e.pdf', $4, 'application/pdf', 10240,
                    'uploaded', 'completed', 'indexed', 1, 'embedded', $5, false)
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
            VALUES ($1, $2, 1, 0, 12, 12, $3, length($3), 42, $4::jsonb, $5, true)
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
            VALUES ($1, $2, 1, 'intfloat/multilingual-e5-small', $3, 384, $4::text::extensions.vector, $5)
            """,
            cid,
            did,
            emb.model_fingerprint,
            vec_str,
            content_hash,
        )
    CLEANUP_SUBJECTS.append(sid)
    return {"subject_id": sid, "topic_id": tid, "document_id": did, "chunk_id": cid}


@asynccontextmanager
async def _spawn_worker_process(
    port: int,
    *,
    env_overrides: dict[str, str] | None = None,
) -> AsyncIterator[subprocess.Popen[str]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(API_ROOT)
    env["M48_WORKER_PORT"] = str(port)
    env.setdefault("M48_STUB_DELAY_SECONDS", "0.35")
    env.setdefault("M48_STUB_STEPS", "4")
    if env_overrides:
        env.update(env_overrides)

    import tempfile

    log_f = tempfile.NamedTemporaryFile(
        mode="w+", delete=False, suffix=f"_w{port}.log", encoding="utf-8"
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.evaluation.multiprocess_worker_app"],
        cwd=str(API_ROOT),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        async with httpx.AsyncClient() as client:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    log_f.seek(0)
                    out = log_f.read()
                    raise RuntimeError(f"Worker on {port} exited early ({proc.returncode}):\n{out}")
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/health/live", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.25)
            else:
                log_f.seek(0)
                raise RuntimeError(
                    f"Worker on port {port} failed health probe within 20s:\n{log_f.read()}"
                )
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        log_f.close()
        try:
            Path(log_f.name).unlink(missing_ok=True)
        except OSError:
            pass


def _parse_sse_frames(raw_text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in raw_text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":")]
        if not lines:
            continue
        ev_name = "message"
        ev_id = None
        data_parts: list[str] = []
        for ln in lines:
            if ln.startswith("event:"):
                ev_name = ln.split(":", 1)[1].strip()
            elif ln.startswith("id:"):
                ev_id = ln.split(":", 1)[1].strip()
            elif ln.startswith("data:"):
                data_parts.append(ln.split(":", 1)[1].strip())
        if data_parts:
            frames.append(
                {"id": ev_id, "event": ev_name, "data": json.loads("\n".join(data_parts))}
            )
    return frames


@pytest.mark.asyncio
async def test_multiprocess_redis_limits_load_saturation_timeout_and_crash_recovery():
    """Verify real multi-process OS FastAPI workers enforce 1/conv, 2/user, 10/global, queue drain, timeout, and crash TTL recovery."""
    kb = await _provision_subject_kb()
    cohort: list[tuple[UUID, str, UUID]] = []
    for idx in range(12):
        uid, tok = await _provision_student(prefix=f"mp_load_{idx}")
        pool = get_pool()
        conv_id = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, $4, 'active')",
                conv_id,
                uid,
                kb["subject_id"],
                f"MP Conv {idx}",
            )
        cohort.append((uid, tok, conv_id))

    # Give user 0 two extra conversations for 2/user test
    u0, tok0, c0 = cohort[0]
    pool = get_pool()
    c0_b, c0_c = uuid4(), uuid4()
    async with pool.acquire() as conn:
        for cid, title in ((c0_b, "U0 Conv B"), (c0_c, "U0 Conv C")):
            await conn.execute(
                "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, $4, 'active')",
                cid,
                u0,
                kb["subject_id"],
                title,
            )

    w1_url = "http://127.0.0.1:18101"
    w2_url = "http://127.0.0.1:18102"

    async with _spawn_worker_process(18101), _spawn_worker_process(18102):
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Per-conversation limit = 1 across Worker 1 and Worker 2
            async def _hold_slow(url: str, tok: str, cid: UUID, key: str) -> httpx.Response:
                return await client.post(
                    f"{url}/api/v1/chat/conversations/{cid}/messages",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={
                        "content": "SLOW_HOLD Explain paging frames",
                        "idempotency_key": key,
                        "stream": True,
                    },
                )

            t_hold = asyncio.create_task(
                _hold_slow(w1_url, tok0, c0, f"mp-conv1-{uuid4().hex[:6]}")
            )
            await asyncio.sleep(0.6)
            r_dup = await client.post(
                f"{w2_url}/api/v1/chat/conversations/{c0}/messages",
                headers={"Authorization": f"Bearer {tok0}"},
                json={
                    "content": "Concurrent conv hit",
                    "idempotency_key": f"mp-conv2-{uuid4().hex[:6]}",
                    "stream": True,
                },
            )
            assert r_dup.status_code == 409
            assert r_dup.json()["error"]["code"] == "CONVERSATION_CONCURRENCY_LIMIT"

            # 2. Per-user limit = 2 across distinct conversations and processes
            t_u0_b = asyncio.create_task(
                _hold_slow(w2_url, tok0, c0_b, f"mp-u0b-{uuid4().hex[:6]}")
            )
            await asyncio.sleep(0.5)
            r_u0_3 = await client.post(
                f"{w1_url}/api/v1/chat/conversations/{c0_c}/messages",
                headers={"Authorization": f"Bearer {tok0}"},
                json={
                    "content": "Third user stream",
                    "idempotency_key": f"mp-u0c-{uuid4().hex[:6]}",
                    "stream": True,
                },
            )
            assert r_u0_3.status_code == 429
            assert r_u0_3.json()["error"]["code"] == "USER_CONCURRENCY_LIMIT"
            await asyncio.gather(t_hold, t_u0_b)

            # 3. 20-request Multi-Process Saturation Burst (distributed across W1 and W2)
            # Use 12 users (2 extra convs cleanly spread -> 20 distinct conversations)
            burst_targets: list[tuple[str, str, UUID]] = []
            for idx in range(20):
                u_uid, u_tok, u_cid = cohort[idx % 12]
                if idx >= 12:
                    extra_cid = uuid4()
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, $4, 'active')",
                            extra_cid,
                            u_uid,
                            kb["subject_id"],
                            f"Burst Extra {idx}",
                        )
                    u_cid = extra_cid
                burst_targets.append((w1_url if idx % 2 == 0 else w2_url, u_tok, u_cid))

            redis_conn = get_redis()
            assert redis_conn is not None
            peak_global = 0
            monitoring = True

            async def _monitor_peak() -> None:
                nonlocal peak_global
                while monitoring:
                    cnt = await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY)
                    if cnt > peak_global:
                        peak_global = cnt
                    await asyncio.sleep(0.05)

            mon_task = asyncio.create_task(_monitor_peak())

            async def _fire_burst(
                item: tuple[str, str, UUID], i: int
            ) -> tuple[int, list[dict[str, Any]], float]:
                url, tok, cid = item
                t0 = time.perf_counter()
                r = await client.post(
                    f"{url}/api/v1/chat/conversations/{cid}/messages",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={
                        "content": f"Burst question {i} paging frames",
                        "idempotency_key": f"burst-{i}-{uuid4().hex[:6]}",
                        "stream": True,
                    },
                )
                return r.status_code, _parse_sse_frames(r.text), (time.perf_counter() - t0) * 1000.0

            burst_results = await asyncio.gather(
                *[_fire_burst(item, i) for i, item in enumerate(burst_targets)]
            )
            monitoring = False
            await mon_task

            assert all(code == 200 for code, _, _ in burst_results)
            completed_events = sum(
                1
                for _, evs, _ in burst_results
                if any(e["event"] == "generation_completed" for e in evs)
            )
            queued_events = sum(
                1
                for _, evs, _ in burst_results
                if any(e["event"] == "admission_waiting" for e in evs)
            )
            assert completed_events == 20
            assert queued_events >= 1
            assert 1 <= peak_global <= 10
            assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0
            assert await redis_conn.zcard(GLOBAL_CLOUD_WAITERS_KEY) == 0

            # 4. Private Local bypasses global cloud 10/10 saturation completely
            local_uid, local_tok, local_cid = cohort[11]
            prep = await client.post(
                f"{w1_url}/api/v1/chat/conversations/{local_cid}/messages/prepare-local",
                headers={"Authorization": f"Bearer {local_tok}"},
                json={
                    "content": "Explain paging locally",
                    "model_id": "llama3.2:3b",
                    "idempotency_key": f"loc-sat-{uuid4().hex[:6]}",
                },
            )
            assert prep.status_code == 200

            latencies = sorted(lat for _, _, lat in burst_results)
            p50 = statistics.median(latencies)
            p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
            print(
                f"[M4.8 MP 20-Burst] completed={completed_events}/20 queued={queued_events} peak_global={peak_global} p50={p50:.1f}ms p95={p95:.1f}ms"
            )

    # 5. Admission timeout + Worker SIGKILL crash recovery (TTL=5s, heartbeat=1s)
    async with _spawn_worker_process(
        18103,
        env_overrides={
            "RAGBAIT_GENERATION_LEASE_TTL_SECONDS": "5",
            "RAGBAIT_GENERATION_HEARTBEAT_SECONDS": "1",
            "RAGBAIT_GENERATION_ADMISSION_TIMEOUT_SECONDS": "1",
        },
    ) as crash_proc:
        async with httpx.AsyncClient(timeout=15.0) as client:
            u_crash, tok_crash, c_crash = cohort[1]
            # Start very slow stream that would hold slot, then abruptly SIGKILL the OS worker!
            req_task = asyncio.create_task(
                client.post(
                    f"http://127.0.0.1:18103/api/v1/chat/conversations/{c_crash}/messages",
                    headers={"Authorization": f"Bearer {tok_crash}"},
                    json={
                        "content": "VERY_SLOW_CRASH test",
                        "idempotency_key": f"crash-{uuid4().hex[:6]}",
                        "stream": True,
                    },
                )
            )
            redis_conn = get_redis()
            assert redis_conn is not None
            for _ in range(30):
                if await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) >= 1:
                    break
                await asyncio.sleep(0.1)
            assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 1

            # Hard-kill OS process without clean shutdown
            crash_proc.kill()
            crash_proc.wait(timeout=3)
            with pytest.raises(httpx.HTTPError):
                await req_task

            # Wait past 5s lease TTL, then confirm clean recovery on fresh worker
            await asyncio.sleep(5.5)
            async with _spawn_worker_process(18101):
                _, tok_ok, c_ok = cohort[2]
                r_rec = await client.post(
                    f"http://127.0.0.1:18101/api/v1/chat/conversations/{c_ok}/messages",
                    headers={"Authorization": f"Bearer {tok_ok}"},
                    json={
                        "content": "Post crash recovery test",
                        "idempotency_key": f"rec-{uuid4().hex[:6]}",
                        "stream": True,
                    },
                )
                assert r_rec.status_code == 200
                assert any(
                    e["event"] == "generation_completed" for e in _parse_sse_frames(r_rec.text)
                )
                assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0

    # 6. Bounded admission wait timeout (RATE_LIMITED) + zero leaked permits/waiters
    async with _spawn_worker_process(
        18103,
        env_overrides={"RAGBAIT_GENERATION_ADMISSION_TIMEOUT_SECONDS": "1"},
    ):
        redis_conn = get_redis()
        assert redis_conn is not None
        held_permits = []
        for _ in range(10):
            p, _, _ = await try_acquire_cloud_permits(conversation_id=uuid4(), user_id=uuid4())
            assert p is not None
            held_permits.append(p)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                _, tok_wait, c_wait = cohort[3]
                r_to = await client.post(
                    f"http://127.0.0.1:18103/api/v1/chat/conversations/{c_wait}/messages",
                    headers={"Authorization": f"Bearer {tok_wait}"},
                    json={
                        "content": "Queue timeout test",
                        "idempotency_key": f"to-{uuid4().hex[:6]}",
                        "stream": True,
                    },
                )
                assert r_to.status_code == 200
                evs_to = _parse_sse_frames(r_to.text)
                assert any(e["event"] == "admission_waiting" for e in evs_to), f"Events: {evs_to}"
                assert evs_to[-1]["event"] == "generation_failed"
                assert evs_to[-1]["data"]["code"] == "RATE_LIMITED"
                assert await redis_conn.zcard(GLOBAL_CLOUD_WAITERS_KEY) == 0
        finally:
            for p in held_permits:
                await release_cloud_permits(p)
            assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0

    # 7. Real Redis outage fail-closed behavior (isolated worker pointing at closed port 6399)
    async with _spawn_worker_process(
        18104,
        env_overrides={"REDIS_URL": "redis://127.0.0.1:6399/0"},
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            _, tok_outage, c_outage = cohort[4]
            r_cloud_fail = await client.post(
                f"http://127.0.0.1:18104/api/v1/chat/conversations/{c_outage}/messages",
                headers={"Authorization": f"Bearer {tok_outage}"},
                json={
                    "content": "Cloud outage fail closed",
                    "idempotency_key": f"out-{uuid4().hex[:6]}",
                    "stream": True,
                },
            )
            assert r_cloud_fail.status_code == 503
            r_local_ok = await client.post(
                f"http://127.0.0.1:18104/api/v1/chat/conversations/{c_outage}/messages/prepare-local",
                headers={"Authorization": f"Bearer {tok_outage}"},
                json={
                    "content": "Local outage bypass",
                    "model_id": "llama3.2:3b",
                    "idempotency_key": f"locout-{uuid4().hex[:6]}",
                },
            )
            assert r_local_ok.status_code == 200


@pytest.mark.asyncio
async def test_cross_process_sse_lifecycle_replay_and_cancellation_races():
    """Verify SSE ordering, content_replaced citation repair, idempotent replay, and cross-process cancel."""
    kb = await _provision_subject_kb()
    owner_id, owner_tok = await _provision_student(prefix="mp_sse_owner")
    other_id, other_tok = await _provision_student(prefix="mp_sse_other")

    pool = get_pool()
    conv_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, 'SSE Race Conv', 'active')",
            conv_id,
            owner_id,
            kb["subject_id"],
        )

    w1 = "http://127.0.0.1:18101"
    w2 = "http://127.0.0.1:18102"

    async with _spawn_worker_process(18101), _spawn_worker_process(18102):
        async with httpx.AsyncClient(timeout=20.0) as client:
            # 1. Full SSE lifecycle with repair (TRIGGER_REPAIR -> [S99] -> content_replaced -> generation_completed)
            idem = f"sse-rep-{uuid4().hex[:8]}"
            r1 = await client.post(
                f"{w1}/api/v1/chat/conversations/{conv_id}/messages",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={
                    "content": "TRIGGER_REPAIR Explain paging",
                    "idempotency_key": idem,
                    "stream": True,
                },
            )
            assert r1.status_code == 200
            frames = _parse_sse_frames(r1.text)
            ev_names = [f["event"] for f in frames]
            assert "generation_started" in ev_names, f"Unexpected SSE frames: {frames}"
            assert "sources_identified" in ev_names
            assert "text_delta" in ev_names
            assert "content_replaced" in ev_names
            assert ev_names[-1] == "generation_completed"
            completed_data = frames[-1]["data"]
            assert "[S99]" not in completed_data["content"]
            msg_id = completed_data["message_id"]

            # 2. Safe idempotent replay on Worker 2: zero duplicate assistant rows, zero re-generation
            r_replay = await client.post(
                f"{w2}/api/v1/chat/conversations/{conv_id}/messages",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={
                    "content": "TRIGGER_REPAIR Explain paging",
                    "idempotency_key": idem,
                    "stream": True,
                },
            )
            assert r_replay.status_code == 200
            rep_frames = _parse_sse_frames(r_replay.text)
            assert rep_frames[-1]["event"] == "generation_completed"
            assert rep_frames[-1]["data"].get("replayed") is True
            assert rep_frames[-1]["data"]["message_id"] == msg_id

            # Post-completion cancellation attempt -> 409 conflict
            c_done = await client.post(
                f"{w2}/api/v1/chat/conversations/{conv_id}/messages/{msg_id}/cancel",
                headers={"Authorization": f"Bearer {owner_tok}"},
            )
            assert c_done.status_code == 409

            # Non-owner cancellation attempt -> 404
            c_other = await client.post(
                f"{w2}/api/v1/chat/conversations/{conv_id}/messages/{msg_id}/cancel",
                headers={"Authorization": f"Bearer {other_tok}"},
            )
            assert c_other.status_code == 404

            # 3. Active cross-process cancellation: stream SLOW_HOLD on Worker 1, cancel from Worker 2
            active_msg_id: str | None = None

            async def _read_slow_stream() -> list[dict[str, Any]]:
                nonlocal active_msg_id
                collected: list[dict[str, Any]] = []
                async with client.stream(
                    "POST",
                    f"{w1}/api/v1/chat/conversations/{conv_id}/messages",
                    headers={"Authorization": f"Bearer {owner_tok}"},
                    json={
                        "content": "SLOW_HOLD Explain TLB",
                        "idempotency_key": f"cancel-{uuid4().hex[:8]}",
                        "stream": True,
                    },
                ) as resp:
                    assert resp.status_code == 200
                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            blk, buf = buf.split("\n\n", 1)
                            parsed = _parse_sse_frames(blk + "\n\n")
                            for ev in parsed:
                                collected.append(ev)
                                if ev["event"] == "generation_started":
                                    active_msg_id = ev["data"]["message_id"]
                return collected

            stream_task = asyncio.create_task(_read_slow_stream())
            for _ in range(50):
                if active_msg_id is not None:
                    break
                await asyncio.sleep(0.1)
            assert active_msg_id is not None

            await asyncio.sleep(0.35)
            cancel_res = await client.post(
                f"{w2}/api/v1/chat/conversations/{conv_id}/messages/{active_msg_id}/cancel",
                headers={"Authorization": f"Bearer {owner_tok}"},
            )
            assert cancel_res.status_code == 200
            cancel_ev_list = await stream_task
            assert any(e["event"] == "generation_cancelled" for e in cancel_ev_list)

            # Idempotent second cancel -> 200 already_cancelled=True
            repeat_cancel = await client.post(
                f"{w1}/api/v1/chat/conversations/{conv_id}/messages/{active_msg_id}/cancel",
                headers={"Authorization": f"Bearer {owner_tok}"},
            )
            assert repeat_cancel.status_code == 200
            assert repeat_cancel.json()["already_cancelled"] is True

            redis_conn = get_redis()
            assert redis_conn is not None
            assert await redis_conn.zcard(GLOBAL_CLOUD_SEMAPHORE_KEY) == 0


@pytest.mark.asyncio
async def test_e2e_auth_matrix_source_revocation_upload_gate_and_live_ollama():
    """Verify full E2E auth matrix, source revocation masking, direct upload API state, and real local Ollama."""
    kb = await _provision_subject_kb()
    contrib_id, contrib_tok = await _provision_student(prefix="e2e_contrib", contributor=True)
    other_contrib_id, other_contrib_tok = await _provision_student(
        prefix="e2e_othercontrib", contributor=True
    )
    non_contrib_id, non_contrib_tok = await _provision_student(
        prefix="e2e_noncontrib", contributor=False
    )
    _, pending_tok = await _provision_student(
        prefix="e2e_pending", approval_status="pending", contributor=False
    )
    _, rejected_tok = await _provision_student(
        prefix="e2e_rejected", approval_status="rejected", contributor=False
    )
    _, suspended_tok = await _provision_student(
        prefix="e2e_suspended", approval_status="suspended", contributor=False
    )
    _, admin_tok = await _provision_student(prefix="e2e_admin", role="admin", contributor=True)

    # Seed pre-existing conversation for approved non-contributor to verify history browse/rename permissions
    pool = get_pool()
    non_contrib_conv = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status) VALUES ($1, $2, $3, 'Existing History', 'active')",
            non_contrib_conv,
            non_contrib_id,
            kb["subject_id"],
        )

    async with _spawn_worker_process(18101):
        base = "http://127.0.0.1:18101"
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Auth & User-State Matrix
            # Unauthenticated & Expired JWT -> 401
            assert (await client.get(f"{base}/api/v1/chat/conversations")).status_code == 401
            expired_tok = create_test_jwt(user_id=contrib_id, expires_in_minutes=-10)
            assert (
                await client.get(
                    f"{base}/api/v1/chat/conversations",
                    headers={"Authorization": f"Bearer {expired_tok}"},
                )
            ).status_code == 401

            # Pending / Rejected / Suspended -> 403
            for bad_tok in (pending_tok, rejected_tok, suspended_tok):
                r_bad = await client.get(
                    f"{base}/api/v1/chat/conversations",
                    headers={"Authorization": f"Bearer {bad_tok}"},
                )
                assert r_bad.status_code == 403

            # Approved non-contributor: KB browse OK; ALL Chat/LLM/Routing gated 403 CHAT_CONTRIBUTION_REQUIRED
            assert (
                await client.get(
                    f"{base}/api/v1/kb/subjects",
                    headers={"Authorization": f"Bearer {non_contrib_tok}"},
                )
            ).status_code == 200
            for method, url, payload in (
                ("GET", f"{base}/api/v1/chat/conversations", None),
                (
                    "PATCH",
                    f"{base}/api/v1/chat/conversations/{non_contrib_conv}",
                    {"title": "Blocked Rename"},
                ),
                (
                    "POST",
                    f"{base}/api/v1/chat/conversations",
                    {"subject_id": str(kb["subject_id"]), "title": "Blocked"},
                ),
                (
                    "POST",
                    f"{base}/api/v1/chat/conversations/{non_contrib_conv}/messages",
                    {"content": "Should be gated", "stream": False},
                ),
                (
                    "POST",
                    f"{base}/api/v1/llm/chat/completions",
                    {"messages": [{"role": "user", "content": "hi"}], "stream": False},
                ),
                (
                    "POST",
                    f"{base}/api/v1/routing/preview",
                    {"messages": [{"role": "user", "content": "hi"}], "stream": False},
                ),
            ):
                r_nc = await client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {non_contrib_tok}"},
                    json=payload,
                )
                assert r_nc.status_code == 403, (
                    f"Expected 403 for {method} {url}, got {r_nc.status_code}"
                )
                assert r_nc.json()["error"]["code"] == "CHAT_CONTRIBUTION_REQUIRED"

            # 2. Contributor creates conversation, completes RAG turn, inspects sources & page URL
            r_conv = await client.post(
                f"{base}/api/v1/chat/conversations",
                headers={"Authorization": f"Bearer {contrib_tok}"},
                json={"subject_id": str(kb["subject_id"]), "title": "OS Study Thread"},
            )
            assert r_conv.status_code == 201
            conv_id = r_conv.json()["id"]

            r_turn = await client.post(
                f"{base}/api/v1/chat/conversations/{conv_id}/messages",
                headers={"Authorization": f"Bearer {contrib_tok}"},
                json={
                    "content": "Explain paging and EAT",
                    "idempotency_key": f"e2e-{uuid4().hex[:8]}",
                    "stream": False,
                },
            )
            assert r_turn.status_code == 200
            asst_msg_id = r_turn.json()["assistant_message"]["id"]

            # Non-contributor gets 403 at Gate 2; entitled other user blocked from inspecting conversation or source snapshot (404)
            assert (
                await client.get(
                    f"{base}/api/v1/chat/conversations/{conv_id}",
                    headers={"Authorization": f"Bearer {non_contrib_tok}"},
                )
            ).status_code == 403
            assert (
                await client.get(
                    f"{base}/api/v1/chat/conversations/{conv_id}",
                    headers={"Authorization": f"Bearer {other_contrib_tok}"},
                )
            ).status_code == 404
            assert (
                await client.get(
                    f"{base}/api/v1/chat/messages/{asst_msg_id}/sources",
                    headers={"Authorization": f"Bearer {other_contrib_tok}"},
                )
            ).status_code == 404

            # Owner inspects live source -> available + exact /knowledge-base/document/{id}?page=12 URL
            r_src_before = await client.get(
                f"{base}/api/v1/chat/messages/{asst_msg_id}/sources",
                headers={"Authorization": f"Bearer {contrib_tok}"},
            )
            assert r_src_before.status_code == 200
            srcs_before = r_src_before.json()["sources"]
            assert len(srcs_before) >= 1
            assert srcs_before[0]["is_available"] is True
            assert (
                srcs_before[0]["document_viewer_url"]
                == f"/knowledge-base/document/{kb['document_id']}?page=12"
            )

            # 3. Source Revocation via real Admin Archive API -> immediate snapshot masking & KB 404
            r_arch = await client.post(
                f"{base}/api/v1/kb/admin/documents/{kb['document_id']}/archive",
                headers={"Authorization": f"Bearer {admin_tok}"},
                json={"reason": "M4.8 E2E Moderation Test"},
            )
            assert r_arch.status_code == 200

            r_src_after = await client.get(
                f"{base}/api/v1/chat/messages/{asst_msg_id}/sources",
                headers={"Authorization": f"Bearer {contrib_tok}"},
            )
            assert r_src_after.status_code == 200
            srcs_after = r_src_after.json()["sources"]
            assert srcs_after[0]["is_available"] is False
            assert srcs_after[0]["document_viewer_url"] is None
            assert srcs_after[0]["excerpt"] == UNAVAILABLE_SOURCE_PLACEHOLDER
            assert (
                await client.get(
                    f"{base}/api/v1/kb/documents/{kb['document_id']}",
                    headers={"Authorization": f"Bearer {contrib_tok}"},
                )
            ).status_code == 404

            # Restore doc cleanly for live Private Local Ollama test
            assert (
                await client.post(
                    f"{base}/api/v1/kb/admin/documents/{kb['document_id']}/restore",
                    headers={"Authorization": f"Bearer {admin_tok}"},
                    json={"reason": "Restore after M4.8 source check"},
                )
            ).status_code == 200

            # 4. Direct Upload Restriction Probe (Section 12.6): unapproved 403, approved student currently reachable (blocker finding!)
            upload_payload = {
                "subject_id": str(kb["subject_id"]),
                "topic_id": str(kb["topic_id"]),
                "title": "M4.8 Probe",
                "original_filename": "probe.txt",
                "mime_type": "text/plain",
                "file_size_bytes": 64,
            }
            assert (
                await client.post(f"{base}/api/v1/kb/documents/upload-intent", json=upload_payload)
            ).status_code == 401
            assert (
                await client.post(
                    f"{base}/api/v1/kb/documents/upload-intent",
                    headers={"Authorization": f"Bearer {pending_tok}"},
                    json=upload_payload,
                )
            ).status_code == 403
            r_contrib_upload = await client.post(
                f"{base}/api/v1/kb/documents/upload-intent",
                headers={"Authorization": f"Bearer {contrib_tok}"},
                json=upload_payload,
            )
            assert r_contrib_upload.status_code == 403
            assert r_contrib_upload.json()["error"]["code"] == "ADMIN_REQUIRED"

            r_admin_upload = await client.post(
                f"{base}/api/v1/kb/documents/upload-intent",
                headers={"Authorization": f"Bearer {admin_tok}"},
                json=upload_payload,
            )
            assert r_admin_upload.status_code == 201
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM public.kb_documents WHERE id = $1",
                    UUID(r_admin_upload.json()["document_id"]),
                )

            # 5. Live Local Ollama Private Local End-to-End (127.0.0.1:11434)
            try:
                tags_resp = await client.get("http://127.0.0.1:11434/api/tags", timeout=3.0)
                ollama_up = tags_resp.status_code == 200
            except Exception:
                ollama_up = False

            if ollama_up:
                models = [m["name"] for m in tags_resp.json().get("models", [])]
                target_model = (
                    "llama3.2:3b"
                    if "llama3.2:3b" in models
                    else (models[0] if models else "llama3.2:3b")
                )
                prep = await client.post(
                    f"{base}/api/v1/chat/conversations/{conv_id}/messages/prepare-local",
                    headers={"Authorization": f"Bearer {contrib_tok}"},
                    json={
                        "content": "Briefly define paging and state the EAT value with [S1].",
                        "model_id": target_model,
                    },
                )
                assert prep.status_code == 200
                prep_data = prep.json()

                oll_resp = await client.post(
                    "http://127.0.0.1:11434/api/chat",
                    json={
                        "model": target_model,
                        "messages": prep_data["messages"],
                        "stream": False,
                    },
                    timeout=90.0,
                )
                assert oll_resp.status_code == 200
                oll_text = oll_resp.json()["message"]["content"].strip()
                assert len(oll_text) > 0

                fin = await client.post(
                    f"{base}/api/v1/chat/conversations/{conv_id}/messages/finalize-local",
                    headers={"Authorization": f"Bearer {contrib_tok}"},
                    json={
                        "message_id": prep_data["message_id"],
                        "prepared_request_id": prep_data["prepared_request_id"],
                        "completion_text": oll_text,
                        "finish_reason": "stop",
                    },
                )
                assert fin.status_code == 200
                assert fin.json()["status"] == "completed"
