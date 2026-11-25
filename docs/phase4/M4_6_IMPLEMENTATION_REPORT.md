# RagBait Phase 4 — Milestone M4.6 Implementation & Verification Report

**Date:** November 25, 2026  
**Milestone:** M4.6 — Generation Reliability & Load Control  
**Branch:** `phase4`  
**Base Commit (Workstream A — M4.5 Hardening):** `36f4a46`  
**Status:** COMPLETE & VERIFIED  

---

## A. Executive Summary & Architectural Resolutions

Milestone M4.6 implements production-grade Server-Sent Events (SSE) cloud streaming, distributed cross-process Redis concurrency governance, lease heartbeat renewal, explicit generation cancellation, client disconnect detection, idempotent SSE replay, and bounded admission waiting — building directly atop hardened M4.5 (`36f4a46`) and M2.9 routing/admission without duplicating provider adapters or credential vault paths.

### Cross-Document Conflict Resolutions (Recorded in ADR 23)
1. **Cancellation Route Alignment**: Both `POST /api/v1/chat/messages/{message_id}/cancel` (authoritative) and `POST /api/v1/chat/conversations/{conversation_id}/messages/{message_id}/cancel` (conversation-scoped alias) are implemented, verifying ownership (`404` non-owner) and atomic state transitions.
2. **SSE Admission Timeout vs. Sync HTTP 429**: Conversation (`409 CONVERSATION_CONCURRENCY_LIMIT`), user (`429 USER_CONCURRENCY_LIMIT`), authorization (`403`/`404`), and Redis outage (`503 SERVICE_UNAVAILABLE`) checks reject cleanly BEFORE committing SSE headers. When only the global cap (`sem:global:cloud = 10`) is saturated on a streaming call (`stream=true`, default), the endpoint commits `HTTP 200 text/event-stream`, emits `event: admission_waiting`, waits up to 30 seconds, and on expiration emits terminal `event: generation_failed` (`code="RATE_LIMITED"`, `retryable=true`). Synchronous calls (`stream=false` without `Accept: text/event-stream`) wait and raise `HTTP 429 CONCURRENCY_TIMEOUT`.
3. **Redis Fail-Closed Cloud Policy**: Cloud generation fails closed (`HTTP 503 SERVICE_UNAVAILABLE`) when Redis is unreachable rather than falling back to unsafe per-worker in-memory locks across multi-worker deployments. Private Local Ollama (`prepare-local`/`finalize-local`) continues bypassing Redis cloud semaphores completely.
4. **`finalize-local` Replay Token**: `FinalizeLocalRequest.prepared_request_id` (`min_length=8`) remains mandatory per M4.5 hardening (`36f4a46`).

---

## B. Database Schema & Migration

- **Migration**: [20260307000000_m4_6_generation_reliability.sql](file:///d:/Projects/RagBait/supabase/migrations/20260307000000_m4_6_generation_reliability.sql) (applied cleanly to local Supabase PostgreSQL)
  - Adds partial lookup indexes on `public.chat_messages`:
    - `idx_chat_messages_gen_req_id` on `(generation_request_id) WHERE generation_request_id IS NOT NULL`
    - `idx_chat_messages_active_conv` on `(conversation_id, status) WHERE status IN ('pending', 'streaming')`
- **Database Tests (`npx supabase test db`)**: `Files=16, Tests=352, Result: PASS` (zero regressions).

---

## C. LLM Gateway & M2.9 Routing Streaming Pipeline

- **[interfaces.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/interfaces.py)**: Added typed `GatewayStreamEvent` (`started`, `delta`, `completed`, `cancelled`, `error`) and `raise_for_stream_error_payload()` to intercept upstream mid-stream JSON error payloads.
- **[streaming.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/streaming.py)**: Added optional SSE `id:` sequencing, `format_sse_comment()` keepalives, and incremental UTF-8 multibyte decoding (`include_id=True`).
- **Cloud Provider Adapters**: [gemini.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/providers/gemini.py), [groq.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/providers/groq.py), [openrouter.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/providers/openrouter.py), and [cerebras.py](file:///d:/Projects/RagBait/apps/api/app/modules/llm_gateway/providers/cerebras.py) validate mid-stream JSON error frames and map them via `map_http_status_to_category()` into sanitized `ProviderAdapterError` instances (never leaking raw API keys).
- **Routing Execution & Failover**: [executor.py](file:///d:/Projects/RagBait/apps/api/app/modules/routing/executor.py) (`PreFirstTokenCandidateError`, `stream_cloud_candidate_events`) and [service.py](file:///d:/Projects/RagBait/apps/api/app/modules/routing/service.py) (`stream_route_and_dispatch`) enforce:
  - Transparent failover across eligible cloud candidates ONLY before any visible token is emitted.
  - Strictly NO failover after visible token emission (emits sanitized `error` event and calls `mark_outcome_unknown`).
  - Single quota reservation settlement on client cancellation (`error_category="cancelled_by_client"`).

---

## D. Distributed Redis Concurrency Semaphores & Heartbeats

- **Configuration**: [config.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/config.py) (`GenerationSettings` with startup `@model_validator` bounds checking: conv limit `1`, user limit `2`, global cloud limit `10`, admission wait `30s`, lease TTL `60s`, heartbeat `15s`, keepalive `10s`, first-token timeout `30s`, idle timeout `30s`, max duration `180s`).
- **Distributed Semaphores**: [semaphores.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/semaphores.py)
  - Keys: `sem:conv:{conversation_id}`, `sem:user:{user_id}`, `sem:global:cloud`, and `sem:global:cloud:waiters`.
  - **Atomic Multi-Scope Lua Acquisition** (`ACQUIRE_CLOUD_PERMITS_LUA`): Prunes expired ZSET entries against Redis server `TIME`, enforces `1 / 2 / 10` caps in a single round-trip, and writes all 3 scopes atomically — guaranteeing zero partial leaks on rejection.
  - **Atomic Lua Lease Renewal** (`RENEW_CLOUD_PERMITS_LUA`): Extends TTL ONLY if `lease_token` exists and has not yet expired across all 3 scopes (refuses to revive expired permits).
  - **Owner-Verified Lua Release** (`RELEASE_CLOUD_PERMITS_LUA`): Deletes ONLY entries matching the caller's unique `lease_token`.
  - **Background Heartbeat Task** (`start_permit_heartbeat`): Renews lease every 15s and sets `lease_lost_event` upon loss/eviction to prevent split-brain execution.
  - Verified across independent spawned OS worker processes in `TestMultiProcessSemaphoreContention`.

---

## E. Cancellation, Disconnect Detection & DB State Transitions

- **Cancellation Controller**: [cancellation.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/cancellation.py)
  - Sets Redis cancel marker (`chat:cancel:{message_id}`) + publishes wakeup on `chat:cancel:ch:{message_id}`.
  - Enforces non-owner isolation (`404 MESSAGE_NOT_FOUND`).
  - Rejects cancellation of already-terminal `'completed'` (`409 GENERATION_ALREADY_COMPLETED`) or `'failed'` (`409 GENERATION_ALREADY_FAILED`) turns; repeat cancellation of `'cancelled'` is idempotent (`200 OK`, `already_cancelled: true`).
  - `watch_cancellation_and_disconnect()` monitors Redis Pub/Sub, Redis cancel key fallback, and FastAPI `request.is_disconnected()` concurrently.
- **Persistence Guards**: [persistence.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/persistence.py)
  - `verify_conversation_ready_for_cloud()` validates ownership, active conversation/subject state (`403`/`404`), and active cloud turns (`409 CONVERSATION_CONCURRENCY_LIMIT`) before semaphore admission.
  - Conditional compare-and-swap (`expected_generation_request_id` + `status IN ('pending', 'streaming')`) in `complete_cloud_turn_atomically()`, `mark_assistant_message_failed()`, and `mark_assistant_message_cancelled()` preserves partial content safely without overwriting terminal states.

---

## F. Streaming Orchestration, Citation Repair & Private Local Isolation

- **Streaming Orchestrator**: [streaming_service.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/streaming_service.py)
  - `prepare_cloud_sse_stream()` completes authorization, idempotency replay detection, Redis preflight, grounded context preparation, and turn reservation BEFORE committing HTTP 200 SSE headers, releasing database pool connections immediately.
  - `stream_cloud_sse_events()` handles bounded `admission_waiting` queuing, keepalive comments (`: keep-alive`), `generation_started`, `sources_identified` (safe metadata without raw chunk text or secrets), `text_delta`, post-stream deterministic citation validation with up to 1 bounded repair emitting `content_replaced`, atomic completion + async compaction scheduling, and terminal `generation_completed` / `generation_failed` / `generation_cancelled` events.
- **Generation & Router Integration**: [generation.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/generation.py), [service.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/service.py) (wraps synchronous `stream=false` path in `wait_for_sync_cloud_permits` + heartbeat), [schemas.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/orchestration/schemas.py) (`stream: bool = True`, `CancelGenerationResponse`), and [router.py](file:///d:/Projects/RagBait/apps/api/app/modules/chat/router.py).
- **Private Local Isolation**: Verified that `prepare-local` and `finalize-local` bypass Redis cloud semaphores completely and succeed even while `sem:global:cloud` is 10/10 saturated or Redis is offline (`503` for cloud).

---

## G. Thin Frontend SSE Transport Helper

- [types/chat.ts](file:///d:/Projects/RagBait/apps/web/src/types/chat.ts): Added typed SSE stream payload interfaces and discriminated `ChatStreamEvent` union + `CancelGenerationResponse`.
- [lib/chat-client.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat-client.ts): Added `cancelGeneration(messageId, conversationId?)`.
- [lib/chat/streaming-client.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat/streaming-client.ts): Added `parseSseByteStream()`, `streamConversationMessage()` (incremental UTF-8 multibyte decoding across arbitrary TCP splits, multi-line `data:`, CRLF/LF, keepalive skipping, Supabase JWT auth, pre-stream `ApiError`), and `abortAndCancelGeneration()`.

---

## H. Controlled Load & Concurrency Saturation Results

Executed against live FastAPI ASGI transport, local PostgreSQL (`127.0.0.1:54322`), and local Redis (`127.0.0.1:6379`) via [test_m4_6_load.py](file:///d:/Projects/RagBait/apps/api/tests/test_m4_6_load.py):

| Profile | Streams | Completed | Queued (`admission_waiting`) | Rejected (`RATE_LIMITED`) | Peak `sem:global:cloud` | Leaked Global / Waiters | p50 Latency | p95 Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **5 Concurrent** | 5 | 5 | 0 | 0 | 5 (≤ 10) | 0 / 0 | 372.6 ms | 394.9 ms |
| **10 Concurrent (Cap)** | 10 | 10 | 0 | 0 | 10 (≤ 10) | 0 / 0 | 484.3 ms | 537.3 ms |
| **20 Burst (Queued Drain)** | 20 | 20 | 10 | 0 | 10 (strict cap) | 0 / 0 | 1006.5 ms | 1512.6 ms |
| **20 Burst (1s Wait Timeout)** | 20 | 10 | 10 | 10 | 10 (strict cap) | 0 / 0 | 1533.9 ms | 1724.6 ms |

---

## I. Verification Ledger (Post-Final-Edit)

| Check Suite | Command | Outcome | Notes |
| :--- | :--- | :--- | :--- |
| **Backend Format & Lint** | `uv run ruff format --check ... && uv run ruff check ...` | **PASS** | 0 errors across gateway, routing, chat, M4.5 & M4.6 tests |
| **Backend Strict Typing** | `uv run mypy app/modules/llm_gateway/ app/modules/routing/ app/modules/chat/` | **PASS** | `Success: no issues found in 53 source files` |
| **M4.5 + M4.6 + Gateway Pytest** | `uv run pytest tests/test_gateway_streaming.py tests/test_m4_5_*.py tests/test_m4_6_*.py -s -q` | **58 passed** | 0 failed, 0 skipped in `24.28s` |
| **Full Backend Regression** | `uv run pytest tests/ -q --tb=line` | **427 passed**, 4 failed, 4 skipped | All 4 failures are pre-existing WIP in `tests/test_community_inference.py` lines `237, 276, 315, 464` |
| **Database pgTAP Suite** | `npx supabase test db` | **16 files, 352 tests PASS** | Zero regressions |
| **Frontend Lint & Types** | `pnpm lint; pnpm typecheck` | **PASS** | 0 errors (`2` pre-existing exhaustive-deps warnings in `/chat`) |
| **Frontend Vitest Suite** | `pnpm test` | **24 files, 147 tests PASS** | Includes new `streaming-client.test.ts` (4) + `chat-client.test.ts` (+2) |
| **Frontend Prod Build** | `pnpm build` | **PASS** | Clean Next.js 15.1.7 production build (`26/26` routes) |

---

## J. Security & Privacy Verification

- **No Secret Leakage**: Raw decrypted credentials never enter SSE frames, telemetry, logs, or `ProviderAdapterError` strings.
- **No Unvalidated Source Leakage**: `sources_identified` exposes only safe citation keys, document IDs/titles, physical page numbers, and scores — never raw unvalidated chunks.
- **Strict Authorization & Anti-Enumeration**: Non-owners receive `403` on message submission and `404` on cancellation; terminal completed/failed rows cannot be flipped to cancelled.
- **Zero-Cloud Private Local Guarantee**: Private Local flows remain fully operational during Redis outages and 10/10 cloud saturation without external API fallback.

---

## K. Documentation & Deferred Scope

- Updated [PHASE4_ROADMAP.md](file:///d:/Projects/RagBait/docs/phase4/PHASE4_ROADMAP.md), [ARCHITECTURE_DECISIONS.md](file:///d:/Projects/RagBait/docs/phase4/ARCHITECTURE_DECISIONS.md) (ADR 16, ADR 17, new ADR 23), [API_AND_EVENT_CONTRACTS.md](file:///d:/Projects/RagBait/docs/phase4/API_AND_EVENT_CONTRACTS.md), [GENERATION_RELIABILITY_AND_LOAD_PLAN.md](file:///d:/Projects/RagBait/docs/phase4/GENERATION_RELIABILITY_AND_LOAD_PLAN.md), and [SECURITY_AND_PRIVACY_THREAT_MODEL.md](file:///d:/Projects/RagBait/docs/phase4/SECURITY_AND_PRIVACY_THREAT_MODEL.md).
- Deferred as planned: **M4.7** (Student Chat UI, conversation sidebar, interactive citation chips, Sources drawer, PDF page jump) and **M4.8** (50-question golden dataset benchmark & final Phase 4 release certification).
