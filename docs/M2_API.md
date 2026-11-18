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

### 2.1 Submit New Credential
- **Route**: `POST /api/v1/me/api-keys`
- **Rate Limit**: 5 requests / minute
- **Request Body**:
  ```json
  {
    "provider_id": "groq",
    "raw_key": "gsk_exampleSecretKey1234567890",
    "nickname": "StudyGroup Free Groq",
    "sharing_scope": "community_shared",
    "shared_consent": true,
    "account_scope_id": "org_groq_university_lab",
    "daily_request_limit": 500,
    "monthly_request_limit": 15000,
    "daily_spending_limit_cents": null
  }
  ```
- **Validation Rules**:
  - `provider_id`: Must exist in `public.llm_providers` and be marked `is_enabled = true`.
  - **OpenAI Multi-Stage Credential Validation**:
    1. *Format Check*: Must match regex `^sk-proj-[A-Za-z0-9_-]{48,}$`.
       - Keys prefixed with `org-...` are rejected with `400 INVALID_CREDENTIAL_FORMAT` (an organization identifier is metadata, not an API credential).
       - Legacy personal keys `^sk-[A-Za-z0-9]{32,}$` are rejected with `422 SHARING_POLICY_VIOLATION`.
    2. *Provider-Side Verification Probe*: Non-mutating `GET https://api.openai.com/v1/models` using candidate key.
    3. *Project/Org Eligibility Verification*: Inspect response headers `openai-organization` and `openai-project` to verify the key is an authorized project-scoped service account.
    4. *Personal Key Prohibition*: Personal key pooling is permanently disabled. Supported strictly through administrator-managed project-scoped service accounts.
  - **Groq Credential Validation**:
    - Must match regex: `^gsk_[A-Za-z0-9]{48,}$`.
  - **Duplicate Prevention**:
    - Generates a keyed SHA-256 HMAC fingerprint (`key_fingerprint`) using `VAULT_PEPPER_SECRET`.
    - If `(provider_id, key_fingerprint)` already exists in `public.api_key_credentials`, returns `409 DUPLICATE_CREDENTIAL`.
  - `sharing_scope = 'community_shared'` requires `shared_consent = true`.
- **Response** (`201 Created`):
  ```json
  {
    "id": "7a3e9c4b-3d2f-41e8-a5b8-d4c3b2a10987",
    "provider_id": "groq",
    "nickname": "StudyGroup Free Groq",
    "masked_key": "gsk_...7890",
    "status": "active",
    "sharing_scope": "community_shared",
    "shared_consent": true,
    "daily_request_limit": 500,
    "monthly_request_limit": 15000,
    "created_at": "2026-02-01T12:00:00Z"
  }
  ```
- **Error Codes**:
  - `400 INVALID_CREDENTIAL_FORMAT`: Key format does not match provider specifications or is an org identifier.
  - `409 DUPLICATE_CREDENTIAL`: Key has already been registered for this provider.
  - `422 SHARING_POLICY_VIOLATION`: Provider terms forbid pooling personal keys for this provider.

### 2.2 List Own Credentials
- **Route**: `GET /api/v1/me/api-keys`
- **Rate Limit**: 60 requests / minute
- **Response** (`200 OK`):
  ```json
  {
    "items": [
      {
        "id": "7a3e9c4b-3d2f-41e8-a5b8-d4c3b2a10987",
        "provider_id": "groq",
        "nickname": "StudyGroup Free Groq",
        "masked_key": "gsk_...7890",
        "status": "active",
        "sharing_scope": "community_shared",
        "shared_consent": true,
        "daily_request_limit": 500,
        "monthly_request_limit": 15000,
        "today_usage": {
          "requests": 42,
          "total_tokens": 18450
        },
        "created_at": "2026-02-01T12:00:00Z"
      }
    ],
    "total": 1
  }
  ```

### 2.3 Modify Credential Settings
- **Route**: `PATCH /api/v1/me/api-keys/{id}`
- **Request Body**:
  ```json
  {
    "nickname": "Updated Nickname",
    "sharing_scope": "owner_only",
    "shared_consent": false,
    "daily_request_limit": 200
  }
  ```
- **Response** (`200 OK`): Updated metadata record.

### 2.4 Test Credential
- **Route**: `POST /api/v1/me/api-keys/{id}/test`
- **Rate Limit**: 5 requests / minute
- **Behavior**: Executes an isolated, non-mutating capability probe (e.g. `GET /v1/models` or lightweight single-token ping) against the provider using decrypted credentials via `ragbait_vault_service`.
- **Response** (`200 OK`):
  ```json
  {
    "status": "ok",
    "latency_ms": 142,
    "models_discovered": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
  }
  ```

### 2.5 Delete / Revoke Credential (Active Revocation)
- **Route**: `DELETE /api/v1/me/api-keys/{id}`
- **Behavior**: Transactionally deletes the record from `public.api_key_credentials` and cascades deletion of `private.api_key_secrets`. Flushes any in-memory or Redis caches. Active dispatches cease immediately. (Historical database backups retain encrypted ciphertexts and wrapped DEKs until the 30-day backup retention window expires).
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
