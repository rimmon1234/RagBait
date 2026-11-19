# RagBait M2 — API Specification & Endpoint Contracts

## 1. Authentication & Security Headers

All HTTP endpoints require an authenticated user session:
- Header: `Authorization: Bearer <Supabase_JWT>`
- Database role claim must be `authenticated`.
- Accounts with `approval_status = 'suspended'` are strictly rejected with HTTP `403 Forbidden`.

The WebSocket endpoint for Node Agents (`WSS /api/v1/nodes/stream`) uses **Ed25519 asymmetric public-key challenge-response**:
- Connection Initiation: `X-Node-ID: <node_uuid>`
- Authentication: Replay-protected Ed25519 signature over an ephemeral 60-second server nonce and session context. Private keys never touch the network or server.

---

## 2. API Key Credential Management

All credential endpoints derive ownership strictly from the authenticated principal (`current_user.id`). Attempts to query or mutate another user's credential return a uniform HTTP `404 Not Found` without distinction.

### 2.1 Submit New Credential
- **Route**: `POST /api/v1/me/api-keys`
- **Rate Limit**: 5 requests / minute
- **Request Body**:
  ```json
  {
    "provider_id": "groq",
    "nickname": "StudyGroup Free Groq",
    "api_key": "gsk_exampleSecretKey1234567890",
    "account_scope_id": "org_groq_university_lab",
    "sharing_scope": "community_shared",
    "shared_consent": true,
    "daily_request_limit": 500,
    "monthly_request_limit": 15000,
    "daily_spending_limit_cents": null,
    "monthly_spending_limit_cents": null
  }
  ```
- **Validation Rules**:
  - `provider_id`: Must exist in `public.llm_providers` and be marked `is_enabled = true`.
  - `api_key`: Must not be empty. Stripped of whitespace before encryption.
  - **Duplicate Prevention**:
    - Computes keyed HMAC-SHA256 fingerprint (`key_fingerprint`) using `VAULT_PEPPER_SECRET`.
    - If `(provider_id, key_fingerprint)` already exists in `public.api_key_metadata`, returns `409 Conflict`.
  - **Community Sharing Eligibility**:
    - `sharing_scope = 'community_shared'` requires `public.llm_providers.allows_community_sharing = true` (eligible: Groq, OpenRouter, Cerebras).
    - Requires `shared_consent = true`. Missing consent returns `422 Unprocessable Entity`.
    - Providers without community sharing approval (Gemini, OpenAI, xAI) return `409 Conflict`.
- **Response** (`201 Created`):
  ```json
  {
    "id": "7a3e9c4b-3d2f-41e8-a5b8-d4c3b2a10987",
    "provider_id": "groq",
    "nickname": "StudyGroup Free Groq",
    "masked_key": "gsk_...7890",
    "status": "disabled",
    "sharing_scope": "community_shared",
    "shared_consent": true,
    "sharing_consent_timestamp": "2026-02-01T12:00:00Z",
    "consent_version": "v1",
    "validation_status": "unvalidated",
    "last_validated_at": null,
    "validation_error_code": null,
    "account_scope_id": "org_groq_university_lab",
    "daily_request_limit": 500,
    "monthly_request_limit": 15000,
    "daily_spending_limit_cents": null,
    "monthly_spending_limit_cents": null,
    "created_at": "2026-02-01T12:00:00Z",
    "updated_at": "2026-02-01T12:00:00Z"
  }
  ```
- **Security Invariant**: Credentials always start in `status: "disabled"` and `validation_status: "unvalidated"`. Plaintext keys are never serialized or logged.

### 2.2 List Own Credentials
- **Route**: `GET /api/v1/me/api-keys`
- **Rate Limit**: 60 requests / minute
- **Response** (`200 OK`):
  ```json
  {
    "credentials": [
      {
        "id": "7a3e9c4b-3d2f-41e8-a5b8-d4c3b2a10987",
        "provider_id": "groq",
        "nickname": "StudyGroup Free Groq",
        "masked_key": "gsk_...7890",
        "status": "disabled",
        "sharing_scope": "community_shared",
        "shared_consent": true,
        "sharing_consent_timestamp": "2026-02-01T12:00:00Z",
        "consent_version": "v1",
        "validation_status": "unvalidated",
        "last_validated_at": null,
        "validation_error_code": null,
        "account_scope_id": "org_groq_university_lab",
        "daily_request_limit": 500,
        "monthly_request_limit": 15000,
        "daily_spending_limit_cents": null,
        "monthly_spending_limit_cents": null,
        "created_at": "2026-02-01T12:00:00Z",
        "updated_at": "2026-02-01T12:00:00Z"
      }
    ],
    "total": 1
  }
  ```

### 2.3 Get Specific Credential Details
- **Route**: `GET /api/v1/me/api-keys/{id}`
- **Rate Limit**: 60 requests / minute
- **Response** (`200 OK`): Returns single `CredentialResponse` matching the authenticated owner. Returns `404 Not Found` if nonexistent or owned by another user.

### 2.4 Modify Credential Settings
- **Route**: `PATCH /api/v1/me/api-keys/{id}`
- **Rate Limit**: 20 requests / minute
- **Request Body**:
  ```json
  {
    "nickname": "Updated Nickname",
    "account_scope_id": "org-revised",
    "sharing_scope": "owner_only",
    "shared_consent": false,
    "daily_request_limit": 200,
    "monthly_request_limit": 5000,
    "daily_spending_limit_cents": 100,
    "monthly_spending_limit_cents": 2500
  }
  ```
- **Response** (`200 OK`): Updated `CredentialResponse`.

### 2.5 Run Non-Generative Validation Probe
- **Route**: `POST /api/v1/me/api-keys/{id}/validate`
- **Rate Limit**: 5 requests / minute
- **Behavior**: Decrypts the secret in memory via the dedicated vault session and fires a lightweight, non-generative metadata/model probe (consuming 0 inference tokens):
  - **Gemini**: `GET https://generativelanguage.googleapis.com/v1beta/models`
  - **Groq**: `GET https://api.groq.com/openai/v1/models`
  - **OpenRouter**: `GET https://openrouter.ai/api/v1/auth/key`
  - **Cerebras**: `GET https://api.cerebras.ai/v1/models`
  - **OpenAI**: `GET https://api.openai.com/v1/models`
  - **xAI**: `GET https://api.x.ai/v1/models`
- **Response** (`200 OK`):
  ```json
  {
    "is_valid": true,
    "outcome": "valid",
    "http_status": 200,
    "error_code": null,
    "diagnostic_message": "Authentication successful (0 inference tokens consumed)",
    "probed_at": "2026-02-01T12:05:00Z"
  }
  ```
- Automatically updates `public.api_key_metadata.validation_status` to `"valid"` or diagnostic error code (`"invalid"`, `"rate_limited"`, etc.).

### 2.6 Enable Credential
- **Route**: `POST /api/v1/me/api-keys/{id}/enable`
- **Rate Limit**: 20 requests / minute
- **Behavior**: Transitions `status` from `disabled` to `active`.
- **Precondition**: `validation_status` MUST be `"valid"`. Unvalidated or invalid credentials return `422 Unprocessable Entity` ("Credential must pass an active validation probe before being enabled").
- **Response** (`200 OK`): Updated `CredentialResponse` with `status: "active"`.

### 2.7 Disable Credential
- **Route**: `POST /api/v1/me/api-keys/{id}/disable`
- **Rate Limit**: 20 requests / minute
- **Behavior**: Transitions `status` to `disabled`. The credential is immediately bypassed during provider dispatch routing.
- **Response** (`200 OK`): Updated `CredentialResponse` with `status: "disabled"`.

### 2.8 Delete Credential (Purge Secret)
- **Route**: `DELETE /api/v1/me/api-keys/{id}`
- **Rate Limit**: 10 requests / minute
- **Behavior**: Permanently deletes the metadata record from `public.api_key_metadata` and cascades deletion of `private.api_key_secrets` (destroying ciphertext, wrapped DEK, and nonces). Records an audit event in `public.audit_logs`.
- **Response** (`204 No Content`).

---

## 3. Student Local Inference Nodes & WebSocket Protocol

### 3.1 Register Local Node with Ed25519 Public Key
- **Route**: `POST /api/v1/me/nodes`
- **Request Body**:
  ```json
  {
    "node_name": "Alice-RTX4090-Desktop",
    "ed25519_public_key": "MCowBQYDK2VwAyEAX5hT8q1...base64_public_key",
    "device_specs": {
      "gpu": "NVIDIA GeForce RTX 4090 24GB",
      "ram_gb": 64,
      "cpu_cores": 16
    }
  }
  ```
- **Response** (`201 Created`):
  ```json
  {
    "node_id": "9b1c3d2e-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
    "node_name": "Alice-RTX4090-Desktop",
    "status": "offline",
    "ed25519_public_key": "MCowBQYDK2VwAyEAX5hT8q1...base64_public_key",
    "created_at": "2026-02-01T12:00:00Z"
  }
  ```
  *(The private key never leaves the student device).*

### 3.2 Update Node Model Configuration
- **Route**: `PUT /api/v1/me/nodes/{node_id}/models`
- **Request Body**:
  ```json
  {
    "model_tag": "qwen2.5:7b",
    "context_length": 32768,
    "is_shared": true,
    "max_model_concurrency": 1
  }
  ```
- **Response** (`200 OK`): Model configuration updated. Toggles between Mode A (Private) and Mode B (Shared).

### 3.3 Node Agent WebSocket Protocol (`WSS /api/v1/nodes/stream`)

#### Step 1: Handshake & Ed25519 Challenge-Response
1. **Client Connects**:
   - URL: `WSS /api/v1/nodes/stream`
   - Header: `X-Node-ID: <node_uuid>`
2. **Gateway Issues Challenge**:
   - Gateway generates 32-byte cryptographically random hex nonce `server_nonce`.
   - Stores in Redis: `SETEX ragbait:node:challenge:{node_id} 60 <server_nonce>`.
   - Sends WebSocket frame:
     ```json
     {
       "type": "CHALLENGE",
       "server_nonce": "9f83ab24e1074e2ba1b9d4c8201a4e5f76b8c9d012e3f4a5b6c7d8e9f0123456",
       "expires_in": 60,
       "session_context": {
         "node_id": "9b1c3d2e-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
         "timestamp": 1771426800
       }
     }
     ```
3. **Agent Responds with Ed25519 Signature**:
   - Agent signs: `payload = b"ragbait-node-auth-v1:" + node_id.encode() + b":" + server_nonce.encode() + b":" + str(timestamp).encode()`.
   - Sends WebSocket frame:
     ```json
     {
       "type": "RESPONSE",
       "signature": "3b7c8a9d0e1f2a3b4c5d6e7f8a9b0c1d...base64_ed25519_sig",
       "timestamp": 1771426800
     }
     ```
4. **Gateway Validates & Issues Ephemeral Session Token**:
   - Gateway reads and immediately consumes `ragbait:node:challenge:{node_id}` from Redis via atomic `GETDEL`.
   - Validates signature against `ed25519_public_key` in `public.inference_nodes`.
   - Verifies `is_revoked = false`.
   - Issues ephemeral 15-minute JWT session token `session_token`.
   - Sends frame:
     ```json
     {
       "type": "AUTH_OK",
       "session_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
       "expires_at": 1771427700,
       "heartbeat_interval_seconds": 15
     }
     ```
   - *If signature invalid, nonce expired/replayed, or node revoked*: Gateway sends `AUTH_FAILED` and closes connection with code `1008` (Policy Violation).

#### Step 2: Heartbeat Exchange (every 15s)
- **Agent sends**:
  ```json
  {"type": "PING", "ram_available_mb": 16384, "gpu_utilization_pct": 12}
  ```
- **Gateway replies**:
  ```json
  {"type": "PONG", "timestamp": 1771424715}
  ```

#### Step 3: Reliable Job Dispatch Frame Contract
When Worker A reads a job from `ragbait:stream:jobs:{node_id}`, it forwards:
```json
{
  "type": "JOB_DISPATCH",
  "job_id": "job_01hxyz89abc",
  "job_token": "eyJhbGciOiJFZERTQSI...signed_gateway_job_token",
  "expires_at": 1771424745,
  "model": "qwen2.5:7b",
  "messages": [
    {"role": "system", "content": "You are a concise study assistant."},
    {"role": "user", "content": "Explain binary search trees."}
  ],
  "temperature": 0.7,
  "max_tokens": 1000,
  "payload_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

#### Step 4: Explicit Confirmation before Redis `XACK`
Upon validating the gateway's `job_token` and checking local idempotency, the Node Agent sends:
```json
{
  "type": "AGENT_ACCEPTED",
  "job_id": "job_01hxyz89abc",
  "accepted_at": 1771424710
}
```
*(Worker A updates DB status to `agent_accepted` and **now executes `XACK` on the Redis Stream**).*

#### Step 5: Sequence-Numbered Streaming & Lease Renewal
- **Job Progress Frame** (sent at least every 10s during inference):
  ```json
  {
    "type": "JOB_PROGRESS",
    "job_id": "job_01hxyz89abc",
    "tokens_generated": 65
  }
  ```
  *(Gateway extends `ragbait:node:job:lease:{job_id}` by 30s in Redis, capped at 300s total duration).*
- **Stream Chunk Frame** (with zero-indexed sequence integer):
  ```json
  {
    "type": "CHUNK",
    "job_id": "job_01hxyz89abc",
    "seq": 0,
    "delta": "A binary search tree is a node-based data structure...",
    "finish_reason": null
  }
  ```
  *(Worker A appends chunk to `ragbait:stream:results:{job_id}` via `XADD`)*.
- **Job Complete Frame**:
  ```json
  {
    "type": "JOB_COMPLETE",
    "job_id": "job_01hxyz89abc",
    "total_seq": 14,
    "input_tokens": 45,
    "output_tokens": 312,
    "duration_ms": 4250
  }
  ```
- **Job Error Frame**:
  ```json
  {
    "type": "JOB_ERROR",
    "job_id": "job_01hxyz89abc",
    "error_code": "MODEL_OOM",
    "message": "Local GPU memory exhausted"
  }
  ```
- **Job Cancel Frame** (sent by gateway if client aborts):
  ```json
  {
    "type": "JOB_CANCEL",
    "job_id": "job_01hxyz89abc",
    "reason": "CLIENT_DISCONNECTED"
  }
  ```

---

## 4. User AI Preferences

### 4.1 Get / Update Preferences
- **Route**: `GET /api/v1/me/ai-preferences` / `PATCH /api/v1/me/ai-preferences`
- **Request Body (PATCH)**:
  ```json
  {
    "allow_community_nodes": true,
    "preferred_model_id": "gemini-3.8-flash"
  }
  ```
- **Response** (`200 OK`): Updated preferences. Enforces the dual-consent privacy gate.

---

## 5. Unified LLM Gateway & Administrator Model Registry

### 5.1 List Available Models
- **Route**: `GET /api/v1/llm/models`
- **Response** (`200 OK`):
  ```json
  {
    "models": [
      {
        "id": "gemini-3.8-flash",
        "provider": "gemini",
        "display_name": "Google Gemini 3.8 Flash",
        "family": "gemini",
        "availability_status": "active",
        "release_stage": "ga",
        "capabilities": ["streaming", "tools", "vision"],
        "cost_category": "free_tier",
        "context_window": 1048576,
        "max_output_tokens": 8192,
        "modalities_input": ["text", "image"],
        "modalities_output": ["text"],
        "supports_streaming": true,
        "privacy_notice": "Google AI Studio free tier prompts may be used for model training."
      },
      {
        "id": "openai/gpt-oss-120b",
        "provider": "groq",
        "display_name": "OpenAI GPT-OSS 120B (Groq)",
        "family": "gpt-oss",
        "availability_status": "active",
        "release_stage": "ga",
        "capabilities": ["streaming", "reasoning", "tools"],
        "cost_category": "free_tier",
        "context_window": 131072,
        "max_output_tokens": 65536,
        "modalities_input": ["text"],
        "modalities_output": ["text"],
        "supports_streaming": true,
        "privacy_notice": null
      },
      {
        "id": "qwen2.5:7b",
        "provider": "ollama",
        "display_name": "Qwen 2.5 7B (Community Node)",
        "family": "qwen",
        "availability_status": "active",
        "release_stage": "ga",
        "capabilities": ["streaming"],
        "cost_category": "local",
        "context_window": 32768,
        "max_output_tokens": 4096,
        "modalities_input": ["text"],
        "modalities_output": ["text"],
        "supports_streaming": true,
        "privacy_notice": "Executed on a student-contributed local computer in volatile memory."
      }
    ]
  }
  ```

### 5.2 Synchronous Generation
- **Route**: `POST /api/v1/llm/generate`
- **Rate Limit**: User-level limit (10 requests/min, max 2 concurrent)
- **Request Body**:
  ```json
  {
    "model": "openai/gpt-oss-120b",
    "provider_preference": "auto",
    "messages": [
      {
        "role": "system",
        "content": "You are a concise engineering study assistant."
      },
      {
        "role": "user",
        "content": "Explain the threshold voltage of a MOSFET."
      }
    ],
    "temperature": 0.7,
    "max_tokens": 1000
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "id": "gen_8f3d1b9a2c",
    "model": "openai/gpt-oss-120b",
    "provider": "groq",
    "content": "The threshold voltage (Vth) of a MOSFET is the minimum gate-to-source voltage required...",
    "usage": {
      "input_tokens": 34,
      "output_tokens": 185,
      "total_tokens": 219
    },
    "latency_ms": 320,
    "finish_reason": "stop"
  }
  ```

### 5.3 Streaming Generation (SSE with Recovery Support)
- **Route**: `POST /api/v1/llm/generate/stream`
- **Headers**:
  - `Accept: text/event-stream`
  - `Last-Event-ID: <seq>` *(optional for reconnection)*
- **Stream Events**:
  ```http
  id: 0
  event: delta
  data: {"content": "The threshold "}

  id: 1
  event: delta
  data: {"content": "voltage is "}

  id: 2
  event: usage
  data: {"input_tokens": 34, "output_tokens": 185, "total_tokens": 219}

  id: 3
  event: done
  data: {"finish_reason": "stop", "latency_ms": 312}
  ```

---

## 6. Admin Model Registry & Quota Administration

### 6.1 Register or Update Model in Catalogue
- **Route**: `POST /api/v1/admin/models` / `PATCH /api/v1/admin/models/{id}`
- **Authorization**: `require_super_admin`
- **Request Body (PATCH)**:
  ```json
  {
    "availability_status": "deprecated",
    "replacement_model_id": "gemini-3.8-flash",
    "shutdown_at": "2026-06-01T00:00:00Z"
  }
  ```
- **Response** (`200 OK`): Updated model metadata. Disables new dispatches while preserving historical audit records.

### 6.2 View Dynamic Provider Quotas
- **Route**: `GET /api/v1/admin/provider-quotas`
- **Authorization**: `require_admin`
- **Response** (`200 OK`):
  ```json
  {
    "quotas": [
      {
        "id": "quota_gemini_38_flash_free",
        "provider_id": "gemini",
        "scope_type": "project",
        "scope_id": "global",
        "model_id": "gemini-3.8-flash",
        "limit_type": "rpm",
        "limit_value": 15,
        "source_documentation": "https://ai.google.dev/pricing",
        "confidence": "high",
        "provenance": "provider_published",
        "last_verification_time": "2026-02-01T00:00:00Z",
        "is_active": true
      }
    ]
  }
  ```

---

## 7. Contributor Usage Ledger & Admission Contracts (M2.4)

### 7.1 Contributor Personal Usage & Quota Ledger
- **Route**: `GET /api/v1/me/usage`
- **Rate Limit**: 60 requests / minute
- **Authorization**: `require_approved_user`
- **Response** (`200 OK`):
  ```json
  {
    "total_dispatched_requests": 42,
    "total_settled_requests": 40,
    "total_input_tokens": 154200,
    "total_output_tokens": 82100,
    "total_actual_cost_cents": 128,
    "total_actual_cost_microcents": 127850000,
    "total_reserved_cost_cents": 140,
    "daily_request_usage": 15,
    "daily_request_limit": 500,
    "monthly_request_usage": 120,
    "monthly_request_limit": 15000,
    "daily_spending_cents": 35,
    "daily_spending_microcents": 34620000,
    "daily_spending_budget_cents": 250,
    "monthly_spending_cents": 128,
    "monthly_spending_microcents": 127850000,
    "monthly_spending_budget_cents": 2500,
    "unknown_outcome_count": 0,
    "recent_reservations": [
      {
        "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "request_id": "req_8f12a4b8",
        "provider_id": "groq",
        "model_id": "openai/gpt-oss-120b",
        "status": "settled",
        "estimated_input_tokens": 500,
        "estimated_output_tokens": 1000,
        "estimated_cost_cents": 5,
        "estimated_cost_microcents": 67500,
        "actual_input_tokens": 480,
        "actual_output_tokens": 620,
        "actual_cost_cents": 3,
        "actual_cost_microcents": 44400,
        "currency": "USD",
        "created_at": "2026-02-04T12:00:00Z",
        "dispatched_at": "2026-02-04T12:00:01Z",
        "settled_at": "2026-02-04T12:00:03Z"
      }
    ]
  }
  ```

#### Monetary Subunit Precision Specifications
- **Unit Scale**: Microcents ($10^{-6}$ cents = $10^{-8}$ USD).
- **Exact Pricing Formula**:
  $$\text{cost\_microcents} = (\text{input\_tokens} \times \text{input\_cost\_per\_million\_cents}) + (\text{output\_tokens} \times \text{output\_cost\_per\_million\_cents})$$
- **Zero Floating-Point Drift**: Pure integer arithmetic throughout all ledger and counter operations. Small generations (< 1 cent) accumulate exact subcent fractions without premature truncation.
- **Reservation & Display Boundary Rounding**: Conservative ceiling $\lceil \text{microcents} / 1{,}000{,}000 \rceil$ ensures budget reservations never under-reserve and non-zero expenditures are reflected in whole-cent dashboards.

### 7.2 Internal Admission Pipeline Contracts
Admission is orchestrated strictly within backend services before external upstream API calls. Plaintext credentials and prompt contents are strictly forbidden from entering these interfaces.

#### Step 1: Pre-Dispatch Admission Reservation
```python
async def reserve_generation(req: AdmissionRequest) -> AdmissionReservation: ...
```
- **Prerequisites**: Validates credential status (`active`), credential validation (`valid`), model availability (`active`), and community sharing consent if cross-owner.
- **Microcents Cost Estimation**: Integer calculation via `compute_cost_microcents(...)`, followed by conservative whole-cent ceiling `microcents_to_ceiling_cents(...)`.
- **Cooldown Verification**: Verifies no active cooldown exists on provider, model, credential, or shared account scope.
- **Step A (Multi-Tier Rate Reservation)**: Atomic sliding-window rate slot check and reservation across all applicable scopes via Redis Lua (`reserve_rate_slots`):
  - `user`: Per-user RPM limit (platform baseline).
  - `credential`: Per-credential RPM limit (contributor configured or platform baseline).
  - `account`: Shared account RPM limit derived strictly from `account_scope_id` when present.
  - `provider`: Upstream provider published RPM quota.
  - `model`: Model-specific published RPM quota.
  - `model_tpm`: Sliding-window token-volume quota (TPM) weighted by estimated tokens.
- **Step B (Concurrency Reservation)**: Atomic distributed concurrency lease reservation via Redis Lua (`acquire_concurrency_lease`) bounded by a hard 300s maximum lifetime.
- **Step C (Transactional Budget Locking)**: PostgreSQL `SELECT FOR UPDATE` on `llm_usage_budget_counters` to evaluate daily/monthly request caps and microcent spending limits, persisting durable `llm_usage_reservations` and immutable `llm_usage_events` records.
- **Fail-Closed Compensation**: If Step B or C fails, previously acquired Redis rate slots or concurrency leases are immediately rolled back.

#### Step 2: Dispatch Commitment
```python
async def mark_dispatched(reservation_id: UUID) -> None: ...
```
- Transitions reservation status from `reserved` to `dispatched` in PostgreSQL and emits a `dispatch_committed` event immediately prior to opening upstream socket connections.

#### Step 3: Concurrency Heartbeat Extension
```python
async def renew_reservation_lease(user_id: UUID, credential_id: UUID, reservation_id: UUID, lease_token: str) -> LeaseRenewalResult: ...
```
- Extends active concurrency lease TTL during long-running streaming generations, strictly bounded by `LLM_MAX_RESERVATION_LIFETIME_SECONDS = 300s`. Once 300s elapsed, renewals are rejected with `max_lifetime_exceeded`.

#### Step 4: Final Settlement
```python
async def settle_reservation(reservation_id: UUID, req: SettlementRequest, model_id: str | None = None) -> SettlementResult: ...
```
- Calculates exact microcents cost based on actual tokens, updates transactional budget counters, transitions reservation to `settled`, releases the Redis concurrency lease, reconciles Redis TPM rate slots to actual token volume, and emits an immutable `usage_settled` audit event.

#### Step 5: Cancellation / Pre-Dispatch Release
```python
async def release_reservation(reservation_id: UUID, reason: str = "cancelled") -> ReleaseResult: ...
```
- Rolls back reserved budget from PostgreSQL counters, transitions reservation to `released`, releases the Redis concurrency lease, releases Redis rate slots, and emits an immutable `reservation_released` audit event.

#### Step 6: Expiration of Stale Reservations
```python
async def expire_reservation(reservation_id: UUID, reason: str = "lease_expired") -> ReleaseResult: ...
```
- Expired uncommitted reservations transition from `reserved` to `expired`, decrement reserved counters in PostgreSQL, clean up Redis leases, and log an immutable `reservation_expired` event.

#### Step 7: Ambiguous Network Partition / Outcome Unknown & Reconciliation
```python
async def mark_outcome_unknown(reservation_id: UUID, reason: str = "upstream_network_timeout") -> None: ...
async def reconcile_reservation(reservation_id: UUID, actual_input_tokens: int, actual_output_tokens: int, actual_cost_microcents: int | None = None, reconciliation_notes: str | None = None) -> SettlementResult: ...
```
- When upstream network drops after dispatch commitment, marks reservation as `outcome_unknown`, frees Redis concurrency to prevent deadlocks, but **conservatively retains** reserved spending budget until administrator or reconciliation intervention.
- `reconcile_reservation` transitions `outcome_unknown` or `reconciliation_required` reservations to `settled`, replaces conservative reservations with confirmed actual microcent usage, and records an immutable `usage_reconciled` event.

### 7.3 Admission Error Responses

#### Rate Limit Exceeded (`429 Too Many Requests`)
```json
{
  "code": "RATE_LIMIT_EXCEEDED",
  "message": "Admission rejected: Rate limit exceeded on credential (limit: 15 RPM)",
  "details": {
    "category": "rate_limit_credential",
    "retry_after": 14,
    "scope": "credential",
    "limit": 15
  }
}
```

#### Concurrency Limit Exceeded (`429 Too Many Requests`)
```json
{
  "code": "CONCURRENCY_LIMIT_EXCEEDED",
  "message": "Admission rejected: Maximum simultaneous operations reached (1)",
  "details": {
    "scope": "credential",
    "current_limit": 1
  }
}
```

#### Budget or Request Limit Exhausted (`429 Too Many Requests`)
```json
{
  "code": "BUDGET_EXHAUSTED",
  "message": "Admission rejected: Daily request limit reached (100)",
  "details": {
    "scope": "daily_requests",
    "limit": 100,
    "current_usage": 100
  }
}
```

#### Infrastructure Fail-Closed (`503 Service Unavailable`)
```json
{
  "code": "SERVICE_UNAVAILABLE",
  "message": "Admission coordination service temporarily unavailable",
  "details": null
}
```

