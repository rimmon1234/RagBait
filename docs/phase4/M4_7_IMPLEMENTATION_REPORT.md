# RagBait Phase 4 — M4.7 Implementation & Verification Report

**Milestone:** M4.7 — Student Chat UI, Streaming Experience & Sources/Context Panel  
**Branch:** `phase4`  
**Base HEAD:** `422e40c` (on top of M4.6 `ad405c0` and M4.5 hardening `36f4a46`)  
**Date:** November 25, 2026  

---

## A. Architecture & Integration Summary

M4.7 delivers the complete student-facing AI study assistant UI inside `apps/web`, wired directly to the existing M4.1–M4.6 FastAPI backend contracts without introducing parallel chat routes, duplicating backend state, or bypassing authorization/visibility rules.

### Key Architectural Guarantees
1. **Single Authoritative Chat Workspace**: Upgraded `/chat` and `/chat/[id]` and existing `ChatSidebar`, `ChatInterface`, `NewConversationModal`, and `RenameConversationModal` rather than scaffolding a separate UI tree.
2. **Backend Contract Fidelity**:
   - Cloud generation streams over SSE (`POST /api/v1/chat/conversations/{id}/messages` with `stream: true`) using the M4.6 `streamConversationMessage` generator and explicit cancellation via `abortAndCancelGeneration` (`POST /api/v1/chat/conversations/{id}/messages/{message_id}/cancel`).
   - Authoritative citation provenance is fetched on demand via `GET /api/v1/chat/messages/{message_id}/sources`, never trusted solely from client-side parsing or stale streaming candidates once terminal state is reached.
   - Document page jumps use the backend's `document_viewer_url` (`/knowledge-base/document/[id]?page=X`), served by a dedicated Next.js App Router viewer page wrapping `DocumentPageViewer`.
3. **Private Local Isolation**: Private Local Ollama mode executes the 3-step `prepare-local` -> direct loopback `http://127.0.0.1:11434` (`ollamaClient.chatStream`) -> `finalize-local` pipeline with zero fallback to Cloud AI if local Ollama is offline or permission-denied.

---

## B. M4.6 Frontend Helper & Contract Verification

Before building M4.7 UI layers, the M4.6 backend and frontend helpers were audited:
- **`apps/web/src/lib/chat/streaming-client.ts`**: `parseSseByteStream` and `streamConversationMessage` handle multi-byte UTF-8 splits (`TextDecoder({ stream: true })`), CRLF/LF framing, multi-line `data:` joins, SSE comments (`:`), malformed JSON skip tolerance, and typed `ApiError` throwing on pre-stream HTTP errors (`401`, `403`, `409`, `429`, `503`). Updated `abortAndCancelGeneration` to return `Promise<CancelGenerationResponse | null>`.
- **Cancellation Canonical Route**: Both `/api/v1/chat/messages/{message_id}/cancel` and `/api/v1/chat/conversations/{conversation_id}/messages/{message_id}/cancel` exist in `chat/router.py`; standardized M4.7 calls on `cancelGeneration(messageId, conversationId)` which targets the conversation-scoped route when `conversationId` is present.
- **Physical Page Jump Gap Resolved**: Backend `source_repository.py` emits `/knowledge-base/document/{document_id}?page={page_number_start}`, which previously lacked an App Router route in `apps/web/src/app/(protected)/`. Added `apps/web/src/app/(protected)/knowledge-base/document/[id]/page.tsx` so `"Open at Page X"` lands on the exact physical PDF page.

---

## C. UX & Generation State Matrix

| State / Phase | Visual Behavior | Composer & Controls | Provenance / Retry Behavior |
|---|---|---|---|
| `idle` | Clean timeline with completed/cancelled/failed badges per persisted turn | Enabled for active chats (contributor / Private Local connected) | `Inspect Sources` queries `/messages/{id}/sources` |
| `submitting` | Optimistic user turn + pending assistant spinner row | Disabled; Stop button shown | No provisional sources assumed yet |
| `admission_waiting` | Queue banner with `#position` and estimated wait (`~Ns`) | Send locked; Stop/Cancel active | Cancelling aborts stream + calls server `/cancel` |
| `generating` | Streaming text via `text_delta`; `"Streaming draft (unvalidated)"` badge | Stop/Cancel button active | Candidate `[S1]` chips show provisional banner on inspect |
| `content_replaced` | Replaces assistant buffer cleanly (never appends) + reconciliation note | Wait for `generation_completed` | Reconciled citations match Stage-3 snapshot |
| `cancelling` | Amber `"Stopping generation..."` indicator | Stop disabled while request settles | Reconciles `409` race via `getConversationMessages()` |
| `completed` | Verified `"Completed"` pill, model badge, final `[Sn]` citation chips | Composer unlocked for next question | `SourcesDrawer` separates Cited (`referenced_in_answer`) vs Considered |
| `failed` | Red alert banner + message error detail | Composer available; draft restored if pre-stream failed | Retry button reuses same `idempotency_key` (`retryPayload`) |
| `cancelled` | Amber `"Cancelled (partial)"` badge retaining persisted text | Composer unlocked | Partial answer preserved cleanly |
| `archived` / `deleted` | Green/Gray status pill in header | Read-only banner (`Restore it to send new questions`) | Full read access to history & available sources |
| `contribution_required` | Non-blocking amber `ContributionGateBanner` with links to `/credentials` | Can browse, rename, archive, restore, delete; Send/New Chat gated | Existing history & sources remain accessible |
| `pending` / `rejected` / `suspended` | Full-screen `ChatAccessGate` alert; zero chat content leaked | All chat API fetches suppressed | Directed to `/access/pending` or `/` |
| `private_local` | Emerald loopback status badge (`127.0.0.1:11434 — Loopback only`) + model `<select>` | Local Ollama probed on switch; offline blocks Send with zero cloud fallback | `prepare-local` -> local NDJSON stream -> `finalize-local` |

---

## D. Files Created & Modified

### Production Frontend Code (`apps/web`)
| File | Action | Responsibility |
|---|---|---|
| [chat.ts](file:///d:/Projects/RagBait/apps/web/src/types/chat.ts) | Modified | Extended `ChatMessage`, added `MessageSourceRead`, `MessageSourcesResponse`, `PrepareLocal*`, `FinalizeLocal*`, `GenerationPhase`, `InferenceExecutionMode` |
| [chat-client.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat-client.ts) | Modified | Added `getMessageSources`, `prepareLocalTurn`, `finalizeLocalTurn`, `getAccessState`, `getChatEntitlementState` |
| [streaming-client.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat/streaming-client.ts) | Modified | Typed return `Promise<CancelGenerationResponse \| null>` on `abortAndCancelGeneration` |
| [useChatAccessState.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat/useChatAccessState.ts) | Created | Auth/approval/contribution access state hook |
| [useChatSession.ts](file:///d:/Projects/RagBait/apps/web/src/lib/chat/useChatSession.ts) | Created | SSE stream state machine, `seenEventIds` dedup, replay handling, EOF/409 reconciliation, idempotent retry, Private Local lifecycle, unmount cleanup |
| [MarkdownMessageRenderer.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/MarkdownMessageRenderer.tsx) | Created | Pure-React XSS-safe Markdown AST renderer (headings, lists, code blocks, quotes, tables, safe links, interactive `[Sn]` badges) |
| [SourcesDrawer.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/SourcesDrawer.tsx) | Created | Desktop slide-over / mobile sheet Sources & Context drawer with Cited/Considered tabs, masked unavailable source handling, and `"Open at Page X"` links |
| [MessageBubble.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/MessageBubble.tsx) | Created | Memoized user/assistant bubble with provisional/completed/cancelled/failed states & source inspection actions |
| [ChatComposer.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/ChatComposer.tsx) | Created | Auto-growing input with `32,768` char validation (no truncation), Enter/Shift+Enter, Stop button, and Cloud vs Private Local Ollama selector |
| [ChatAccessGate.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/ChatAccessGate.tsx) | Created | Full-page auth/restricted-account guard and non-blocking `ContributionGateBanner` |
| [RenameConversationModal.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/RenameConversationModal.tsx) | Modified | Synced `title` state via `useEffect` on open/conversation change |
| [NewConversationModal.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/NewConversationModal.tsx) | Modified | Synced `defaultSubjectId`, added double-submit lock and `CHAT_CONTRIBUTION_REQUIRED` links |
| [ChatSidebar.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/ChatSidebar.tsx) | Modified | Added error/retry panel, loading skeletons, activity date, mobile drawer close, and `canCreateChat` support |
| [ChatInterface.tsx](file:///d:/Projects/RagBait/apps/web/src/components/chat/ChatInterface.tsx) | Modified | Wired `MessageBubble`, `ChatComposer`, smart near-bottom scrolling, queue/cancelling/retry banners, starter prompts, and mobile sidebar button |
| [page.tsx (chat)](file:///d:/Projects/RagBait/apps/web/src/app/(protected)/chat/page.tsx) | Modified | Added `ChatAccessGate`, `ContributionGateBanner`, `401` handling, and list retry controls |
| [page.tsx (chat/[id])](file:///d:/Projects/RagBait/apps/web/src/app/(protected)/chat/[id]/page.tsx) | Modified | Responsive desktop sidebar + mobile slide-over drawer, `useChatSession`, `SourcesDrawer`, and `ChatAccessGate` integration |
| [page.tsx (document/[id])](file:///d:/Projects/RagBait/apps/web/src/app/(protected)/knowledge-base/document/[id]/page.tsx) | Created | Direct physical page jump viewer route for `/knowledge-base/document/[id]?page=X` |

### Test Files (`apps/web/tests/chat/`)
| File | Coverage |
|---|---|
| [streaming-client.test.ts](file:///d:/Projects/RagBait/apps/web/tests/chat/streaming-client.test.ts) | Chunked UTF-8 Bengali/Hindi decoding, multi-event packets, HTTP errors, abort+cancel, malformed JSON & unknown event tolerance |
| [useChatSession.test.ts](file:///d:/Projects/RagBait/apps/web/tests/chat/useChatSession.test.ts) | All SSE events, `content_replaced`, replay dedup, idempotent retry reuse, EOF reconciliation, 409 cancel-race reconciliation, Private Local lifecycle, zero cloud fallback on Ollama error |
| [SourcesDrawer.test.tsx](file:///d:/Projects/RagBait/apps/web/tests/chat/SourcesDrawer.test.tsx) | Authoritative `/sources` fetch, Cited vs Considered tabs, citation focus auto-tab switch, provisional banner, masked unavailable sources, `"Open at Page X"` URL, Escape close & stale clear |
| [MarkdownAndComposer.test.tsx](file:///d:/Projects/RagBait/apps/web/tests/chat/MarkdownAndComposer.test.tsx) | `<script>` / `javascript:` sanitization, tables/lists/code blocks/`[Sn]` click, Enter/Shift+Enter, 32,768 max-length check without truncation, Private Local loopback UI & Stop button |
| [ChatAccessAndPages.test.tsx](file:///d:/Projects/RagBait/apps/web/tests/chat/ChatAccessAndPages.test.tsx) | Pending user access block, approved non-contributor history browse + new-chat gate, `/knowledge-base/document/[id]?page=142` batch offset + physical page render |
| [ChatComponents.test.tsx](file:///d:/Projects/RagBait/apps/web/tests/chat/ChatComponents.test.tsx) | Preserved M4.1 component tests and updated M4.7 empty-state assertion |

---

## E. Test & Build Verification Output

Executed in `d:\Projects\RagBait\apps\web`:

1. **`pnpm lint` (`next lint`)**: Passed — `✔ No ESLint warnings or errors`.
2. **`pnpm typecheck` (`tsc --noEmit`)**: Passed — Exit code `0`, zero TypeScript errors.
3. **`pnpm test` (`vitest run`)**: Passed — `Test Files 28 passed (28)`, `Tests 165 passed (165)`. *(Note: `tests/local-inference/ollama-live-smoke.test.ts` is an opt-in ambient hardware smoke test that prints `Opt-in live smoke test skipped: OLLAMA_LIVE_TEST is not set.` when `OLLAMA_LIVE_TEST` is unset while passing its Vitest case.)*
4. **`pnpm build` (`next build`)**: Passed — Compiled cleanly and generated all 26 App Router pages including `/chat`, `/chat/[id]`, and `/knowledge-base/document/[id]`.

---

## F. Verification Scope & Non-Executed Live Runtime Checks

- **Automated Verification Executed**: Static ESLint analysis, full strict TypeScript compilation, 165 Vitest + React Testing Library unit/component/hook tests (using mocked HTTP/SSE/Ollama responses), and Next.js production build.
- **Not Executed in This Task**: Live end-to-end manual/browser Playwright clicks against a running FastAPI + Supabase + Redis + live Ollama daemon instance. (Scheduled for M4.8 E2E release certification alongside the 50-question golden evaluation benchmark.)
- **Known Pre-Existing Unrelated Failures**: Per preflight inspection, 4 tests in `apps/api/tests/test_community_inference.py` assert HTTP `410 Gone` on discontinued Phase 2 compute-pooling routes (`/api/v1/community/nodes/*`, `/api/v1/community/jobs/*`) and belong to a protected user file outside M4.7 chat.

---

## G. Protected User Files Integrity (SHA-256 Verification)

All 9 pre-existing protected user-owned files remained untouched and unstaged throughout M4.7:

| File | Verified SHA-256 (Baseline & Final Match) |
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
