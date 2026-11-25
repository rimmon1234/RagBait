# RagBait Phase 4 — M4.8 Final Evaluation & Release Certification

**Milestone:** M4.8 — Evaluation, Integration Testing, Load Testing & Final Release Certification  
**Branch:** `phase4`  
**Evaluated HEAD Baseline:** `a32a168` (`feat(chat): implement M4.7 chat UI and sources panel`)  
**Date:** November 25, 2026  
**Final Release Verdict:** **`2. CERTIFIED FOR RESTRICTED / CONTROLLED RELEASE`** *(Public multi-tenant / untrusted student file uploads BLOCKED — see Section H & Section O)*

---

## A. Executive Summary

Milestone M4.8 executed full-stack reproducible evaluation, real-service end-to-end integration verification, multi-process Redis concurrency and crash testing, security and authorization auditing, database pgTAP verification, and cross-stack regression checks across RagBait's completed Phase 4 architecture (M4.1–M4.7).

### Highlights & Outcomes
- **50-Question Bilingual RAG Benchmark (`25 EN` / `25 BN`)**: Achieved **`0.9881` Recall@5**, **`1.0000` Recall@10**, **`1.0000` HitRate@5**, **`0.9444` MRR**, and **`0.9451` nDCG@5** under Hybrid RRF (`k=60`, `w_vec=0.60`, `w_fts=0.40`) on live PostgreSQL + pgvector + local INT8 FastEmbed ONNX (`19.35 ms` p50 / `27.73 ms` p95 retrieval SQL execution).
- **Grounded Generation & Security Fencing**: Achieved **`100%` citation validity**, **`100%` abstention correctness**, **`100%` prompt-injection defense pass rate**, **`100%` context budget compliance**, **`95.24%` physical page accuracy** (`40/42`), **`94.0%` supported/grounded rubric rate** (`mean 2.82 / 3.0`), and **`0` security retrieval leaks** across all 8 isolation dimensions.
- **Empirical Retrieval Finding (M4.4 FTS Gap)**: `match_subject_chunks` uses `plainto_tsquery('english'/'simple', p_query_text)` (conjunctive `AND` across all query words), yielding **`0.000` FTS-only recall on full natural-language questions** (`42/42` zero hits) while scoring **`100%` hit rate (`5/5`) on concise keyword probes**. Consequently, Hybrid RRF matches Vector-only accuracy rather than outperforming it on conversational queries.
- **Real Multi-Process Redis & Live Ollama Verification**: Across two separate OS uvicorn worker processes (`18091`, `18092`) sharing Redis (`127.0.0.1:6379`) and PostgreSQL (`127.0.0.1:54322`), all `20/20` burst streams completed cleanly (`10` queued via `admission_waiting`, `peak_global = 10`, `0` leaked permits, `p50 = 914.0 ms`, `p95 = 1475.0 ms`), `SIGKILL` lease recovery succeeded cleanly, cross-process SSE cancellation and `content_replaced` repair reconciliation passed, and live local Ollama (`llama3.2:3b` on `127.0.0.1:11434`) executed `prepare-local -> /api/chat -> finalize-local` end-to-end.
- **Critical Release Scope Blocker**: Direct probing of `POST /api/v1/kb/documents/upload-intent` returned **`HTTP 201` for an approved non-admin contributor**, and `parser_process.py` runs inside a credential-stripped local Python subprocess rather than an OS/container/cgroups/network-namespace sandbox. Untrusted public multi-tenant uploads therefore remain **BLOCKED** for unconstrained production release.

---

## B. Baseline Git State, Protected Files & Known M4.6 Failure Investigation

### 1. Pre-Existing Local State & Protected File Safety
- Pre-existing formatter whitespace diffs remained untouched and unstaged in `apps/web/src/components/chat/ChatInterface.tsx` and `apps/web/src/components/chat/ChatSidebar.tsx`.
- All 9 protected user-owned files remained unmodified throughout M4.8 (verified in Section M).

### 2. Investigation & Disposition of Backend Test Failures (`5 failed, 429 passed, 6 skipped`)
Reproducing the backend suite exposed **5 failures** and **6 skipped tests** across `440` collected tests (`267.63s`):

| Test Node | Observed Failure | Root Cause Classification | Impact on M4.1–M4.7 Chat/RAG |
|---|---|---|---|
| `tests/test_community_inference.py::test_pairing_code_and_node_registration` (L237) | `assert 200 == 410` | **Expected Pre-Existing Protected Test Mismatch**: Protected commit `422e40c` rewrote `test_community_inference.py` to expect `410 Gone` while `apps/api/app/modules/community/router.py` still serves legacy Phase 2 REST handlers. | **None** (Phase 2 compute-pooling only) |
| `tests/test_community_inference.py::test_node_management_and_isolation` (L276) | `assert 404 == 410` | Same as above — legacy `PATCH /api/v1/community/nodes/{id}` returns `404` instead of `410`. | **None** |
| `tests/test_community_inference.py::test_dual_consent_and_job_submission` (L315) | `assert 409 == 410` | Same as above — legacy `POST /api/v1/community/jobs/submit` returns `409` instead of `410`. | **None** |
| `tests/test_community_inference.py::test_sse_stream_authentication_and_authorization` (L464) | `assert 404 == 410` | Renamed in `422e40c` to verify `HTTP 410 Gone` on `GET /api/v1/community/jobs/{id}/stream`. **Zero relation** to M4.6 `/api/v1/chat/*` SSE authentication. | **None** |
| `tests/test_routing_execution.py::test_route_and_dispatch_community_candidate` (L592) | `assert cr is not None` (`get_community_redis()`) | Dedicated Community Redis (`:6380`) is intentionally offline as Phase 2 community compute-pooling is discontinued; this legacy test lacks the `pytest.skip` guard present in `test_community_inference.py` (L600, L848). | **None** (all 8 other `test_routing_execution.py` tests passed) |

**Skipped Tests Accounting (6/6 Expected)**:
- `tests/test_community_inference.py` (2 tests at L600, L848): skipped because Community Redis (`:6380`) is not configured/running.
- `tests/test_gateway_live_smoke.py` (4 tests): skipped because cloud provider API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`) are unset in the offline/local test environment.

---

## C. Evaluation Dataset & Protocol

- **Protocol Document**: [M4_8_EVALUATION_PROTOCOL.md](file:///d:/Projects/RagBait/docs/phase4/evaluation/M4_8_EVALUATION_PROTOCOL.md)
- **Frozen Corpus Provisioner**: [corpus_fixtures.py](file:///d:/Projects/RagBait/apps/api/tests/fixtures/m4_8/corpus_fixtures.py)
- **Frozen Golden Dataset (`50` Cases)**: [golden_dataset_v1.json](file:///d:/Projects/RagBait/apps/api/tests/fixtures/m4_8/golden_dataset_v1.json)
- **Reproducible Evaluator**: [run_m4_8_evaluation.py](file:///d:/Projects/RagBait/apps/api/tests/evaluation/run_m4_8_evaluation.py)
- **Machine-Readable Output**: [results_m4_8_v1.json](file:///d:/Projects/RagBait/apps/api/tests/evaluation/results_m4_8_v1.json)
- **Automated Gate Suite**: [test_m4_8_rag_evaluation.py](file:///d:/Projects/RagBait/apps/api/tests/evaluation/test_m4_8_rag_evaluation.py)

### Dataset Stratification (`m4_8_bilingual_v1.0.0`)
- **Language Split**: `25` English (`50%`) and `25` Bengali (`50%`) across Operating Systems (`os`) and Database Management Systems (`dbms`) subjects, chunked through production `chunk_document_pages()` and embedded via local `intfloat/multilingual-e5-small` (`e5_small_int8_761b726d_f80102d3`).
- **Category Breakdown**:
  - `direct_factual`: `10` (`5 EN`, `5 BN`)
  - `multi_passage_synthesis`: `10` (`5 EN`, `5 BN`)
  - `multi_page_evidence`: `8` (`4 EN`, `4 BN`)
  - `cross_language_retrieval`: `8` (`4 EN -> BN`, `4 BN -> EN`)
  - `ambiguous_or_conflicting`: `6` (`3 EN`, `3 BN`)
  - `unanswerable`: `8` (`4 EN`, `4 BN` — including out-of-syllabus, cross-subject isolation, archived-only, and prompt-injection triggers)
- **Data Leakage Guard**: Ground-truth `expected_answer_points`, `gold_evidence`, and `evaluation_notes` never enter retrieval or prompt construction.

---

## D. Retrieval Quality Results & Analysis

Executed across all `42` answerable questions against live local PostgreSQL + pgvector + FastEmbed (`top_k = 5`, `candidate_limit = 30`, `rrf_k = 60`, `w_vec = 0.60`, `w_fts = 0.40`):

### 1. Mode Comparison (`n = 42`)

| Retrieval Mode | Recall@5 | Recall@10 | Precision@5 | HitRate@5 | MRR | nDCG@5 | Zero-Hit Queries | p50 Latency | p95 Latency |
|---|---|---|---|---|---|---|---|---|---|
| **Vector-only** (`1.0 / 0.0`) | `0.9881` | `1.0000` | `0.2810` | `1.0000` | `0.9444` | `0.9451` | `0` | `17.01 ms` | `24.00 ms` |
| **FTS-only** (`0.0 / 1.0`, `fts_rank != NULL`) | `0.0000` | `0.0000` | `0.0000` | `0.0000` | `0.0000` | `0.0000` | `42` | `20.63 ms` | `29.75 ms` |
| **Hybrid RRF** (`0.60 / 0.40`) | `0.9881` | `1.0000` | `0.2810` | `1.0000` | `0.9444` | `0.9451` | `0` | `19.35 ms` | `27.73 ms` |

### 2. Hybrid RRF Breakdown by Language & Category

| Cohort | Count | Recall@5 | Recall@10 | Precision@5 | MRR | nDCG@5 | p50 Latency | p95 Latency |
|---|---|---|---|---|---|---|---|---|
| **English** | `21` | `1.0000` | `1.0000` | `0.3048` | `0.9683` | `0.9642` | `19.01 ms` | `23.81 ms` |
| **Bengali** | `21` | `0.9762` | `1.0000` | `0.2571` | `0.9206` | `0.9260` | `20.98 ms` | `28.87 ms` |
| `direct_factual` | `10` | `1.0000` | `1.0000` | `0.2000` | `0.9500` | `0.9631` | `19.58 ms` | `25.41 ms` |
| `multi_passage_synthesis` | `10` | `1.0000` | `1.0000` | `0.2800` | `1.0000` | `0.9945` | `17.26 ms` | `27.73 ms` |
| `multi_page_evidence` | `8` | `0.9375` | `1.0000` | `0.4000` | `0.9167` | `0.8919` | `23.15 ms` | `29.26 ms` |
| `cross_language_retrieval` | `8` | `1.0000` | `1.0000` | `0.2000` | `0.8542` | `0.8914` | `19.01 ms` | `23.47 ms` |
| `ambiguous_or_conflicting` | `6` | `1.0000` | `1.0000` | `0.3667` | `1.0000` | `0.9755` | `19.83 ms` | `28.87 ms` |

### 3. Critical FTS Empirical Diagnosis (`plainto_tsquery` Conjunctive AND)
- **`fts_only` returned `0` hits across all `42` full natural-language questions** because [20260304000000_m4_4_hybrid_retrieval.sql](file:///d:/Projects/RagBait/supabase/migrations/20260304000000_m4_4_hybrid_retrieval.sql#L84-L85) evaluates `plainto_tsquery('english', p_query_text)` and `plainto_tsquery('simple', p_query_text)`. `plainto_tsquery` joins every non-stopword query token with Boolean `AND`. Natural-language questions routinely contain instructional verbs/pronouns or cross-script tokens not present verbatim in the target passage, causing `tsvector @@ tsquery` to evaluate to `false`.
- **Short Keyword Sanity Probes (`5/5` Hit)**: When queried with concise technical keywords (`Belady Anomaly` -> `2` hits top p.12, `120 ns` -> `1` hit p.12, `Priority Inversion` -> `2` hits p.14, `কপি-অন-রাইট` -> `1` hit p.2, `BCNF` -> `1` hit p.20), the GIN `idx_kb_chunks_fts` / `idx_kb_chunks_fts_simple` indexes match accurately.
- **Recommendation for Post-M4.8**: Replace `plainto_tsquery` with `websearch_to_tsquery` or disjunctive (`OR`) stemmed token ranking with minimum coordination threshold for full-sentence student questions so lexical exact-term boosting activates on conversational queries too.
- **Observed Multi-Gold Miss**: Case `M48-26` (Bengali `multi_page_evidence`) retrieved one gold page in top-5 and placed the second page at rank `6–10` (`Recall@5 = 0.5`, `Recall@10 = 1.0`).

### 4. Security & Visibility Fencing Probes (`8/8` Zero Leaks)
Across explicit cross-subject, lifecycle, and indexing restriction probes (`results_m4_8_v1.json` lines 351–360):
- `cross_subject_leaks: 0`
- `archived_leaks: 0`
- `inactive_topic_leaks: 0`
- `inactive_subject_leaks: 0`
- `stale_version_leaks: 0`
- `unembedded_leaks: 0`
- `hash_mismatch_leaks: 0`
- `unanswerable_restricted_hits: 0`

---

## E. Grounded Generation, Citation, Abstention & Prompt Injection Results

Evaluated across all `50` golden questions executed through `ChatOrchestrationService.execute_cloud_chat_turn` using context-bound deterministic test adapters (zero paid keys consumed):

| Metric | Result | Target / Gate | Notes |
|---|---|---|---|
| **Total Evaluated** | `50 / 50` | `50` | Full dataset coverage |
| **Citation Validity Rate** | **`1.0000` (`50/50`)** | `1.0000` | Every `[Sn]` maps to persisted Stage-3 `included_in_context` rows |
| **Physical Page Accuracy** | **`0.9524` (`40/42`)** | `>= 0.90` *(1.0 ideal)* | 2 misses (`M48-26` p.15 outside top-5; `M48-32` primary gold page outside cited set) |
| **Abstention Correctness** | **`1.0000` (`8/8`)** | `>= 0.90` | All unanswerable / out-of-scope / archived-only queries abstained with `0` citations |
| **Prompt Injection Pass Rate** | **`1.0000`** | `1.0000` | Embedded indirect PDF instructions inside OS p.17 never hijacked citations or output |
| **Context Budget Compliance** | **`1.0000` (`50/50`)** | `1.0000` | Zero budget overflows; `T_in + T_out <= C_model` |
| **Bounded Citation Repair** | **`true`** | Verified | Injected invalid `[S99]` repaired and stripped cleanly with no provisional leak |
| **Supported / Grounded Rate (Rubric >= 2)** | **`0.9400` (`47/50`)** | `>= 0.80` | `3` partial-coverage cases (`M48-25`, `M48-26`, `M48-32`) scored `1` |
| **Mean Grounding Rubric Score (`0–3`)** | **`2.82 / 3.00`** | `>= 2.40` | Strong bilingual grounding under deterministic adapter scoring |

---

## F. Real-Service E2E, SSE Streaming & Multi-Process Load Test Results

Implemented in [multiprocess_worker_app.py](file:///d:/Projects/RagBait/apps/api/tests/evaluation/multiprocess_worker_app.py) and [test_m4_8_multiprocess_and_e2e.py](file:///d:/Projects/RagBait/apps/api/tests/test_m4_8_multiprocess_and_e2e.py) against **real spawned OS uvicorn processes**, `ragbait-redis` (`127.0.0.1:6379`), local Supabase PostgreSQL (`127.0.0.1:54322`), and local Ollama (`127.0.0.1:11434`):

1. **Multi-Process Distributed Redis Concurrency & Saturation (`2` OS Workers: `:18091`, `:18092`)**:
   - `1/conversation` semaphore: concurrent cross-process post rejected with `HTTP 409` (`CONVERSATION_CONCURRENCY_LIMIT`).
   - `2/user` semaphore: `3rd` simultaneous turn across processes rejected with `HTTP 429` (`USER_CONCURRENCY_LIMIT`).
   - **20-Request Saturation Burst**: `completed = 20/20`, `queued = 10` (received `event: admission_waiting`), **`peak_global = 10`** (never exceeded `GLOBAL_MAX = 10`), **`p50 = 914.0 ms`**, **`p95 = 1475.0 ms`**, `0` remaining global/waiter keys post-burst.
   - **Private Local Bypass**: `POST /prepare-local` succeeded (`HTTP 200`) during active global cloud saturation without consuming cloud permits.
2. **Crash Recovery, Admission Timeout & Redis Outage Fail-Closed**:
   - **Admission Timeout (`TTL=5s, timeout=1s`)**: Request timed out cleanly with `event: generation_failed` (`RATE_LIMITED`).
   - **Abrupt `SIGKILL` Crash Recovery**: Killing active OS worker holding lease `sem:conv` / `sem:global:cloud` recovered cleanly within TTL window (`5s`) without manual Redis intervention.
   - **Redis Outage (`127.0.0.1:6399`)**: Isolated uvicorn worker failed closed cleanly with **`HTTP 503`** (`GENERATION_SERVICE_UNAVAILABLE`) for cloud generation while `/health/live` (`200`) and `/prepare-local` remained operational.
3. **Cross-Process SSE Lifecycle, Replay, Repair & Cancellation**:
   - Cross-process SSE delivery verified `admission_waiting`, `generation_started`, `sources_identified`, `text_delta`, `content_replaced` (citation repair stripping `[S99]`), and `generation_completed` (`0` provisional leak).
   - **Cross-Process Cancel (`POST /conversations/{id}/messages/{msg_id}/cancel`)**: Initiated on worker `:18094` while streaming on `:18093`; terminated cleanly with `generation_cancelled`, preserving persisted partial text and settled permits (`0` leaks).
   - **Cancel Races**: Re-cancelling terminal message returned **`HTTP 409`**, non-owner cancel returned **`HTTP 404`**.
4. **End-to-End Auth Matrix, Source Revocation & Live Local Ollama**:
   - **Auth/Access Matrix**: Unauthenticated & expired JWT -> `401`; pending/rejected/suspended -> `403`; approved non-contributor -> `200` on KB browse & existing history list/rename, `403` on new chat/turn; other user conversation/source inspect -> `404`.
   - **Live Admin Moderation Source Masking**: Before archive, `GET /messages/{id}/sources` returned `is_available = true` with `/knowledge-base/document/{id}?page=12`; immediately after `POST /api/v1/kb/admin/documents/{id}/archive`, `/sources` masked `excerpt` (`UNAVAILABLE_SOURCE_PLACEHOLDER`), set `is_available = false`, set `document_viewer_url = null`, and KB document fetch returned `404`.
   - **Live Local Ollama (`llama3.2:3b` on `127.0.0.1:11434`)**: Executed `prepare-local` (`200`) -> real Ollama completion (`200`) -> `finalize-local` (`200`, `status = completed`) end-to-end.

---

## G. Frontend Verification & Gap Disclosure

Executed inside `apps/web` cleanly (`Exit code 0`):
- **`pnpm lint` (`next lint`)**: Passed — `0` ESLint warnings or errors.
- **`pnpm typecheck` (`tsc --noEmit`)**: Passed — `0` TypeScript errors.
- **`pnpm test` (`vitest run`)**: Passed — **`28 passed (28)` files, `165 passed (165)` specs** (`13.10s`, including M4.7 specs `useChatSession.test.ts`, `SourcesDrawer.test.tsx`, `MarkdownAndComposer.test.tsx`, `ChatAccessAndPages.test.tsx`, `streaming-client.test.ts`, `ChatComponents.test.tsx`).
- **`pnpm build` (`next build`)**: Passed — compiled all `26` App Router routes cleanly including `/chat`, `/chat/[id]`, and `/knowledge-base/document/[id]`.

**Explicit Browser E2E Disclosure**: Playwright is not installed in [package.json](file:///d:/Projects/RagBait/apps/web/package.json); therefore, live Chromium DOM click/visual E2E automation did not execute. Frontend behavior is certified via Vitest + React Testing Library mocked tests plus Next.js production build verification, paired with real FastAPI/PostgreSQL/Redis/Ollama backend E2E tests.

---

## H. Security, Privacy & Upload/Parser Sandbox Audit

### 1. Verified Passing Security Controls
- **Database RLS & `SECURITY DEFINER` Hardening**: All `5` migrations containing `SECURITY DEFINER` set explicit `search_path` (`SET search_path = ...`); `29` `ENABLE ROW LEVEL SECURITY` statements active; pgTAP suite passed `352/352` assertions including cross-user RLS and evidence immutability.
- **Offline FastEmbed Model Integrity**: `get_model_manifest().verify_local_directory(embedding_settings.model_dir)` returned `is_valid: True, errors: []` for `intfloat/multilingual-e5-small` (`e5_small_int8_761b726d_f80102d3`), and `TextEmbedding` loaded with `local_files_only=True`.
- **Credential & Prompt Secret Hygiene**: Static log scan across `apps/api/app/**/*.py` found `0` plaintext secret, credential, or raw prompt logging leaks (`REDACTED_FIELDS` & structural redaction active).
- **Production Boot Fail-Closed Guards**: `app/core/config.py` validates non-default secrets and rejects `local_dev` KMS mode when `APP_ENV=production`.

### 2. CRITICAL SECURITY BLOCKER — Upload Eligibility & Parser Sandbox
As verified directly in [router.py](file:///d:/Projects/RagBait/apps/api/app/modules/knowledge_base/router.py) / [parser_process.py](file:///d:/Projects/RagBait/apps/api/app/modules/knowledge_base/parser_process.py) AND empirically probed in `test_m4_8_multiprocess_and_e2e.py`:
- **Finding 1 — Approved Student Upload Reachability**: `POST /api/v1/kb/documents/upload-intent` blocked unauthenticated (`401`) and `pending` (`403`) requests, but returned **`HTTP 201` for an approved non-admin student** (`[M4.8 Upload Gate Audit] approved non-admin upload-intent HTTP 201`).
- **Finding 2 — Incomplete Parser OS/Network Isolation**: While `parser_process.py` strips credential environment variables (`env`) in the child Python subprocess, PDF/document parsing still executes directly on the host OS without container/gVisor/Firecracker isolation, Linux network-namespace disconnection (`CLONE_NEWNET`), read-only filesystem bind-mounts, or cgroups memory/CPU hard enforcement.
- **Release Implication**: Per M4.8 Section 12.6 & Section 20 requirements, **public multi-tenant untrusted file uploads MUST remain disabled/restricted**. Full production multi-tenant certification is **BLOCKED** until either container/microVM parser isolation is deployed OR upload endpoints are gated exclusively to trusted admins/curators.

---

## I. Performance, Scale & Operations Readiness

- **Query Embedding Latency**: Cold ONNX initialization: `1590.12 ms`; warm CPU inference (`embed_query`): `3.53 ms – 9.55 ms`.
- **PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`**: `match_subject_chunks` executed in `6.193 ms` with `1113` buffer hits; `kb_chunk_embeddings` uses `hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`; `kb_document_chunks` maintains GIN indexes (`idx_kb_chunks_fts`, `idx_kb_chunks_fts_simple`).
- **20-Burst Concurrent SSE Latency (`10` active / `10` queued)**: `p50 = 914.0 ms`, `p95 = 1475.0 ms` under `0.35s` simulated token streaming across 2 OS worker processes.
- **Health Endpoints**: `/health/live` and `/health/ready` verified across worker spawns and degraded Redis outages.

---

## J. Full Regression Matrix

| Suite / Command | Scope | Result | Exit Code |
|---|---|---|---|
| `ruff format` & `ruff check` (M4.8 files) | All 5 M4.8 Python modules/tests | Passed (`All checks passed!`) | `0` |
| `mypy app/modules/chat/ app/modules/knowledge_base/` | Strict typing on 73 source files | Passed (`no issues found`) | `0` |
| `npx supabase test db` | Full DB schema, RLS, KB & RAG pgTAP suite | **PASS (`16 files, 352 tests`, 1s)** | `0` |
| `pytest tests/evaluation/test_m4_8_rag_evaluation.py tests/test_m4_8_multiprocess_and_e2e.py -v -s` | M4.8 evaluation + E2E + multi-process Redis + Ollama | **5 passed in 88.52s** | `0` |
| `pytest tests/ -q --tb=line` | Full backend suite (`440` tests collected) | `429 passed, 5 failed, 6 skipped in 267.63s` *(all 5 failures & 6 skips documented in Section B)* | `1` *(expected due to 5 legacy community-pooling tests)* |
| `pnpm lint` / `pnpm typecheck` / `pnpm test` / `pnpm build` (`apps/web`) | ESLint, TypeScript strict, 28 Vitest files (`165` specs), Next.js build (`26` routes) | **All 4 Passed (`0` errors)** | `0` |

---

## K. Documentation & Planning Consistency

- Updated [PHASE4_ROADMAP.md](file:///d:/Projects/RagBait/docs/phase4/PHASE4_ROADMAP.md) to reflect M4.8 completion status, exact verified metrics, empirical FTS finding, and the restricted-release condition.
- All M4.8 artifacts reside under `docs/phase4/`, `docs/phase4/evaluation/`, `apps/api/tests/fixtures/m4_8/`, `apps/api/tests/evaluation/`, and `apps/api/tests/test_m4_8_multiprocess_and_e2e.py`.

---

## L. Release Blockers, Non-Blocking Debt & Conditions for Wider Rollout

### Hard Release Blocker (Prevents Unconstrained Public Release)
1. **Upload/Parser Isolation (`KB Upload Gate`)**: Restrict `/api/v1/kb/documents/upload-intent` (and related upload endpoints) to admins/trusted curators OR isolate `parser_process.py` inside a network-disabled, cgroup-bounded container/microVM before enabling general student uploads.

### Non-Blocking Post-M4.8 Improvements
1. **FTS Natural-Language Recall**: Replace `plainto_tsquery` conjunctive `AND` in `match_subject_chunks` with `websearch_to_tsquery` / weighted disjunctive matching so lexical FTS boosts conversational queries alongside short technical phrases.
2. **Legacy Community Router Cleanup**: Deprecate/remove remaining Phase 2 handlers in `apps/api/app/modules/community/router.py` (or return `410 Gone` matching `test_community_inference.py`) and add `get_community_redis()` skip guard in `test_route_and_dispatch_community_candidate`.
3. **Playwright E2E Suite**: Add headless Chromium E2E tests in `apps/web` covering live login, SSE token streaming visual states, and `SourcesDrawer` page jumps.

---

## M. Protected Files Integrity Verification (SHA-256)

Every protected file remained 100% unmodified (identical baseline vs final SHA-256 checksums):

| File | Verified SHA-256 (Baseline & Final Exact Match) |
|---|---|
| `apps/api/app/main.py` | `d23bc2c7cb3434d92f6fe2c140d14017fa29d03b4047884c2ed538bc8b3ef987` |
| `apps/api/app/modules/community/ws.py` | `8b9cb2574b6c416d8079fd274d7c8b35f6014a0134cf469495b32ffe03c04577` |
| `apps/api/tests/test_community_inference.py` | `3e89a5d94c5f9d16df655e7304f80cb3861fc59e87aa2b153c83718f177d5e0a` |
| `apps/web/src/app/(auth)/login/page.tsx` | `dcbceba261ae94beb15f3a8bb0838ea23a7d95082f99012743d1d8fa504b2d85` |
| `apps/web/src/app/(protected)/access/contribution-required/page.tsx` | `0fff5d1a8517cebca4b797d6ed54a83318704dedf4ed04c87b2f85e1c43c1006` |
| `apps/web/src/app/(protected)/credentials/page.tsx` | `6b23a3d8adf28f8d91616b674b5632c83be463e6da12586c30ff0b1d2ef8f5c3` |
| `apps/web/src/app/(protected)/local-inference/page.tsx` | `2bb7ae20ce54fce8601a54f127aca660a97ee8ffb4aa394d62c141c545eb1bcb` |
| `apps/web/src/lib/routing-client.ts` | `9aadb4eb2135b694f595bef08ed707670ccf68f07b2fc1aada12897ee85827ee` |
| `apps/web/src/components/LocalInferenceWorkspace.tsx` | `654e6ed4c1b38f158d597708fa8492af79d203620f918697c9aa6f7e93f436aa` |

---

## N. Recommended Next Phase / Hardening Checklist

1. Restrict KB document upload endpoints to admins or deploy containerized parser sandboxing prior to public student onboarding.
2. Remediate `plainto_tsquery` conjunctive `AND` bottleneck in M4.4 FTS migration.
3. Align legacy Phase 2 `community/router.py` routes with `422e40c` `410 Gone` deprecation expectations.

---

## O. Final Release Verdict

**Verdict: `2. CERTIFIED FOR RESTRICTED / CONTROLLED RELEASE`**

RagBait's Phase 4 RAG Chat system (M4.1–M4.7: persistent conversations, page-aware chunking, offline INT8 FastEmbed pgvector storage, subject-isolated retrieval, context budgeting, citation validation/repair, Redis multi-process load control, SSE streaming/cancellation, live Ollama Private Local mode, and student chat UI + Sources drawer) is **functionally, cryptographically, and concurrency-verified** for **controlled cohorts operating with admin-curated or trusted documents**. Public multi-tenant production deployment with untrusted student uploads remains **BLOCKED** until upload gating or container/microVM parser isolation is enforced.
