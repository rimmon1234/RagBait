# RagBait — M0/M1 Independent Verification & Security Audit Report

**Date**: 2026-11-18  
**Auditor**: Senior Application Security Engineer & Full-Stack Systems Auditor  
**Scope**: RagBait Foundation (M0) and Authentication / Access Control (M1)  
**Target Repositories & Services**: `apps/api/`, `apps/web/`, `supabase/`, `docs/`, PostgreSQL 17, Redis 7, Supabase GoTrue Auth v2.198.  
**Final Verdict**: **APPROVED FOR MILESTONE M2 (PRODUCTION-GRADE FOUNDATION VERIFIED)**

---

## 1. Executive Summary

An independent, rigorous security and architectural audit of **RagBait** was conducted against the live running stack and codebase. This verification did not rely on self-reported test summaries; all migrations, database triggers, function execution privileges, RLS policies, cryptographic token decoders, authoritative identity gates, concurrency locks, and distributed rate limiters were verified with real SQL queries and live HTTP transactions.

During the audit, **5 security and compatibility vulnerabilities** were discovered and completely remediated before sign-off. With these fixes in place, the system passed **100% of automated and runtime verification suites** (16 pgTAP database tests, 35 backend pytest tests including 9 live integration tests, 5 frontend Vitest tests, Next.js production build, Ruff, and Mypy).

---

## 2. Findings & Severity Categorization

| Severity | Issue ID | Component | Description | Resolution Status |
| :--- | :--- | :--- | :--- | :--- |
| **High** | SEC-01 | `service.py` | `resolve_effective_access_state` fell back to reading `user_metadata.email_verified` from the JWT if unconfirmed, violating the authoritative email verification requirement. | **FIXED** (Removed fallback; strictly requires server-side `auth.get_user()` with `email_confirmed_at`). |
| **High** | SEC-02 | `security.py` | PyJWT verification failed on modern Supabase Auth asymmetric tokens (`ES256`/`RS256`), throwing a PEM load exception when decoded against symmetric secret. | **FIXED** (Integrated `PyJWKClient` pointing to Supabase Auth's `/.well-known/jwks.json` with 1-hr caching; retained `HS256` secret fallback). |
| **Medium** | SEC-03 | `me.py` | `GET /api/v1/me` profile endpoint did not enforce `require_not_suspended`, allowing suspended users to read profile data. | **FIXED** (Added `require_not_suspended` dependency; only `/api/v1/me/access` reports suspended state). |
| **Medium** | DB-01 | `schema.sql` | Missing `ON DELETE SET NULL` on `access_requests.reviewed_by` and `audit_logs.actor_id` blocked administrator deletion due to foreign key violations. | **FIXED** (Added `ON DELETE SET NULL` on both columns; applied via migration reset). |
| **Low** | INFRA-01 | `redis.py` | Deprecation warning on `_redis_client.close()`. | **FIXED** (Upgraded to `await _redis_client.aclose()`). |

---

## 3. Detailed Verification Results

### 3.1 Database Security Audit
- **Row Level Security (RLS)**: Verified active (`relrowsecurity = true`) on all four public tables:
  - `public.profiles`
  - `public.access_requests`
  - `public.user_presence`
  - `public.audit_logs`
- **Transaction-Local Claims Isolation**:
  - `get_user_db_session()` issues parameterized `set_config('request.jwt.claims', $1, true)` inside an explicit transaction with `SET LOCAL ROLE authenticated;`.
  - Live testing proved that upon transaction commit/rollback, claims revert to `NULL`/empty and cannot leak across pooled connections.
- **Column-Level Permissions & Trigger Context**:
  - `REVOKE UPDATE ON public.profiles FROM authenticated;` and `GRANT UPDATE (display_name) ON public.profiles TO authenticated;` were validated in SQL.
  - Authenticated queries attempting to alter `role` or `approval_status` are blocked by PostgreSQL with error `42501 (insufficient_privilege)`.
  - `public.protect_profile_roles_and_status()` trigger was verified as `SECURITY INVOKER` (`prosecdef = false`), correctly evaluating `current_user` and rejecting non-system callers.
- **Schema & Function Privilege Hygiene**:
  - `app_private.is_admin` is `SECURITY DEFINER` with `SET search_path = ''`. Execution is revoked from `PUBLIC` and `anon`.
  - `public.handle_new_user` is `SECURITY DEFINER` with `SET search_path = ''`. Direct execution is revoked from `PUBLIC`, `anon`, and `authenticated`.
  - `public.audit_logs` has all `INSERT`, `UPDATE`, and `DELETE` privileges revoked from `authenticated`. Only service-role/admin backend operations can write audit trails.

### 3.2 Authentication & Authorization Audit
- **Authoritative Email Verification Gate**:
  - Unverified users querying `GET /api/v1/me/access` receive `effective_state = "unverified"`, and no entry is created in `public.access_requests`.
  - Once `email_confirmed_at` is authoritatively confirmed via Supabase Auth `client.auth.get_user(jwt)`, calling `/api/v1/me/access` transitions the user to `pending` and idempotently creates a single access request (`ON CONFLICT (user_id) WHERE status = 'pending' DO NOTHING`).
  - Repeated calls produce zero duplicate requests.
- **Cryptographic Token Verification**:
  - Rejects tokens with invalid signatures (`401 INVALID_TOKEN`).
  - Rejects expired tokens (`401 TOKEN_EXPIRED`).
  - Rejects non-authenticated database roles (`401 UNAUTHORIZED_TOKEN_ROLE`).
  - Rejects malformed subject claims (`401 INVALID_SUBJECT`).
  - Supports modern GoTrue asymmetric `ES256` JWKS validation alongside symmetric `HS256` test keys.
- **Access State Transitions**:
  - Unverified $\to$ Pending $\to$ Approved (`contribution_required` for users, `admin` for admins/super-admins) $\to$ Suspended $\to$ Rejected.
  - Suspended accounts receive `403 ACCOUNT_SUSPENDED` across all profile, heartbeat, and mutating endpoints.

### 3.3 Administrative Security & Concurrency Audit
- **Self-Promotion & Self-Suspension**:
  - `PATCH /api/v1/admin/users/{user_id}/role` on self raises `400 SELF_PROMOTION_FORBIDDEN`.
  - `PATCH /api/v1/admin/users/{user_id}/status` on self raises `400 SELF_SUSPENSION_FORBIDDEN`.
- **Role Hierarchy & Invariants**:
  - Standard administrators attempting to modify a super-administrator's role or status receive `403 Forbidden`.
  - Only super-administrators can promote users to `super_admin`.
- **Advisory Locking & Last Super-Admin Protection**:
  - Administrative mutations acquire transaction-scoped advisory lock `SELECT pg_advisory_xact_lock(hashtext('ragbait_super_admin_role_lock'))` and lock target/actor rows with `FOR UPDATE`.
  - Demoting or suspending the final remaining super-administrator raises `409 ConflictError ("Cannot demote the last remaining super-administrator")`.
- **Atomic Audit Logging**:
  - Access request approval/rejection, role updates, and suspension mutations insert audit log records within the same database transaction.

### 3.4 Infrastructure & Resilience Audit
- **Distributed Rate Limiting**:
  - Enforced via atomic Redis Lua script.
  - Exceeding request thresholds returns `429 Too Many Requests` with a dynamic `Retry-After` header.
  - In `production` mode, Redis downtime triggers a fail-secure `503 Service Unavailable` response.
- **Health Probes**:
  - `GET /health/live`: Returns `200 {"status": "alive"}`.
  - `GET /health/ready`: Evaluates live database connection pool and Redis connection; returns `200 {"status": "ready"}` when healthy or `503 {"status": "degraded"}` when any dependency drops.

---

## 4. Automated Test Suite Execution Summary

```
-----------------------------------------------------------------------------------------
Test Target       Command                                    Exit Code  Outcome
-----------------------------------------------------------------------------------------
Database pgTAP    npx supabase test db                       0          16/16 Passed
Backend Pytest    cd apps/api; uv run pytest                 0          35/35 Passed
Backend Ruff      cd apps/api; uv run ruff check .           0          All checks passed
Backend Format    cd apps/api; uv run ruff format --check .  0          40 files formatted
Backend Mypy      cd apps/api; uv run mypy app               0          0 errors (28 files)
Frontend Lint     cd apps/web; pnpm lint                     0          0 errors/warnings
Frontend Types    cd apps/web; pnpm typecheck                0          0 errors
Frontend Vitest   cd apps/web; pnpm test                     0          5/5 Passed
Frontend Build    cd apps/web; pnpm build                    0          15 routes compiled
-----------------------------------------------------------------------------------------
```

---

## 5. Milestone M2 Readiness & Recommendation

RagBait's foundation (M0) and authentication/authorization architecture (M1) are structurally complete, secure, minimal, and fully verified. 

**Recommendation**: **Proceed to Milestone M2 (Secure API Credential Management & Shared LLM Gateway)**.
