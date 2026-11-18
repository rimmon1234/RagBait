# RagBait Security & Threat Model

## 1. Authentication Security

### 1.1 JWT Verification
- **Cryptographic Signature**: Validated against Supabase's JWKS or configured `SUPABASE_JWT_SECRET` using standard algorithms (HS256/RS256/ES256).
- **Mandatory Claims**:
  - `iss`: Validates expected issuer (`SUPABASE_URL + "/auth/v1"`).
  - `aud`: Must equal `"authenticated"`.
  - `sub`: Must be a valid UUID.
  - `exp`: Token must not be expired.
  - `role`: Must strictly equal `"authenticated"`.
- **Authoritative Email Verification**:
  - The client token claims are not trusted as the sole truth for email verification. The server verifies `email_confirmed_at` directly from the database or via the Supabase Admin API before enabling access requests.

### 1.2 Session Identifier Hygiene
- The stable `session_id` claim from Supabase Auth is stored in `user_presence`.
- **Zero Token Storage**: Raw JWTs, refresh tokens, and passwords are never persisted to presence or audit tables.

---

## 2. Database & RLS Security

### 2.1 Connection Pooling & RLS Claims
Direct connections (via asyncpg) simulate Supabase PostgREST's environment by running within explicit transactions:
```sql
BEGIN;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claims', $1, true);
SELECT set_config('request.jwt.claim.sub', $2, true);
SELECT set_config('request.jwt.claim.role', $3, true);
-- user-scoped queries execute here --
COMMIT;
```
Because `is_local = true` is set, all role and claims settings are isolated strictly to that transaction. When returned to the pool, the connection is clean.

### 2.2 Column-Level Defense-in-Depth
- `REVOKE UPDATE ON public.profiles FROM authenticated;`
- `GRANT UPDATE (display_name) ON public.profiles TO authenticated;`
- Trigger `protect_profile_roles_and_status()` halts execution if any unauthorized session attempts to change `role` or `approval_status`.
- FastAPI Pydantic schemas forbid unknown attributes (`extra = "forbid"`).

### 2.3 Search Path Injection & Function Security Classification
Database functions adhere to strict, individualized security classifications:

1. **`app_private.is_admin(user_id uuid)`** (`SECURITY DEFINER`):
   - Located in the non-exposed private schema `app_private` (`REVOKE ALL ON SCHEMA app_private FROM PUBLIC, anon;`).
   - Uses `SET search_path = ''` with fully schema-qualified object references.
   - Execution is restricted to `authenticated`, `service_role`, and `postgres` to prevent RLS recursion while denying access to anonymous users.
2. **`public.protect_profile_roles_and_status()`** (`SECURITY INVOKER`):
   - Evaluates the effective caller context (`pg_catalog.current_user` and `current_setting('request.jwt.claim.role', true)`).
   - Prevents `authenticated` users from mutating `role` or `approval_status`, while allowing `postgres` and `service_role` to apply validated changes.
3. **`public.handle_new_user()`** (`SECURITY DEFINER`):
   - Uses `SET search_path = ''`.
   - Direct execution revoked from `PUBLIC`, `anon`, and `authenticated`.
4. **`public.set_updated_at()`** (`SECURITY INVOKER`):
   - Pure utility refresher running in caller context with `SET search_path = ''`.

---

## 3. Administrative Protection & Concurrency Invariants

### 3.1 Super-Admin Protection
1. **Advisory Locking**: All role updates acquire a transaction-level advisory lock:
   ```sql
   SELECT pg_advisory_xact_lock(hashtext('ragbait_super_admin_role_lock'));
   ```
2. **Last Super-Admin Rule**: The count of active super-admins is verified under the advisory lock. If count <= 1, demoting, suspending, or deleting that user raises an error.
3. **No Self-Promotion**: Users and administrators cannot modify their own role.
4. **Standard Admin Guard**: Standard admins cannot modify or demote a `super_admin`.

### 3.2 Audit Trail Immutability
- Client roles have no `INSERT`, `UPDATE`, or `DELETE` grants on `public.audit_logs`.
- All administrative mutations generate audit logs atomically in the same database transaction.

---

## 4. Rate Limiting & Fail-Secure Policy

- Distributed Redis rate limiting ensures protection across multiple API worker processes.
- **Fail-Secure**: If Redis is unreachable in production:
  - The `/health/ready` check transitions to unhealthy.
  - Sensitive operations fail closed (rejecting requests) rather than silently disabling limits.
- Sensitive credentials, API keys, and authorization headers are scrubbed from structured log outputs.
