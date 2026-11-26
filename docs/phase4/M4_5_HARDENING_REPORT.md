# RagBait Phase 4 — Milestone M4.5 Post-Implementation Hardening Report

**Milestone:** M4.5 Hardening (Workstream A)  
**Branch:** `phase4`  
**Status:** COMPLETE (`M4.5 HARDENING COMPLETE`)  
**Baseline Commit Reviewed:** `ca9281c` (`feat(chat): add grounded RAG context orchestration (M4.5)`)

---

## 1. Executive Audit Summary

All four audit areas (A1–A4) were audited against [20260305000000_m4_5_chat_evidence.sql](file:///D:/Projects/RagBait/supabase/migrations/20260305000000_m4_5_chat_evidence.sql), `apps/api/app/modules/chat/`, and [GENERATION_RELIABILITY_AND_LOAD_PLAN.md](file:///D:/Projects/RagBait/docs/phase4/GENERATION_RELIABILITY_AND_LOAD_PLAN.md). Genuine schema, worker-recovery, security, and token-labeling defects were identified and fixed in [20260306000000_m4_5_hardening.sql](file:///D:/Projects/RagBait/supabase/migrations/20260306000000_m4_5_hardening.sql) and the backend chat modules.

---

## 2. Detailed Findings & Fixes (Audits A1 – A4)

### Audit A1 — Source Snapshot Immutability & Provenance Transitions
- **Initial Issue & Audit Evidence**:
  - `enforce_chat_message_source_immutability()` did not guard `id`, `created_at`, arbitrary reassignment/manual nullification of `document_id` / `chunk_id` while parents still exist, or invalid `referenced_in_answer` transitions (`false -> true` on unincluded rows, or `true -> false` reversion).
  - `chat_message_sources` lacked check constraints pairing `document_id = source_document_id`, `chunk_id = source_chunk_id`, and `NOT (referenced_in_answer = true AND included_in_context = false)`.
- **Root Cause**: Initial trigger focused on content fields and relied solely on `WHERE included_in_context = true` in Python UPDATE SQL rather than enforcing state transitions inside PostgreSQL.
- **Migration & Code Changes**:
  - Added check constraints `ck_chat_msg_sources_provenance_order`, `ck_chat_msg_sources_doc_lineage`, and `ck_chat_msg_sources_chunk_lineage` in [20260306000000_m4_5_hardening.sql](file:///D:/Projects/RagBait/supabase/migrations/20260306000000_m4_5_hardening.sql).
  - Replaced `enforce_chat_message_source_immutability()` to guard `id`, `created_at`, non-cascade FK tampering, and monotonic provenance transitions.
  - Added restricted `SECURITY DEFINER` function `public.mark_chat_message_sources_referenced(uuid, text[])` (revoked from `PUBLIC`, `anon`, `authenticated`; executable strictly by `service_role`, `postgres`) and wired [source_repository.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/source_repository.py) to reject unknown/unincluded keys before invoking it.
- **Security Implications**: Prevents service/admin SQL writes or accidental bugs from mutating historical source lineage, clearing live document links while docs remain active, or marking omitted candidates as referenced.

### Audit A2 — Durable Summary Compaction & Crash Recovery
- **Initial Issue & Audit Evidence**:
  - `chat_conversation_summaries` lacked durable worker leases (`worker_id`, `lease_token`, `locked_until`) and backoff scheduling (`next_retry_at`).
  - `recover_pending_compactions()` queried only `'pending'` and `'failed'` statuses, stranding rows stuck in `'running'` indefinitely after worker crashes.
  - `compact_conversation()` failure updates filtered on `summary_version` rather than `lease_token`, risking clobbering by delayed workers; segment partitioning could also split `(user, assistant)` pairs across map calls.
- **Root Cause**: Initial implementation combined Redis mutex coordination with PostgreSQL optimistic version checks without durable database-level claim leases or running-state expiration recovery.
- **Migration & Code Changes**:
  - Added `worker_id`, `lease_token`, `locked_until`, `next_retry_at`, check constraint `ck_chat_summary_lock_consistency`, and index `idx_chat_conv_summaries_recoverable` in [20260306000000_m4_5_hardening.sql](file:///D:/Projects/RagBait/supabase/migrations/20260306000000_m4_5_hardening.sql).
  - Upgraded [compaction.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/compaction.py) with `claim_next_compaction_job()` (`FOR UPDATE OF s SKIP LOCKED`), `_renew_compaction_leases()`, background asyncio lease heartbeat, lease-fenced CAS SQL commits (`lease_token = ... AND locked_until > now()`), exponential backoff (`compute_compaction_backoff_seconds()`), turn-atomic `_group_rows_into_turns()`, and explicit message-mode detection keeping Private Local conversations `deferred_local`.
  - Added durable standalone/embeddable worker daemon [compaction_worker.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/compaction_worker.py) and lease/backoff settings in [config.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/config.py).
- **Security Implications**: Crashed or slow workers can neither strand summaries in `'running'` nor overwrite newer summaries after losing leases; Private Local transcripts remain strictly off cloud providers even during background recovery.

### Audit A3 — Private Local Prepared-Request Security, Expiry & Replay
- **Initial Issue & Audit Evidence**:
  - `FinalizeLocalRequest.prepared_request_id` was optional (`None`), causing `lock_and_verify_prepared_local_message()` to skip verifying `prepared_request_id` when omitted.
  - `lock_and_verify_prepared_local_message()` did not re-verify active conversation/subject status or explicitly distinguish `'cancelled'` state, and concurrent duplicate `prepare-local` inserts could raise unhandled `UniqueViolationError`.
- **Root Cause**: Nullable schema compatibility left the opaque prepared token verification conditional.
- **Code Changes**:
  - Made `prepared_request_id` mandatory (`min_length=8`) in [orchestration/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/schemas.py).
  - Enforced constant-time `hmac.compare_digest` on `prepared_request_id`, active conversation & subject checks, explicit `PREPARED_REQUEST_CANCELLED` (`409`), and SQL CAS on `finalize-local` in [persistence.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/persistence.py) and [service.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/service.py).
  - Caught `asyncpg.UniqueViolationError` in `reserve_turn_messages()` to return `409 GENERATION_IN_PROGRESS`.
- **Security Implications**: Eliminates finalizing local placeholders without possession of the opaque server token, prevents replay/race duplicates, and blocks finalizing cancelled or deactivated sessions.

### Audit A4 — Citation Repair Integrity & Honest Token Accounting Labels
- **Initial Issue & Audit Evidence**:
  - `validate_citations()` returned `is_valid=True` for empty/whitespace strings, allowing empty repair outputs to complete cleanly.
  - Token usage across repair passes overwrote initial usage instead of accumulating both calls, and `TokenAccountingBreakdown` lacked explicit estimation vs exact-tokenizer labels.
- **Code Changes**:
  - Added `invalid_empty_response` guard in [citations/validator.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/validator.py) and [citations/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/schemas.py).
  - Added `normalize_token_usage_record()` and combined initial + repair token accumulation in [generation.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/generation.py), plus `count_source`, `counting_strategy`, and `is_approximate` fields on `TokenAccountingBreakdown` in [context/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/schemas.py) and [budget.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/budget.py).
- **Security Implications**: Prevents empty/unvalidated completions from persisting and ensures local heuristic token counts are never misrepresented as exact model tokenizer counts.

---

## 3. Files Modified & Created (Workstream A)

- [20260306000000_m4_5_hardening.sql](file:///D:/Projects/RagBait/supabase/migrations/20260306000000_m4_5_hardening.sql)
- [m4_5_evidence_and_summaries_test.sql](file:///D:/Projects/RagBait/supabase/tests/m4_5_evidence_and_summaries_test.sql)
- [apps/api/app/modules/chat/context/config.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/config.py)
- [apps/api/app/modules/chat/context/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/schemas.py)
- [apps/api/app/modules/chat/context/budget.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/budget.py)
- [apps/api/app/modules/chat/context/compaction.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/compaction.py)
- [apps/api/app/modules/chat/context/compaction_worker.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/context/compaction_worker.py)
- [apps/api/app/modules/chat/citations/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/schemas.py)
- [apps/api/app/modules/chat/citations/validator.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/validator.py)
- [apps/api/app/modules/chat/citations/source_repository.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/citations/source_repository.py)
- [apps/api/app/modules/chat/orchestration/schemas.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/schemas.py)
- [apps/api/app/modules/chat/orchestration/persistence.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/persistence.py)
- [apps/api/app/modules/chat/orchestration/generation.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/generation.py)
- [apps/api/app/modules/chat/orchestration/service.py](file:///D:/Projects/RagBait/apps/api/app/modules/chat/orchestration/service.py)
- [apps/api/tests/test_m4_5_hardening.py](file:///D:/Projects/RagBait/apps/api/tests/test_m4_5_hardening.py)

---

## 4. Verification Summary

- `npx supabase test db`: **16/16 files, 352/352 subtests passed** (including 42 assertions in `m4_5_evidence_and_summaries_test.sql`).
- `uv run ruff format` & `uv run ruff check` across `app/modules/chat/` and `tests/test_m4_5_*.py`: **All checks passed!**
- `uv run mypy app/modules/chat/`: **Success: no issues found in 27 source files**.
- `uv run pytest tests/test_m4_5_context_budget.py tests/test_m4_5_citations_and_compaction.py tests/test_m4_5_orchestration_e2e.py tests/test_m4_5_hardening.py -v`: **27 passed in 5.01s**.
- `uv run pytest tests/test_chat_persistence.py tests/test_kb_chunking.py tests/test_kb_embeddings.py tests/test_kb_retrieval.py tests/test_m3_5_admin_moderation.py -q`: **55 passed in 10.52s**.

## 5. Remaining Limitations

- Citation validation proves citation-key structural validity and excerpt inclusion lineage; it does not verify semantic entailment (scheduled for M4.8 RAG quality evaluation).
- Background compaction workers (`ConversationCompactionWorker`) run either programmatically/via CLI daemon or explicit recovery calls; without an active daemon or periodic caller, `pending` compactions wait until invoked.
- `API_AND_EVENT_CONTRACTS.md` contract update requiring `prepared_request_id` in `finalize-local` is included alongside M4.6 contract documentation.
