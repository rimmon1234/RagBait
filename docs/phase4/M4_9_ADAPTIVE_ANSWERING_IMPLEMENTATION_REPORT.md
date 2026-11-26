# RagBait Phase 4 — Milestone M4.9: Adaptive Note-First RAG, General Knowledge & Web-Augmented Answering Implementation Report

**Milestone:** Phase 4 — M4.9 (Adaptive Answering + Advanced RAG + Web Integration)  
**Branch:** `phase4`  
**Date:** November 26, 2026  
**Final Verdict:** `IMPLEMENTED WITH DOCUMENTED LIMITATIONS`

---

## A. Current Strictness Investigation

Inspection of the M4.0–M4.8 codebase localized the rigid closed-book behavior across three layers:

1. **System Prompt & Abstention Directives (`apps/api/app/modules/chat/context/prompt_builder.py`)**:
   - `CORE_SYSTEM_INSTRUCTIONS` Rule 1 mandated: `"Answer academic questions using ONLY verified study material provided in <study_context_evidence> when available."`
   - Rule 6 required automatic abstention whenever supplied passages lacked exact details.
   - `NO_EVIDENCE_ADDENDUM` injected `"NO-EVIDENCE ABSTENTION DIRECTIVE"` whenever `has_evidence=False`, instructing the model to refuse answering whenever retrieval returned zero passages.
   - `GUARDRAIL_INSTRUCTIONS` Rule 4 prohibited mentioning external search even when server-verified web results could be supplied.
2. **Retrieval & Context Orchestration (`apps/api/app/modules/knowledge_base/retrieval/retrieval_service.py`, `apps/api/app/modules/chat/context/context_service.py`)**:
   - Single-shot retrieval embedded the unrewritten user prompt directly, frequently yielding zero FTS hits on long conversational prompts under `plainto_tsquery('english', ...)` and failing to re-rank RRF candidates.
   - Context preparation lacked an explicit question-intent classifier, evidence-sufficiency evaluator, or policy switch between `note_first_adaptive` and strict `notes_only`.
3. **Citations & Frontend Rendering (`apps/api/app/modules/chat/citations/`, `apps/web/src/components/chat/`)**:
   - Parser/validator accepted only `[S1]..[Sn]` markers linked to `public.chat_message_sources`. There was no `[W1]..[Wn]` external source snapshot schema or web source UI presentation.

### Security Controls Verified & Preserved
- XML structural delimiter isolation (`<study_context_evidence>`, `<user_query>`, `<conversation_summary>`, and now `<web_context_evidence trust_level="untrusted_external_web">`) via `sanitize_xml_text`.
- Explicit prompt-injection guardrails ignoring directives embedded in PDFs, web pages, or user inputs and refusing secret/tool/cross-subject exfiltration.
- Hard contribution gate (`require_chat_entitlement` without admin exemption), subject-scoped SQL retrieval (`match_subject_chunks`), immutable note snapshots with live 3-way catalog revalidation masking, and zero-cloud Private Local isolation.

---

## B. New Answering Architecture & Advanced RAG Pipeline

### 1. Answer Policies (`NOTE_FIRST_ADAPTIVE` vs. `NOTES_ONLY`)
- **Default (`note_first_adaptive`)**: Enforces Priority 1 **Uploaded Notes (`[Sn]`)** -> Priority 2 **General Academic Knowledge** -> Priority 3 **Verified External Web Sources (`[Wn]`)**.
- **Strict Opt-In (`notes_only`)**: Enforced deterministically server-side in `build_answer_plan()` (`apps/api/app/modules/chat/context/answer_planner.py`), setting `use_general_knowledge=False`, `requires_web_search=False`, and requiring abstention if course passages are absent.

### 2. Advanced RAG Techniques (`apps/api/app/modules/knowledge_base/retrieval/advanced_rag.py`)
1. **LLM Query Rewriting (`plan_and_expand_queries`)**: Resolves conversational pronouns and rewrites questions into standalone technical queries using the student's/pool's eligible cloud credential (`discover_cloud_candidates` + `filter_candidates` + `rank_candidates` + `retrieve_decrypted_credential`, max 2 attempts, bounded 6s timeout). Private Local (`inference_mode == "local"`) strictly skips cloud helpers and uses local deterministic normalization.
2. **Multi-Query Expansion & Multi-Query Hybrid Fusion (`RetrievalService.retrieve_subject_chunks_multi`)**: Generates up to 2 complementary search queries per turn, runs authorized subject-scoped `match_subject_chunks` across the query set, and deduplicates by `chunk_id` with a multi-query consensus boost.
3. **LLM Re-Ranking & Subselection (`rerank_and_subselect_chunks`)**: Evaluates top RRF candidates (bounded snippet length inside untrusted XML) to filter off-topic passages, reorder top chunks (`ADVANCED_RAG_RERANK_TOP_K=8`), and emit a structured sufficiency signal (`sufficient`, `partial`, `insufficient`, `conflicting`).
4. **Improved Structured Prompts (`prompt_builder.py`)**: Separates **From your notes** (`[Sn]`), **Additional explanation** (general academic knowledge without fake `[Sn]` tags), and **Web research** (`[Wn]`) naturally only when applicable, explicitly addressing conflicts and honest uncertainty.

### 3. Adaptive Decision Stage & Sufficiency Evaluation (`answer_planner.py`)
- Evaluates lexical overlap, vector proximity, RRF rank, LLM sufficiency hint, question type (`academic_conceptual`, `course_specific`, `factual_current`, `explicit_web_search`, `comparative`, `mathematical_procedural`, `ambiguous_or_private`), and web-search triggers.
- Outputs `EvidenceSufficiencyStatus` (`notes_sufficient`, `notes_partially_sufficient`, `notes_insufficient`, `notes_conflicting`, `external_verification_recommended`) and `AnswerPlan`.
- Retains strict abstention for `ambiguous_or_private` questions (e.g., personal exam seats/roll numbers, unresolved standalone `"Explain it."`) and unverified claims.

---

## C. Internet Integration & SSRF Safeguards

- **Server-Side Adapter Interface (`apps/api/app/modules/chat/web_search/`)**:
  - Supports admin-configured providers via `RAGBAIT_WEB_SEARCH_PROVIDER` (`none` | `tavily` | `brave`) and `RAGBAIT_WEB_SEARCH_API_KEY` (never pooled from student LLM credentials or exposed to the browser).
  - Supports **Tavily Search REST API** (`https://api.tavily.com/search`, free Researcher tier option) and **Brave Search REST API** (`https://api.search.brave.com/res/v1/web/search`).
  - **Current Environment Configuration Status**: `apps/api/.env` has no `RAGBAIT_WEB_SEARCH_API_KEY` configured (`WEB_SEARCH_PROVIDER="none"`). When invoked without a configured key, `WebSearchService.search()` deterministically returns `status="unavailable_unconfigured"` with `results=[]` and zero fabricated sources.
- **Rate Limits, Privacy & Cost Bounds**:
  - `redact_search_query()` strips emails, bearer tokens, and `sk-...` / `AIza...` / `gsk_...` secrets before dispatch.
  - Bounded `WEB_SEARCH_MAX_QUERIES_PER_TURN=2`, `WEB_SEARCH_MAX_RESULTS_PER_QUERY=5`, `WEB_SEARCH_TIMEOUT_SECONDS=8.0`, Redis cache (`900s` TTL), per-user RPM (`6`), and global RPM (`60`).
- **SSRF-Hardened Page Fetcher (`web_search/security.py`)**:
  - Rejects non-HTTP(S) schemes, embedded userinfo credentials, `.local` / `.localhost` / `.internal` hostnames, loopback, RFC-1918 private networks, IPv6 unique-local/link-local, and cloud metadata IPs (`169.254.169.254`) via DNS resolution checks (`_is_public_ip`).
  - Disables automatic HTTP redirects and re-validates every redirect hop (`<= 3`), restricts MIME types (`text/html`, `application/xhtml+xml`, `text/plain`), aborts streams above `WEB_FETCH_MAX_BYTES` (`512 KiB`), and strips `<script>`, `<style>`, `<iframe>`, and `<svg>` via stdlib HTML parsing.

---

## D. Source & Citation Architecture

- **KB Notes (`public.chat_message_sources`)**: Unchanged table and immutability contract; cited via `[S1]`, `[S2]`, ..., rendered in blue badges with document viewer page jumps (`/knowledge-base/document/{id}?page=X`).
- **External Web Sources (`public.chat_message_web_sources` in [20260309000000_m4_9_web_sources_and_policy.sql](file:///D:/Projects/RagBait/supabase/migrations/20260309000000_m4_9_web_sources_and_policy.sql))**:
  - Stores immutable snapshots (`citation_key ~ '^W[1-9][0-9]*$'`, `title`, `url`, `domain`, `publisher`, `published_at`, `retrieved_at`, `excerpt`, `content_hash`, `snippet_only`, `availability_status`, `included_in_context`, `referenced_in_answer`).
  - Protected by immutability trigger (`enforce_chat_message_web_source_immutability`), controlled `SECURITY DEFINER` provenance update (`mark_chat_message_web_sources_referenced`), and Row-Level Security (`REVOKE ALL` from `anon`, owner SELECT RLS).
- **Citation Validation (`citations/parser.py`, `citations/validator.py`, `citations/source_repository.py`)**:
  - Parses and validates both `[Sn]` and `[Wn]` against separate server registries while rejecting fake `[S99]`, `[W99]`, and malformed markers (`[SOURCE1]`, `[WEB1]`). General knowledge statements pass cleanly without requiring fake citations.
- **Sources & Context Drawer & Composer UX (`apps/web/src/components/chat/`)**:
  - `MarkdownMessageRenderer.tsx`: Distinct interactive `[S1]` (blue) and `[W1]` (teal) citation chips.
  - `SourcesDrawer.tsx`: Distinguishes KB course notes from Web sources (displaying domain, publisher, published/retrieved dates, `"Search snippet"` vs `"Fetched excerpt"` scope, and safe external link button), preserving Cited vs All Considered tabs and revocation masking.
  - `ChatComposer.tsx` & `chat/[id]/page.tsx`: Adds compact `Notes first` (default) vs `Notes only` selector backed server-side.

---

## E. Files Changed

### Database & Tests (Supabase)
- `supabase/migrations/20260309000000_m4_9_web_sources_and_policy.sql` — Web source snapshot table, immutability trigger, provenance function, RLS.
- `supabase/tests/m4_9_web_sources_test.sql` — 8 pgTAP assertions covering schema, privileges, immutability, and controlled transitions.

### Backend (`apps/api`)
- `app/modules/chat/context/config.py` — M4.9 Advanced RAG, web budget, and server-side web search settings.
- `app/modules/chat/context/schemas.py` — `AnswerPolicy`, `QuestionType`, `EvidenceSufficiencyStatus`, `AttributionMode`, `SelectedWebSource`, `AdvancedRagTrace`, `AnswerPlan`.
- `app/modules/chat/context/answer_planner.py` — Stage 3/4 sufficiency evaluator and decision matrix.
- `app/modules/chat/context/formatter.py` — Untrusted XML `<web_context_evidence>` & `<web_source>` serialization.
- `app/modules/chat/context/prompt_builder.py` — `NOTE_FIRST_ADAPTIVE` & `NOTES_ONLY` structured prompt builders with preserved injection guardrails.
- `app/modules/chat/context/budget.py` — Prioritizes KB notes first, then bounded external web evidence, then summary and recent turns.
- `app/modules/chat/context/context_service.py` — Orchestrates history loading, query rewrite/expansion, multi-query retrieval, LLM re-ranking, answer planning, web search fallback, and budget allocation.
- `app/modules/knowledge_base/retrieval/advanced_rag.py` — LLM query rewriting, query expansion, heuristic intent classifier, and LLM re-ranking via eligible cloud pool keys.
- `app/modules/knowledge_base/retrieval/retrieval_service.py` — Added `retrieve_subject_chunks_multi()`.
- `app/modules/chat/web_search/{__init__.py,schemas.py,security.py,adapters.py,service.py}` — Modular Tavily/Brave adapters, SSRF & redirect protection, HTML sanitization, redaction, rate limits, Redis caching.
- `app/modules/chat/citations/{schemas.py,parser.py,validator.py,source_repository.py}` — Dual `[Sn]` + `[Wn]` citation parsing/validation and web snapshot persistence/revalidation.
- `app/modules/chat/orchestration/{schemas.py,persistence.py,generation.py,service.py,streaming_service.py}` — Wired `answer_policy`, `[Wn]` persistence, SSE `sources_identified` enrichment, and telemetry.
- `tests/test_m4_9_adaptive_rag.py` — Unit, SSRF, Advanced RAG, budget, citation, and snapshot DB persistence tests.
- `tests/evaluation/m4_9_adaptive_answering/{__init__.py,adaptive_dataset_v1.json,test_m4_9_adaptive_evaluation.py,results_m4_9_v1.json}` — 30-case bilingual EN/BN adaptive answering benchmark (keeping M4.8 `golden_dataset_v1.json` untouched).
- `tests/evaluation/run_m4_8_evaluation.py` — Preserved immutable `llm_usage_events` rows during synthetic pre-cleanup.

### Frontend (`apps/web`)
- `src/types/chat.ts` — Extended `MessageSourceRead`, `StreamSourceSummary`, request types, and `AnswerPolicy`.
- `src/lib/chat/useChatSession.ts` — Propagates `answer_policy` across Cloud SSE and Private Local submissions & retries.
- `src/components/chat/MarkdownMessageRenderer.tsx` — Added interactive `[Wn]` web citation chips alongside `[Sn]` chips.
- `src/components/chat/SourcesDrawer.tsx` — Added distinct Web Source card rendering (`Open Source Website`, domain/date/snippet metadata).
- `src/components/chat/ChatComposer.tsx` & `src/components/chat/ChatInterface.tsx` — Added compact `Notes first` / `Notes only` source mode selector.
- `src/app/(protected)/chat/[id]/page.tsx` — Connected `answerPolicy` state to session & composer.
- `tests/chat/{MarkdownAndComposer.test.tsx,SourcesDrawer.test.tsx}` — Added tests for `[W1]` chips, web source cards, and `Notes only` toggle.

---

## F. Adaptive Answering Evaluation Results (`results_m4_9_v1.json`)

Executed across `adaptive_dataset_v1.json` (30 English & Bengali scenarios with deterministic mocked search adapters; zero paid API consumption):

| Metric | Observed Result |
|---|---|
| Total scenarios | **30** |
| Notes-grounded correctness | **11 / 11 (100%)** |
| General-knowledge usefulness (partial & missing notes) | **8 / 8 (100%)** |
| Appropriate web-search triggering | **7 / 7 (100%)** |
| Unnecessary web-search rate | **0 / 18 (0.0%)** |
| Abstention precision (private / ambiguous) | **3 / 3 (100%)** |
| Knowledge Base `[Sn]` citation validity | **1.0000** |
| External Web `[Wn]` citation validity | **1.0000** |
| Unsupported / hallucinated citation rate | **0.0000** |
| Context-budget compliance rate | **1.0000** |
| Security & injection test pass rate | **1.0000** |
| Average web searches per turn | **0.2333** |

---

## G. Regression Results

- **Database (pgTAP)**: `npx supabase test db --local` -> **17/17 files, 360/360 tests PASSED** (includes `m4_9_web_sources_test.sql`, `rls_test.sql`, and all M2–M4.5 tests).
- **Backend (`apps/api`)**:
  - `uv run ruff check` on M4.9 & modified modules -> **All checks passed!**
  - `uv run mypy app/modules/chat/ app/modules/knowledge_base/retrieval/` -> **Success: no issues found in 46 source files**.
  - `uv run pytest` targeted + M4.1–M4.8 regression run (`test_m4_9_adaptive_rag.py`, `test_m4_9_adaptive_evaluation.py`, `test_m4_5_context_budget.py`, `test_m4_5_citations_and_compaction.py`, `test_m4_5_hardening.py`, `test_m4_5_orchestration_e2e.py`, `test_m4_6_streaming_lifecycle.py`, `test_m4_8_rag_evaluation.py`, `test_kb_retrieval.py`, `test_kb_chunking.py`, `test_kb_embeddings.py`, `test_chat_persistence.py`, `test_audit_remediation_gates.py`) -> **91 passed in 45.44s**. *(Pre-existing Community Redis suites `test_community_inference.py` and `test_route_and_dispatch_community_candidate` were not part of this subset because legacy community worker compute is deprecated and port 6380 container was stopped).*
- **Frontend (`apps/web`)**:
  - `npm run lint` -> **No ESLint warnings or errors**.
  - `npm run typecheck` -> **0 TypeScript errors**.
  - `npx vitest run` -> **29/29 files, 172/172 tests PASSED** (`ollama-live-smoke.test.ts` opt-in hardware probe skipped as `OLLAMA_LIVE_TEST` unset).
  - `npm run build` -> **26/26 App Router routes built successfully**.

---

## H. Security Verification

- **Prompt injection resistance**: XML delimiter escaping + untrusted instructions blocks tested across both PDFs and web excerpts (`&lt;system_instructions&gt;`).
- **SSRF & external fetch hardening**: Verified blocking of `127.0.0.1`, `localhost`, `10.0.0.5`, `192.168.1.1`, `[::1]`, `169.254.169.254`, `file://`, `ftp://`, `user:pass@`, redirect-to-loopback (`302`), and non-HTML binary MIME types.
- **No source leakage or fake citations**: Cross-subject retrieval returned 0 cross-subject chunks; archived document excerpts masked cleanly (`Archived or Moderated`); cross-user source requests denied (`403`/`404`); fabricated `[S9]`/`[W4]` rejected by `validate_citations`.
- **Entitlement & Private Local integrity**: `test_audit_remediation_gates.py` and `test_chat_persistence.py` confirmed hard contribution gate enforcement without admin exemptions; `test_advanced_rag_rewrite_expansion_rerank_and_local_privacy` verified `inference_mode == "local"` triggers zero cloud/web calls.

---

## I. Protected File Integrity

Baseline and post-implementation SHA-256 hashes matched identically across all 9 protected files:

| File | SHA-256 Hash (Baseline == Final) |
|---|---|
| `apps/api/app/modules/community/ws.py` | `8B9CB2574B6C416D8079FD274D7C8B35F6014A0134CF469495B32FFE03C04577` |
| `apps/api/tests/test_community_inference.py` | `3E89A5D94C5F9D16DF655E7304F80CB3861FC59E87AA2B153C83718F177D5E0A` |
| `apps/web/src/app/(auth)/login/page.tsx` | `DCBCEBA261AE94BEB15F3A8BB0838EA23A7D95082F99012743D1D8FA504B2D85` |
| `apps/web/src/app/(protected)/access/contribution-required/page.tsx` | `0FFF5D1A8517CEBCA4B797D6ED54A83318704DEDF4ED04C87B2F85E1C43C1006` |
| `apps/web/src/app/(protected)/credentials/page.tsx` | `6B23A3D8ADF28F8D91616B674B5632C83BE463E6DA12586C30FF0B1D2EF8F5C3` |
| `apps/web/src/app/(protected)/local-inference/page.tsx` | `2BB7AE20CE54FCE8601A54F127ACA660A97EE8FFB4AA394D62C141C545EB1BCB` |
| `apps/web/src/components/CommunityConsentModal.tsx` | `F77C43EAFA485EC65095FACC7450F4F80A6DFDBB27E4EB0FC10B64D58F0A177A` |
| `apps/web/src/components/LocalInferenceWorkspace.tsx` | `654E6ED4C1B38F158D597708FA8492AF79D203620F918697C9AA6F7E93F436AA` |
| `apps/web/src/lib/routing-client.ts` | `9AADB4EB2135B694F595BEF08ED707670CCF68F07B2FC1AADA12897EE85827EE` |

---

## J. Remaining Limitations

- **Implemented & Verified**: `NOTE_FIRST_ADAPTIVE` answer planning, strict `NOTES_ONLY` server toggle, LLM query rewrite/expansion/re-ranking (tested with mocked callers & pool discovery), SSRF-safe web fetcher/redaction/rate-limiting, immutable `chat_message_web_sources` migration, dual `[Sn]`/`[Wn]` validation & `SourcesDrawer` rendering, and 30-case bilingual M4.9 evaluation suite.
- **External Provider Configuration Blocker (Live Web Search Disabled by Default)**: No Tavily or Brave Search API credential (`RAGBAIT_WEB_SEARCH_PROVIDER` / `RAGBAIT_WEB_SEARCH_API_KEY`) is configured in `apps/api/.env`. Live internet search therefore returns `unavailable_unconfigured` until an administrator sets `RAGBAIT_WEB_SEARCH_PROVIDER=tavily` (or `brave`) and `RAGBAIT_WEB_SEARCH_API_KEY=<admin_key>`.
- **Live Cloud LLM End-to-End Answer Generation**: Evaluated deterministically via unit/integration/evaluation mocks rather than live external Tavily + OpenRouter generation billing.

---

## K. Final Verdict

**`IMPLEMENTED WITH DOCUMENTED LIMITATIONS`**

Adaptive note-first RAG, general-knowledge supplementation, LLM query rewriting/expansion/re-ranking, server-enforced `Notes only` strict toggle, SSRF-hardened web-search architecture, dual `[Sn]`/`[Wn]` provenance snapshots, and UI rendering are implemented and verified across DB, backend, and frontend test suites. Live external web search awaits an administrator-supplied search API key (`RAGBAIT_WEB_SEARCH_PROVIDER` + `RAGBAIT_WEB_SEARCH_API_KEY`).
