# RagBait — Forensic Audit & Bug Remediation Changelog

**Date:** November 22, 2026  
**Commit Baseline:** `18c9aca` $\to$ Audit & Remediation  
**Status:** Verified & Closed  

---

## Remediated Issues Summary

| Issue ID | Category | Component | Description & Resolution | Status |
| :--- | :--- | :--- | :--- | :--- |
| **BUG-01** | Routing / UX | Frontend Auth (`login`, `contribution-required`, `pending`) | Fixed legacy M2 feature gate trap where approved students saw "Account Approved! Milestone M2 Feature Gate". Approved students now route immediately to `/knowledge-base`. | **RESOLVED** |
| **BUG-02** | State / UI | `Navbar.tsx` | Fixed auth state flash where "Sign in" and "Register" appeared despite an active session. Navbar now initializes fallback state from JWT session metadata immediately. Added "Manage / Add Subjects" admin direct link. | **RESOLVED** |
| **BUG-03** | Auth / Perf | `admin/service.py`, `access/service.py`, `login/page.tsx` | Fixed admin login latency and temporary approval state display. Admin roles are now inspected immediately on session detection and route straight to `/admin` without waiting for round trips. | **RESOLVED** |
| **BUG-04** | Feature Gap | `admin/knowledge-base/page.tsx` | Added direct document upload capability for administrators from the Knowledge Base control center, routing through the private storage and worker extraction lifecycle. | **RESOLVED** |
| **BUG-05** | UI / Architecture | `community-nodes/page.tsx`, `local-inference/page.tsx` | Removed obsolete "Community Network" tabs and routes. Overwrote `/community-nodes` to redirect to `/local-inference`. Enforced the 3 canonical inference modes (`private`, `auto`, `cloud_only`). | **RESOLVED** |
| **BUG-06** | Backend / Vault | `credentials/vault.py` | Fixed persistent runtime error `"Vault connection pool is not initialized"`. Added resilient lazy pool auto-initialization in `get_vault_session()`. | **RESOLVED** |
| **BUG-07** | Admin / KB | `admin/knowledge-base/subjects/page.tsx` | Fixed subject addition modal not opening from the overview link (`?action=new`). Component now reads URL query params and automatically triggers `SubjectFormModal`. | **RESOLVED** |
| **BUG-08** | Metrics / Admin | `admin/service.py`, `admin/presence/page.tsx`, `admin/users/page.tsx` | Fixed dashboard metric discrepancies: `pending_requests` now queries `access_requests WHERE status = 'pending'`; presence window corrected to 900s; contextual user actions refined. | **RESOLVED** |
| **BUG-09** | Testing / Database | `supabase/tests/m3_1_knowledge_base_test.sql` | Fixed 11 failing pgTAP subtests caused by test fixture collisions against pre-existing seed data. Scoped test assertions to isolated test fixture UUIDs. | **RESOLVED** |
| **BUG-10** | Code Quality | `apps/api/app`, `apps/web` | Cleaned up Mypy strict type diagnostics (0 errors across 96 backend files) and TypeScript compiler diagnostics (0 errors). | **RESOLVED** |
| **BUG-11** | Frontend / API | `credentials/page.tsx`, `credentials/schemas.py`, `types/index.ts` | Fixed credentials page crash caused by JSON schema key divergence: backend returned `items` while frontend expected `credentials`, making state `undefined` and throwing `Cannot read properties of undefined (reading 'length')`. Added dual-compatibility computed fields and resilient array unpacking. | **RESOLVED** |
| **BUG-12** | Routing / Gateway | `routing-client.ts`, `llm_gateway/schemas.py`, `routing/executor.py`, `admission/service.py` | Fixed "Error during routed inference: Request failed with status 422". Root cause: `idempotency_key` was required by `ChatCompletionRequest` but omitted by frontend client; root `temperature` was unmapped. Also fixed upstream model resolution where dynamically returned models (e.g. `nvidia/nemotron-...` via OpenRouter) threw 404 in reservation settlement by ensuring fallback to the registered model ID. | **RESOLVED** |

---

## Detailed File Modifications

### Backend (`apps/api`)

1. **`app/modules/credentials/vault.py`**
   - Added lazy initialization guard inside `get_vault_session()`: if `_vault_pool` is `None` or closed, `init_vault_pool()` is automatically awaited.
   - Raised `VaultDatabaseError` with a clear message if initialization fails, preventing unhandled `AttributeError: 'NoneType' object has no attribute 'acquire'`.

2. **`app/modules/access/service.py`**
   - Updated `resolve_effective_access_state()`: accounts with `role in ("admin", "super_admin")` immediately resolve to `effective_state = "admin"` (unless explicitly suspended), bypassing student admission gates.

3. **`app/modules/admin/service.py`**
   - Replaced `pending_requests` count query from `public.profiles WHERE approval_status = 'pending'` to `SELECT count(*) FROM public.access_requests WHERE status = 'pending'`, bringing `/admin` overview cards into exact synchronization with `/admin/requests`.

4. **`app/modules/llm_gateway/service.py`**
   - Added explicit validation and type narrowing for `provider_id` and `model_id` with `BadRequestError`, satisfying Mypy strict mode.

5. **`app/modules/routing/executor.py`**
   - Fixed module imports: `retrieve_decrypted_credential` from `app.modules.credentials.repository` and `format_sse_event` from `app.modules.llm_gateway.streaming`.

6. **`app/modules/community/ws.py`, `streams.py`, `repository.py`**
   - Removed duplicate exception handlers in WebSocket message loop.
   - Added typing annotations (`asyncio.Task[Any]`, boolean casts for Lua script returns, and safe string conversion for `active_session_id`).

7. **`app/modules/knowledge_base/repository.py`, `job_repository.py`, `admin_repository.py`, `worker.py`**
   - Removed obsolete `# type: ignore` comments that triggered Mypy unused-ignore warnings.

### Frontend (`apps/web`)

1. **`src/app/(auth)/login/page.tsx`**
   - Added instant client-side JWT role inspection: admins and super admins are immediately redirected to `/admin`.
   - Approved students are redirected directly to `/knowledge-base` instead of legacy access holding screens.

2. **`src/components/Navbar.tsx`**
   - Initialized fallback profile state immediately from `session.user` metadata on auth state changes, eliminating the flash of "Sign in" / "Register" buttons during active sessions.
   - Added "Manage / Add Subjects" link in the navigation menu for administrators.

3. **`src/app/(protected)/access/contribution-required/page.tsx`**
   - Replaced obsolete Milestone M2 feature gate UI with an automatic redirect to `/knowledge-base`.

4. **`src/app/(protected)/access/pending/page.tsx`**
   - Added mount check to automatically route approved students to `/knowledge-base` and admins to `/admin`.

5. **`src/app/admin/knowledge-base/page.tsx`**
   - Added "Upload Document" modal trigger for administrators, allowing direct administrative document ingestion into the Knowledge Base.

6. **`src/app/admin/knowledge-base/subjects/page.tsx`**
   - Added `action === "new"` query parameter inspection using `useSearchParams()`, auto-opening the `SubjectFormModal` on mount when accessed from "Add Subject" buttons.

7. **`src/app/admin/presence/page.tsx`**
   - Updated recent presence threshold from 120s to 900s (15 minutes), matching the visual "Recent (< 15m)" badge condition.

8. **`src/app/admin/users/page.tsx`**
   - Updated action buttons: "Review" link to `/admin/requests` for `pending` users, "Suspend" button for `approved` users, and "Reactivate" button for `suspended` users.

9. **`src/app/(protected)/community-nodes/page.tsx`**
   - Overwritten to immediately redirect to `/local-inference` with a clear deprecation banner.

10. **`src/app/(protected)/local-inference/page.tsx`**
    - Cleaned up obsolete `"community"` inference mode references and dead state variables.
    - Strictly enforced the 3 canonical inference modes: Private Local (`private`), Intelligent Router (`auto`), and Cloud Only (`cloud_only`).
    - Added dynamic cloud model fetching from `/api/v1/llm/models` with fallback list matching registered models in database.
    - Sanitized conversation history payload before streaming to prevent sending empty assistant placeholder turns or error banners as context.

11. **`tests/ContributionRequiredPage.test.tsx`, `tests/CommunityNodesPage.test.tsx`**
    - Updated unit tests to verify immediate redirection behavior and mock requirements.

12. **`apps/api/app/modules/llm_gateway/schemas.py`, `apps/web/src/lib/routing-client.ts`, `apps/web/src/lib/api.ts` (BUG-13)**
    - Reconciled chat completion validation: automatically trimmed and normalized message roles and contents, pruned empty/whitespace-only messages, and normalized empty string model parameters to `None`.
    - Formatted structured FastAPI `RequestValidationError` (`error.details`) into client error strings so exact field-level validation errors are visible instead of generic "Input validation failed".

13. **`apps/web/src/lib/routing-client.ts`, `apps/web/src/app/(protected)/local-inference/page.tsx` (BUG-14)**
    - Fixed empty assistant block during streaming: `streamChatCompletion` previously looked exclusively for OpenAI-specific `parsed.choices?.[0]?.delta?.content` and ignored RagBait's native `parsed.delta` payload, causing all streamed text tokens to be dropped and leaving an empty response bubble.
    - Added parsing for SSE `event: generation_error` and `error_code`, properly dispatching `callbacks.onError` so upstream failures render descriptive alerts in the chat bubble instead of silently closing the stream.
    - Updated `local-inference/page.tsx` onComplete handler to render a fallback label if zero tokens were produced.

14. **`apps/web/src/app/admin/layout.tsx`, `apps/api/app/modules/knowledge_base/service.py`, `apps/web/src/app/(protected)/local-inference/page.tsx` (BUG-15)**
    - Added proactive authorization guard in `AdminLayout` to redirect unauthenticated or non-admin users to `/login` or `/knowledge-base`, preventing unauthorized access attempts and broken 403 screens.
    - Increased knowledge base daily upload rate limit for administrators and faculty from 20 to 100 uploads/day to facilitate batch curriculum ingestion.
    - Added per-message model name tracking on `Message` interface to prevent historical message bubbles from retroactively changing displayed model names when the model selector is switched.

### Database Tests (`supabase`)

1. **`supabase/tests/m3_1_knowledge_base_test.sql`**
   - Generated dedicated isolated test UUIDs inside the transaction and scoped assertions to test fixture IDs (`id IN (...)`), resolving 11 subtest failures caused by pre-existing seed data collisions.

---

## Verification Test Results

```
========================================================================
Test Suite                             Results
========================================================================
Supabase pgTAP (10 files)              212 / 212 passed (100%)
Backend Pytest (apps/api)              315 passed, 4 skipped, 0 failed
Backend Ruff Lint & Format             146 files clean, 0 errors
Backend Mypy Strict Mode               96 files clean, 0 errors
Frontend Typecheck (tsc)               0 errors
Frontend Lint (next lint)              0 errors / 0 warnings
Frontend Vitest (21 files)             122 / 122 passed (100%)
Frontend Production Build (next build) 25 / 25 routes compiled successfully
Live LLM Routed Streaming (SSE)        Verified HTTP 200 via OpenRouter
========================================================================
```
