# RagBait API Specification

Base URL: `/api/v1`
Authentication: Bearer JWT in `Authorization` header (`Authorization: Bearer <supabase_access_token>`).

All API errors return standard envelope:
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable description",
    "details": null
  }
}
```

---

## 1. Identity & Profile Endpoints

### 1.1 `GET /api/v1/me`
Returns current authenticated user profile.
- **Authorization**: Valid authenticated session.
- **Response `200 OK`**:
```json
{
  "id": "e2f7b11d-3b7c-4a34-b295-812e96d24a91",
  "email": "student@example.edu",
  "display_name": "Jane Doe",
  "role": "user",
  "approval_status": "approved",
  "created_at": "2026-11-18T10:00:00Z",
  "updated_at": "2026-11-18T10:00:00Z"
}
```

### 1.2 `GET /api/v1/me/access`
Evaluates user's effective access state and idempotently creates a pending access request if the user is email-verified and has no existing request.
- **Authorization**: Valid authenticated session.
- **Response `200 OK`**:
```json
{
  "effective_state": "pending",
  "email_verified": true,
  "role": "user",
  "approval_status": "pending",
  "has_pending_request": true
}
```
*Possible `effective_state` values*:
- `unverified`: Email is not confirmed.
- `pending`: Access request submitted, awaiting admin review.
- `rejected`: Request rejected by admin.
- `suspended`: Account suspended by admin.
- `contribution_required`: Approved regular student (holding state for M2 API key contribution).
- `admin`: Approved administrator or super-admin.

### 1.3 `PATCH /api/v1/me/profile`
Updates current user's mutable profile fields.
- **Authorization**: Valid authenticated session.
- **Request Body**:
```json
{
  "display_name": "Jane Smith"
}
```
*Note*: Extra fields (e.g. `role`, `approval_status`) trigger HTTP 422 Unprocessable Entity.
- **Response `200 OK`**: Updated profile object.

### 1.4 `POST /api/v1/me/heartbeat`
Lightweight presence pulse.
- **Authorization**: Valid authenticated session with verified `session_id` JWT claim.
- **Request Body**: Optional `{ "client_info": { "device": "desktop" } }`
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "last_seen_at": "2026-11-18T10:05:00Z",
  "expires_at": "2026-11-18T10:07:00Z"
}
```

### 1.5 `GET /api/v1/me/chat-entitlement`
Returns the user's authoritative AI Chat contribution entitlement status.
- **Authorization**: Valid authenticated session.
- **Mandatory Policy Rule**: Every approved user must contribute at least one validated, provider-approved, free-tier cloud API credential to the RagBait shared pool before accessing AI Chat. This requirement applies even when using private local Ollama. Local models and paid API credentials do not count.
- **Response `200 OK`**:
```json
{
  "eligible": true,
  "reason_code": "QUALIFYING_CONTRIBUTION_ACTIVE",
  "eligible_credential_count": 1
}
```
*Possible `reason_code` values*:
- `QUALIFYING_CONTRIBUTION_ACTIVE`: Entitlement active ($\ge 1$ qualifying shared free-tier credential; temporary quota exhaustion does not revoke entitlement).
- `NO_CREDENTIAL_CONTRIBUTED`: No qualifying free-tier API key contributed.
- `VALIDATION_REQUIRED`: Contributed key requires validation probe.
- `SHARING_CONSENT_REQUIRED`: Community sharing consent required.
- `CREDENTIAL_INACTIVE`: Key is disabled in Key Management.
- `PROVIDER_NOT_ELIGIBLE_FOR_SHARING`: Provider not approved for free-tier community sharing.
- `PAID_CREDENTIAL_INELIGIBLE`: Contributed key is classified as paid or billable and cannot enter the shared pool.
- `BILLING_VERIFICATION_REQUIRED`: Contributed key cannot be authoritatively verified as free-tier only.
- `BILLING_VERIFICATION_STALE`: Contributed key billing verification is older than 30 days and requires re-validation.

---

## 2. Administration Endpoints

Requires role `admin` or `super_admin` AND `approval_status == 'approved'`.

### 2.1 `GET /api/v1/admin/overview`
High-level system counts and active counts.
- **Response `200 OK`**:
```json
{
  "total_users": 45,
  "pending_requests": 3,
  "approved_users": 40,
  "suspended_users": 2,
  "rejected_users": 0,
  "active_users_now": 12,
  "active_users_recent": 24
}
```

### 2.2 `GET /api/v1/admin/users`
Paginated user list with search and filters.
- **Query Params**:
  - `page` (default 1), `page_size` (default 20, max 100)
  - `search` (matches email or display_name)
  - `role` (`user`, `admin`, `super_admin`)
  - `approval_status` (`pending`, `approved`, `rejected`, `suspended`)
- **Response `200 OK`**:
```json
{
  "items": [
    {
      "id": "e2f7b11d-3b7c-4a34-b295-812e96d24a91",
      "email": "student@example.edu",
      "display_name": "Jane Doe",
      "role": "user",
      "approval_status": "approved",
      "created_at": "2026-11-18T10:00:00Z",
      "updated_at": "2026-11-18T10:00:00Z"
    }
  ],
  "total": 45,
  "page": 1,
  "page_size": 20,
  "total_pages": 3
}
```

### 2.3 `GET /api/v1/admin/access-requests`
Lists pending or all access requests.
- **Query Params**: `status` (default `pending`), `page`, `page_size`.
- **Response `200 OK`**: Paginated list of access requests with user metadata.

### 2.4 `POST /api/v1/admin/access-requests/{request_id}/decision`
Reviews an access request.
- **Request Body**:
```json
{
  "decision": "approved",
  "reason": "Verified college student"
}
```
*Note*: Atomically updates `access_requests.status` and `profiles.approval_status` and writes an audit log.

### 2.5 `PATCH /api/v1/admin/users/{user_id}/role`
Updates a user's role.
- **Request Body**: `{ "role": "admin" }`
- **Rules Enforced**:
  - Self-promotion blocked (HTTP 400).
  - Admin cannot change `super_admin` role (HTTP 403).
  - Cannot demote/remove the last remaining `super_admin` (HTTP 409).
  - Serialized via `pg_advisory_xact_lock`.

### 2.6 `PATCH /api/v1/admin/users/{user_id}/status`
Suspends or reactivates a user.
- **Request Body**: `{ "approval_status": "suspended", "reason": "Academic violation" }`
- **Rules Enforced**:
  - Cannot suspend self.
  - Cannot suspend the last `super_admin`.

### 2.7 `GET /api/v1/admin/presence`
Active and recently seen users.
- **Query Params**: `threshold_seconds` (default 120).
- **Response `200 OK`**: Active sessions grouped by user.

### 2.8 `GET /api/v1/admin/audit-logs`
Paginated audit log records.
- **Response `200 OK`**: Paginated audit events.

---

## 3. Health Endpoints

- `GET /health/live`: Returns `200 OK` if the process is running.
- `GET /health/ready`: Verifies database connection pool and Redis connection. Returns `503 Service Unavailable` if critical dependencies fail.

---

## 4. Community Local Inference Endpoints (Discontinued)

> **Privacy Policy on Local Inference:**
> *RagBait allows students to use their own locally installed Ollama models privately. These models and their computing resources are never shared with other students. Shared inference pooling is restricted to explicitly consented, provider-eligible cloud API credentials.*

All community node mutation, pairing, job submission, status, and streaming endpoints return **HTTP 410 Gone**:
- `POST /api/v1/community/nodes/pair-code` $\to$ `410 Gone`
- `POST /api/v1/community/nodes/pair` $\to$ `410 Gone`
- `PATCH /api/v1/community/nodes/{node_id}` $\to$ `410 Gone`
- `POST /api/v1/community/nodes/{node_id}/pause` $\to$ `410 Gone`
- `POST /api/v1/community/nodes/{node_id}/resume` $\to$ `410 Gone`
- `DELETE /api/v1/community/nodes/{node_id}` $\to$ `410 Gone`
- `PUT /api/v1/community/nodes/{node_id}/models` $\to$ `410 Gone`
- `POST /api/v1/community/jobs/submit` $\to$ `410 Gone`
- `GET /api/v1/community/jobs/{job_id}` $\to$ `410 Gone`
- `POST /api/v1/community/jobs/{job_id}/cancel` $\to$ `410 Gone`
- `GET /api/v1/community/jobs/{job_id}/stream` $\to$ `410 Gone`
- `WS /api/v1/community/ws` $\to$ Disconnects with code `1008 Policy Violation` (`COMMUNITY_LOCAL_INFERENCE_DISCONTINUED`)
- `GET /api/v1/community/nodes` $\to$ `200 OK` (returns `[]`)
- `GET /api/v1/community/models` $\to$ `200 OK` (returns `[]`)

---

## 5. LLM Gateway & Intelligent Routing Endpoints

### 5.1 `POST /api/v1/llm/chat/completions`
Unified OpenAI-compatible chat completion endpoint with deterministic intelligent routing, fair-use admission control, and quota tracking.
- **Authorization**: `require_chat_entitled_user` dependency (User must be `approved` AND have active chat contribution entitlement). Returns `403 Forbidden` (`CONTRIBUTION_REQUIRED`) if not entitled.
- **Request Body**:
```json
{
  "messages": [
    { "role": "system", "content": "You are a helpful study assistant." },
    { "role": "user", "content": "Explain binary search." }
  ],
  "model": "gemini-3.8-flash",
  "routing_mode": "auto",
  "credential_scope": "eligible_pool",
  "temperature": 0.7,
  "stream": true
}
```
- **Execution Scopes (`credential_scope`)**:
  - `eligible_pool` (default): Candidate discovery evaluates the user's personal keys and all provider-eligible shared free-tier credentials from the community pool. Shared credentials are strictly constrained to free-tier eligible models with zero cost (`SHARED_POOL_FREE_TIER_ONLY`).
  - `owner_only`: Candidate discovery strictly excludes any credentials contributed by other users. Requests will fail if no valid personal credential is configured.
- **Response Headers**:
  - `x-ragbait-routing-mode`: `auto`
  - `x-ragbait-selected-source`: Source identifier
  - `x-ragbait-selected-model`: Model identifier
  - `x-ragbait-decision-reason`: Routing decision explanation

### 5.2 `POST /api/v1/routing/preview`
Simulates candidate evaluation and returns the deterministic routing decision without executing inference.
- **Authorization**: `require_chat_entitled_user` dependency.
- **Request Body**: Accepts `messages`, `routing_mode`, `credential_scope`, `model`, `provider_id`.
- **Response `200 OK`**:
```json
{
  "selected_source": "cloud:gemini",
  "selected_model": "gemini-3.8-flash",
  "routing_mode": "auto",
  "reason_code": "MATCH_FOUND",
  "decision_reason": "Selected Gemini via eligible community pool",
  "fallback_chain": ["cloud:groq"]
}
```

