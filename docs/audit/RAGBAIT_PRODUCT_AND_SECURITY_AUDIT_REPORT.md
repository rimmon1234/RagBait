# RagBait — Comprehensive Product & Security Audit Report

**Audit Date:** November 25, 2026  
**Branch:** `phase4` (Baseline `68acebf`)  
**Status:** **REMEDIATION COMPLETED & VERIFIED (November 25, 2026)**

---

## 1. Audit Scope & Objective

This audit evaluated RagBait's complete product, architectural, security, and implementation history across Phases M0 through M4.8 against original user-approved requirements and security policies.

### Key Baseline Findings Confirmed in Code & Live Behavior

1. **Duplicate Student Chat Surfaces (`Study Chat` vs `AI Inference` — CONTRADICTION)**:
   - `apps/web/src/components/Navbar.tsx` rendered both `"Study Chat"` (`/chat`) and `"AI Inference"` (`/local-inference`).
   - `/local-inference` originated in M2.7/M2.9 using transient React state + `POST /api/v1/llm/chat/completions`, whereas `/chat` (M4.1–M4.7) provides durable PostgreSQL persistence, subject-scoped RAG, citations, Cloud SSE streaming, and Private Local Ollama loops.
   - `apps/web/src/app/(protected)/local-inference/page.tsx` did not check `/api/v1/me/chat-entitlement`, creating both UX fragmentation and a functional entitlement bypass.
2. **Weakened Contribution Hard Gate & Backend Bypass (`REGRESSED`)**:
   - Original RagBait product policy requires contributing at least one qualifying, explicitly consented, independently verified free-tier cloud credential (`billing_tier = 'verified_free_only'`, OpenRouter `data.is_free_tier == true`) before unlocking AI Chat.
   - M4.1 (`apps/api/app/modules/chat/router.py`) restricted only `POST /conversations`, `POST /messages`, and `POST /prepare-local`, allowing non-contributors onto `GET /conversations*`, `GET /messages`, `PATCH`, `DELETE`, `GET /sources`, `POST /cancel`, and `POST /finalize-local`.
   - M4.7 (`useChatAccessState.ts` & `ChatAccessGate.tsx`) downgraded frontend Chat enforcement to a non-blocking `ContributionGateBanner` so approved non-contributors entered `/chat` and could read/manage history or switch to Private Local.
   - `POST /api/v1/llm/chat/completions` (`llm_gateway/router.py`) and `POST /api/v1/routing/preview` (`routing/router.py`) checked only `require_approved_user`, bypassing `require_chat_entitlement` entirely.
3. **Knowledge Base Non-Admin Upload & Unsandboxed Parser Exposure (`CRITICAL SECURITY BLOCKER`)**:
   - `POST /api/v1/kb/documents/upload-intent` and `POST /api/v1/kb/documents/{document_id}/confirm-upload` (`knowledge_base/router.py`) allowed any `require_approved_user` (ordinary student) to obtain signed upload URLs (`HTTP 201`) and enqueue `kb_extraction_jobs` despite `parser_process.py` lacking OS/container/cgroups/network-namespace isolation.
   - `job_repository.py::claim_next_job` and `admin_service.py::retry_job` did not verify trusted uploader role (`admin`/`super_admin`), leaving any previously queued untrusted document reachable by the extraction worker.
   - Private Storage bucket (`study-materials`) already has `public = false` and `0` permissive client `storage.objects` RLS policies (`STORAGE_POLICIES = []`).
4. **Verified Working Subsystems (Confirmed Accurate)**:
   - OpenRouter verification (`GET https://openrouter.ai/api/v1/key`, strict boolean `data.is_free_tier`) and `:free` model allowlist excluding `openrouter/free`.
   - Envelope encryption vault (`private.api_key_secrets`, `ragbait_vault_service`, AES-256-GCM + context AAD + HMAC pepper).
   - M4.2–M4.6 RAG core: 420-token chunk ceiling, offline INT8 `multilingual-e5-small` ONNX (`100%` manifest hash verification), `match_subject_chunks` subject scoping (`0` leaks), XML trust boundaries, immutable `chat_message_sources` + live revocation masking, Redis 3-scope ZSET leases (`0` permit leaks, fail-closed `503`), and Private Local loopback separation.
   - M4.8 known empirical limitation: `plainto_tsquery` conjunctive `AND` yielded `0` FTS-only hits across 42 full conversational queries (`5/5` hits on short technical keyword probes).
   - 5 pre-existing backend failures (`4` in protected `test_community_inference.py`, `1` in `test_routing_execution.py` line 592 for discontinued `:6380` Community Redis) are unrelated to Phase 4 Chat/RAG.

---

## 2. Implemented Remediation & Verification (Steps 6–11)

1. **Step 6 — Contribution Hard Gate Restored**:
   - `apps/api/app/core/dependencies.py`: Fixed `check_user_chat_entitlement` to evaluate `approval_status != "approved"` before admin exemption, and propagated `ACCOUNT_NOT_APPROVED` cleanly in `require_chat_entitlement`.
   - `apps/api/app/modules/chat/router.py`, `apps/api/app/modules/llm_gateway/router.py`, `apps/api/app/modules/routing/router.py`: Enforced `Depends(require_chat_entitlement)` on all `/api/v1/chat/*` routes, `POST /api/v1/llm/chat/completions`, and `POST /api/v1/routing/preview`.
   - `apps/web/src/lib/chat/useChatAccessState.ts` & `apps/web/src/components/chat/ChatAccessGate.tsx` & `apps/web/src/app/(protected)/chat/`: Removed permissive `ContributionGateBanner`, restored hard full-page blocking gate until `entitlement.eligible === true`, preserved PostgreSQL conversation history rows untouched, and retained unrestricted `/knowledge-base` and `/credentials` access.
2. **Step 7 — Unified AI Chat Navigation & Inline Ollama Setup**:
   - `apps/web/src/components/Navbar.tsx`: Removed duplicate `"AI Inference"` link and consolidated to one canonical `"AI Chat"` destination (`/chat`).
   - `apps/web/src/middleware.ts` & `apps/web/src/app/(protected)/community-nodes/page.tsx`: Redirected `/local-inference*` and `/community-nodes*` to `/chat`.
   - `apps/web/src/components/chat/ChatComposer.tsx`: Replaced `/local-inference` link with an inline collapsible `Setup Guide` for Local Ollama & CORS (`OLLAMA_ORIGINS=*`).
3. **Step 8 — Trusted Knowledge Base Upload & Parser Hardening**:
   - `apps/api/app/core/dependencies.py`, `apps/api/app/modules/knowledge_base/router.py`, `service.py`: Added `require_trusted_kb_uploader` (`Depends(require_approved_user)`, raising `403 ADMIN_REQUIRED` for non-admins) and defense-in-depth checks on `/upload-intent` and `/confirm-upload`.
   - `apps/api/app/modules/knowledge_base/job_repository.py` & `admin_service.py`: Restricted `claim_next_job`, `complete_job_success`, and `retry_job` (`UNTRUSTED_DOCUMENT_PARSER_BLOCKED`) strictly to approved `admin`/`super_admin` uploaders.
   - `apps/web/src/app/(protected)/knowledge-base/**` & `DocumentList.tsx`: Gated `"Upload Material"` and `"Manage / Add Subjects"` UI controls to `admin`/`super_admin` and aligned `"Study Chat"` -> `"AI Chat"`.
4. **Step 9–11 — Automated Verification & Reconciliation**:
   - Added negative gate & trusted-upload tests in `apps/api/tests/test_audit_remediation_gates.py`, `apps/web/tests/UnifiedNavigation.test.tsx`, `ChatAccessAndPages.test.tsx`, `MarkdownAndComposer.test.tsx`, and `KnowledgeBasePages.test.tsx`, and reconciled Phase 2/3/4 test fixtures with the restored gates.
   - Verified `npx supabase test db` (`16 files, 352/352 passed`), `apps/web` (`pnpm lint`, `pnpm typecheck`, `29/29` test files / `169/169` specs, `pnpm build` `26/26` routes), `apps/api` (`ruff check`, `ruff format --check`, `mypy` `0` errors across `46` checked files, and full `pytest` `432 passed, 5 failed, 6 skipped` with the `5` failures strictly pre-existing discontinued Phase 2 community-pooling tests).
