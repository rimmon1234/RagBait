# Unified Chat & Restored Contribution Gate Architecture Design

**Document Version:** 1.0.0  
**Branch:** `phase4`  
**Date:** November 25, 2026  

---

## 1. Investigation: Why "Study Chat" and "AI Inference" Existed Separately

Forensic inspection of documentation (`docs/M2_7_IMPLEMENTATION_PLAN.md`, `docs/plans/M2.9-intelligent-routing.md`, `docs/phase4/ARCHITECTURE_DECISIONS.md` ADR 01, `docs/phase4/M4_1_IMPLEMENTATION_REPORT.md`) and frontend/backend source code establishes the exact historical cause:

1. **Phase 2 Origin (`/local-inference` — labeled `"AI Inference"` in `Navbar.tsx`)**:
   - Introduced in M2.7 as a browser-to-loopback Ollama playground using volatile React state (`useState<Message[]>`), with zero database persistence.
   - Extended in M2.9 (`M2.9` unified routing) with a mode switcher (`private` | `auto` | `cloud_only` in `apps/web/src/app/(protected)/local-inference/page.tsx`, and `auto` | `owner_only` | `private` in `apps/web/src/components/LocalInferenceWorkspace.tsx`) calling `POST /api/v1/llm/chat/completions`.
2. **Phase 4 Origin (`/chat` & `/chat/[id]` — labeled `"Study Chat"` in `Navbar.tsx`)**:
   - Introduced in M4.1 to satisfy ADR 01/02/03: durable PostgreSQL conversations (`chat_conversations`, `chat_messages`), subject scoping (`subject_id`), hybrid retrieval + XML citation snapshots (`chat_message_sources`), SSE streaming & cancellation (M4.6), AND integrated **Private Local Ollama** (`prepare-local` -> loopback `http://127.0.0.1:11434` -> `finalize-local`) plus `SourcesDrawer` (M4.7).
3. **Why Fragmentation Remained**:
   - `Navbar.tsx` kept both links (`Study Chat` -> `/chat` and `AI Inference` -> `/local-inference`), while `/chat` grew nearly every capability plus subject-grounded RAG, leaving `/local-inference` as an unpersisted legacy playground whose route `/api/v1/llm/chat/completions` lacked `require_chat_entitlement`.

### Capability Overlap vs. Distinction Matrix

| Capability | `/chat` (Study Chat — M4.1–M4.7) | `/local-inference` (AI Inference — M2.7/M2.9) | Resolution |
|---|---|---|---|
| Persistent PostgreSQL history & sidebar | Yes (`chat_conversations`, `chat_messages`) | No (ephemeral React state only) | **Keep authoritative `/chat` persistence** |
| Subject-bound RAG + `[Sn]` Citations + `SourcesDrawer` | Yes | No | **Retained in canonical `/chat`** |
| Shared Cloud AI (pooled free-tier / routing) | Yes (`POST /chat/conversations/{id}/messages`) | Yes (`POST /api/v1/llm/chat/completions`) | **Consolidate into `/chat`; enforce gate on `/llm/chat/completions`** |
| Execution routing preference (`auto` vs `My API Keys Only` / `private_local`) | Cloud SSE + Private Local selector | Explicit `auto` / `owner_only` / `private` selector | **Expose `Cloud (Auto)` / `My API Keys Only` / `Private Local` cleanly in `ChatComposer` if compatible, or preserve Cloud + Private Local** |
| Ollama Loopback connection (`127.0.0.1:11434`) & Setup Guide | Loopback probe + `/local-inference` Setup link | Loopback LNA permission check + inline CORS Setup Guide | **Move Ollama Setup & CORS Guide inline inside `ChatComposer.tsx`** |
| Contribution Hard Gate (`require_chat_entitlement`) | Partial (only POST creation/turn) | Bypassed in `local-inference/page.tsx` & `llm_gateway/router.py` | **Enforce hard gate across ALL `/api/v1/chat/*`, `/api/v1/llm/chat/completions`, `/api/v1/routing/preview`, and frontend `/chat`** |

**Selected Consolidation Option**: **Option A — Consolidate duplicate chat destinations into one canonical `/chat` experience**.
- Note: `apps/web/src/app/(protected)/local-inference/page.tsx`, `apps/web/src/components/LocalInferenceWorkspace.tsx`, and `apps/web/src/lib/routing-client.ts` are **protected user-owned files** and remain byte-for-byte unmodified.
- Consolidation is enforced cleanly through:
  1. `apps/web/src/components/Navbar.tsx`: Remove `AI Inference` link; rename `Study Chat` -> **`AI Chat`** (`href="/chat"`), yielding a single canonical Chat navigation link on both desktop and mobile viewports.
  2. `apps/web/src/middleware.ts`: Redirect `/local-inference`, `/local-inference/*`, and `/community-nodes` server-side (`NextResponse.redirect(..., "/chat")`) so direct URLs, bookmarks, and old `/local-inference` links always land in the canonical unified `/chat` workspace guarded by auth and the hard contribution gate.
  3. `apps/web/src/app/(protected)/community-nodes/page.tsx`: Update client fallback target from `/local-inference` to `/chat`.
  4. `apps/web/src/components/chat/ChatComposer.tsx`: Replace `<Link href="/local-inference">Setup</Link>` with an inline collapsible **Ollama Setup & CORS Guide** drawer/panel directly inside `/chat`, and keep Cloud vs Private Local cleanly separated internally (`useChatSession.ts`: `prepare-local` -> loopback `http://127.0.0.1:11434` -> `finalize-local` with zero cloud fallback).

---

## 2. Restored Hard API Contribution Gate Architecture

### 2.1 Two Mandatory Sequential Gates
1. **Gate 1 — Application Approval (`require_approved_user`)**:
   - `pending`, `rejected`, `suspended` accounts receive `HTTP 403` (`ACCOUNT_NOT_APPROVED` / `ACCOUNT_SUSPENDED`) on all protected routes.
2. **Gate 2 — Shared Chat Contribution Entitlement (`require_chat_entitlement`)**:
   - **All approved accounts (`user`, `admin`, `super_admin` — zero admin exemption)** must hold at least **1 credential** in `public.api_key_metadata` satisfying **ALL** conditions (`app/core/dependencies.py::check_user_chat_entitlement`):
     1. `c.owner_id == current_user.id`
     2. `p.runtime_type == 'cloud_api'`
     3. `p.allows_community_sharing is True` *(enabled via migration `20260308000000` for free-tier capable `openrouter`, `gemini`, and `groq`; paid-only `openai` and `xai` remain excluded)*
     4. `p.is_enabled is True`
     5. `c.sharing_scope == 'community_shared'`
     6. `c.shared_consent is True`
     7. `c.validation_status == 'valid'`
     8. `c.status in ('active', 'rate_limited', 'quota_exhausted')` *(quota exhaustion pauses pool dispatch while preserving Chat entitlement)*
     9. `c.billing_tier == 'verified_free_only'` *(verified via OpenRouter `/api/v1/key` `data.is_free_tier == True`, or free-tier eligible probe on `gemini` / `groq`; excludes `paid` and `unknown`)*
     10. Freshness: `c.billing_verified_at >= now() - 30 days`
   - Any non-approved user or admin (`pending`/`rejected`/`suspended`) fails Gate 1 (`ACCOUNT_NOT_APPROVED` / `ACCOUNT_SUSPENDED`).

### 2.2 Authoritative Backend Enforcement Surface (No Bypasses)
`Depends(require_chat_entitlement)` is enforced across **every** conversational and direct-inference endpoint:
- `POST /api/v1/chat/conversations`
- `GET /api/v1/chat/conversations`
- `GET /api/v1/chat/conversations/{id}`
- `PATCH /api/v1/chat/conversations/{id}`
- `POST /api/v1/chat/conversations/{id}/archive`
- `POST /api/v1/chat/conversations/{id}/restore`
- `DELETE /api/v1/chat/conversations/{id}`
- `GET /api/v1/chat/conversations/{id}/messages`
- `POST /api/v1/chat/conversations/{id}/messages`
- `POST /api/v1/chat/messages/{message_id}/cancel`
- `POST /api/v1/chat/conversations/{id}/messages/{message_id}/cancel`
- `POST /api/v1/chat/conversations/{id}/messages/prepare-local`
- `POST /api/v1/chat/conversations/{id}/messages/finalize-local`
- `GET /api/v1/chat/messages/{message_id}/sources`
- `POST /api/v1/llm/chat/completions` *(closed M2/AI-Inference bypass)*
- `POST /api/v1/routing/preview` *(closed routing preview bypass)*

**Data Preservation Guarantee**: Locking Chat never deletes or alters `chat_conversations`, `chat_messages`, or `chat_message_sources`. Once a student contributes a qualifying credential, all prior conversations become accessible again.

**Independent Access Preserved**:
- `/api/v1/kb/*` read endpoints (`/subjects`, `/topics`, `/documents`, `/pages`, `/download-url`) require only `require_approved_user` so approved students retain Knowledge Base access while Chat is locked.
- `/api/v1/me/api-keys*` and `/api/v1/me/chat-entitlement` remain accessible via `require_not_suspended` / `require_approved_user` so students can satisfy the contribution requirement.

### 2.3 Frontend Hard Gate (`useChatAccessState` & `ChatAccessGate`)
- `useChatAccessState.ts`: Sets `requiresContribution = isApproved && !Boolean(entitlement?.eligible)` and sets `canBrowseHistory = isApproved && Boolean(entitlement?.eligible)` (i.e. `canAccessChat`). No conversation list or message fetch runs while locked.
- `ChatAccessGate.tsx`: Replaces the permissive non-blocking flow with a dedicated full-page **AI Chat Contribution Required** gate when `access.requiresContribution` is true, explaining why paid keys and local Ollama do not qualify, displaying diagnostic reason text, reassuring that saved chat history stays intact, and providing primary actions to **`Manage API Credentials` (`/credentials`)**, **`Check Contribution Status` (`access.refresh()`)**, and **`Browse Knowledge Base` (`/knowledge-base`)**.
