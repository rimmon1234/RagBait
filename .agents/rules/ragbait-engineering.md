# RagBait Engineering Rules

## 1. Minimal and Maintainable Code
- **YAGNI & Simplicity**: Build the simplest solution that fulfills requirements. Avoid speculative abstractions, unnecessary wrapper functions, redundant design patterns, or premature microservices.
- **Dependency Discipline**: Prefer actively maintained, pinned dependencies. Do not install multiple libraries that solve the same problem.
- **No Fake Functionality**: Never create non-functional buttons, mock endpoints pretending to be integrated, or fabricated data in production.

## 2. Security and Authorization Architecture
- **Server as the Source of Truth**: All authorization must be enforced on the backend and database. Never trust client-side redirects or client-supplied metadata.
- **Database RLS & Transaction-Local Claims**:
  - Direct PostgreSQL queries via asyncpg MUST execute within transactions using parameterized `set_config()` calls with `is_local = true`:
    - `SET LOCAL ROLE authenticated;`
    - `SELECT set_config('request.jwt.claims', $1, true);`
    - `SELECT set_config('request.jwt.claim.sub', $1, true);`
    - `SELECT set_config('request.jwt.claim.role', $1, true);`
  - Claims must never leak across pooled connections.
  - Column-level permissions: Table-level `UPDATE` on `public.profiles` is revoked from `authenticated`; only `display_name` is granted.
  - Database trigger `protect_profile_roles_and_status()` prevents unauthorized mutations of `role` and `approval_status`.
- **JWT Verification**: Validate Supabase JWT signatures, `iss`, `aud = 'authenticated'`, `sub`, and token expiration. Validate that the token's database role is `authenticated`.
- **Authoritative Email Verification**: Do not rely on default JWT claims. Use authoritative server-side checks (`email_confirmed_at IS NOT NULL`) before creating access requests.
- **Privilege Separation & Super-Admin Invariants**:
  - Self-promotion is strictly blocked.
  - Standard admins cannot alter `super_admin` records.
  - Demoting or removing the last super-admin is blocked using transaction-level advisory locks (`pg_advisory_xact_lock`).
  - Administrative mutations and audit logs must commit atomically in the same transaction.
  - Zero secrets or service-role keys in frontend browser bundles.
  - All `SECURITY DEFINER` functions must specify `SET search_path = public, pg_temp`.

## 3. Rate Limiting & Fail-Safe Behavior
- Use Redis for distributed rate limiting across API processes.
- Counter updates must be atomic.
- When Redis fails, readiness probes must fail and sensitive operations must fail closed (never silently bypass limits in production).
- Return HTTP 429 with `Retry-After`.

## 4. Presence and Session Tracking
- Use the verified JWT `session_id` claim as the stable session identifier in `user_presence`.
- Never store raw access tokens, refresh tokens, or passwords in presence or audit tables.
- Heartbeat default interval: 60s; active threshold: 120s.

## 5. Code Quality & Testing
- Strict TypeScript on the frontend; type annotations, Pydantic, and Ruff/Mypy on the backend.
- UTC timestamps and UUID primary keys for all entities.
- Write tests alongside each module. No tests may be falsified.
