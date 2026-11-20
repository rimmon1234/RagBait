# RagBait M2 — Failure Handling, Circuit Breaking & Recovery Specification

## 1. Failure Philosophy

RagBait's shared LLM Gateway operates under four resilience principles:
1. **Predictable Containment**: A failure in one contributor's credential, local node, or provider must never degrade other resources or crash the gateway process.
2. **Anti-Amplification**: When an upstream provider or credential fails, the gateway limits retries to **at most one alternative candidate**. Indiscriminate rotation across multiple credentials is strictly forbidden.
3. **Fail-Secure Defaults**: In production, if distributed admission control (Redis) or cryptographic envelope decryption (KMS) is unavailable, shared community routing fails closed with an informative `503 Service Unavailable`.
4. **Transparent User Communication**: Users are informed of the precise reason for an error (e.g., rate limits, provider outages, or missing community consent) without exposing backend secrets or stack traces.

---

## 2. Comprehensive Error Classification Matrix

| Error Scenario | Detection Signal | Gateway Classification | Credential / Node Action | Fallback Policy | User-Facing HTTP Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Invalid / Revoked API Key** | HTTP 401 / 403 (`invalid_api_key`, `unauthorized`) | `CREDENTIAL_REVOKED` | Mark credential `revoked`; evict from Redis pool; notify owner in UI | Retry once with next eligible credential | Transparent to user (if retry succeeds); else `502 Bad Gateway` |
| **Transient Rate Limit (RPM)** | HTTP 429 with `Retry-After` or `rate_limit_exceeded` | `RATE_LIMIT_TRANSIENT` | Apply cooldown to credential (or `account_scope_id` if org-wide) for `Retry-After` duration (min 60s) | Retry once with alternative provider or credential | `429 Too Many Requests` (if no alternatives exist) |
| **Quota / Credit Exhaustion** | HTTP 429 / 400 (`insufficient_quota`, `credit_exhausted`) | `QUOTA_EXHAUSTED` | Mark credential `quota_exhausted`; exclude from pool until monthly reset date | Retry once with alternative provider | `503 Service Unavailable` with message: *"Platform quota reached"* |
| **Provider Server Error** | HTTP 500, 502, 503, 504 | `PROVIDER_DOWN` | Increment failure counter; apply 120s cooldown if 3 consecutive failures occur | Retry once with alternative provider | `502 Bad Gateway` |
| **Connect / Read Timeout** | Connect > 5s, Read > 30s | `TIMEOUT` | Increment failure counter; release concurrency lease immediately | Retry once with alternative provider | `504 Gateway Timeout` |
| **Node Auth: Invalid Signature** | Ed25519 signature verification fails | `NODE_AUTH_INVALID` | Close WSS with code 1008; record failure in Redis rate limiter | None | N/A (Agent connection closed) |
| **Node Auth: Replayed / Expired Nonce** | Nonce missing in Redis or already consumed | `NODE_AUTH_REPLAY` | Close WSS with code 1008; log security audit alert | None | N/A (Agent connection closed) |
| **Node Auth: Revoked Node Key** | `is_revoked = true` in `inference_nodes` | `NODE_REVOKED` | Close WSS with code 1008; block connection | None | N/A (Agent connection closed) |
| **Node Job Auth: Expired / Target Mismatch**| Node Agent rejects `job_token` validation | `JOB_AUTH_REJECTED` | Agent returns `JOB_ERROR`; gateway fails over to cloud compute | Retry once with cloud provider | `502 Bad Gateway (Compute Rejected)` |
| **Local Node Disconnected** | WSS connection drops or ping missed for 30s | `NODE_OFFLINE` | Mark node `offline` in Redis & DB; evict from active routing | Re-route to cloud provider (if user privacy setting permits) | `503 Node Disconnected` |
| **Local Node Job Lease Expired** | No chunk or progress frame received for 30s | `NODE_JOB_TIMEOUT` | Mark job failed; free concurrency; terminate relay stream | Retry once with cloud provider (if privacy permits) | `504 Local Node Timeout` |
| **Local Node OOM (Out of Memory)** | Node agent reports `out_of_memory` frame | `NODE_OOM` | Mark model on node `paused` for 15 minutes; report memory error to owner | Re-route to cloud provider | `503 Local Compute Exhausted` |
| **Durable Stream NACK / Dead-Letter**| Node repeatedly rejects job or Worker crashes | `STREAM_JOB_FAILED` | Move job to `ragbait:stream:jobs:dlq`; release concurrency | Retry once with cloud provider | `502 Bad Gateway (Compute Failed)` |
| **Contributor Withdraws Mid-Flight**| Owner disables or deletes key during active request | `CREDENTIAL_WITHDRAWN` | In-flight request completes or aborts gracefully; subsequent dispatches immediately blocked | No retry on same key | Transparent (or `409 Resource Withdrawn`) |
| **Client Disconnects (SSE Abort)** | Client closes browser tab or terminates SSE connection | `CLIENT_CANCELLED` | Send `JOB_CANCEL` to node agent; immediately release concurrency lease | None | Connection closed |
| **Redis Admission Control Down** | Connection timeout or connection refused | `INFRASTRUCTURE_FAILURE` | Fail closed on shared pool; allow owner-only private node routing | None | `503 Service Unavailable (Security Engine)` |
| **KMS / Master Key Failure** | Decryption fails or KMS unreachable | `CRYPTO_FAILURE` | Abort dispatch; log security event; zero plaintext fallback | None | `500 Decryption Failed` |

---

## 3. Quota Scope & Cooldown Mechanics

### 3.1 Credential vs Account Scope Cooldown
When an HTTP 429 or quota error is returned:
1. **Isolated Key Scope**: If the provider enforces per-key quotas (e.g. OpenRouter standard keys), only that specific credential (`credential_id`) is placed in cooldown.
2. **Organization / Project Scope**: If the provider enforces quotas at the organization level (e.g., GroqCloud or GCP Project), the gateway cools down the entire `account_scope_id`.
   - **Anti-Evasion Invariant**: The gateway will **not** route subsequent requests to other credentials that belong to the same exhausted organization scope.
   - Evaluated dynamically against `public.provider_quotas`.

### 3.2 Cooldown Backoff Strategy
Cooldown durations are enforced in Redis using TTL keys (`ragbait:cooldown:cred:{id}` and `ragbait:cooldown:scope:{scope_id}`):
- 1st failure: `max(Retry-After, 60s)`
- 2nd consecutive failure: 120s
- 3rd consecutive failure: 300s (5 minutes)
- 4th consecutive failure: 900s (15 minutes)

---

## 4. Reliable Node Delivery & Recovery Failure Scenarios

### 4.1 Worker Crash Before Agent Receipt
- **State**: Job is `QUEUED` in Redis Stream and `CLAIMED` by Worker A in the Pending Entries List (PEL). Worker A terminates before delivering the frame or before receiving `AGENT_ACCEPTED`.
- **Handling**:
  - The job remains unacknowledged in the PEL with an active idle timer.
  - A supervisory worker detects the idle entry (> 30s) via `XPENDING` / `XAUTOCLAIM`.
  - The supervisor claims the job and re-checks node availability. If the Node Agent reconnected to Worker C, the job is re-dispatched. If the node is offline, the job is marked `FAILED` and falls back to an eligible cloud provider.

### 4.2 Worker Crash After Agent Receipt
- **State**: Agent has already emitted `AGENT_ACCEPTED`, Worker A has executed `XACK`, and inference is `RUNNING` on the student device. Worker A crashes during generation.
- **Handling**:
  - The WebSocket drops. The Node Agent detects socket termination and halts or holds generation.
  - Worker B (handling the HTTP SSE stream) detects that no new chunks have been appended to `ragbait:stream:results:{job_id}` for 15s.
  - Worker B terminates the client SSE stream with `RELAY_DISCONNECTED` and logs the incident.
  - Concurrency gauge in Redis is decremented.

### 4.3 Agent Crash After Acceptance
- **State**: Agent returns `AGENT_ACCEPTED`, but Ollama crashes or the student PC is powered off before producing output tokens.
- **Handling**:
  - Worker A detects TCP disconnect or lease silence.
  - The lease key `ragbait:node:job:lease:{job_id}` expires in Redis after 30s without progress frames.
  - Worker A marks job status `FAILED` in `public.llm_requests`, decrements concurrency, and Worker B executes a single fallback to an eligible cloud provider.

### 4.4 Duplicate Delivery & Idempotency Suppression
- **State**: Network delay causes a job to be re-delivered to a Node Agent that is already processing it or has already completed it.
- **Handling**:
  - Every job carries a unique `job_id`.
  - The Node Agent checks its local in-memory active job set and Redis dedup key (`ragbait:job:dedup:{job_id}`).
  - If the job is active or finished, the agent re-emits `AGENT_ACCEPTED` and discards the duplicate instruction, preventing double execution.

### 4.5 Expired Delivery Lease & Bounded Retry
- **State**: An inference job hangs or fails to complete within reasonable parameters.
- **Handling**:
  - Jobs enforce a strict maximum duration ceiling of **300 seconds (5 minutes)**.
  - If a job exceeds 300s or fails repeatedly, it is transferred to `ragbait:stream:jobs:dlq`.
  - The gateway initiates **at most one fallback attempt** across the entire request lifecycle.

### 4.6 Missing Chunks & Stream Reconnection
- **State**: Client temporary network blip causes dropped SSE packets.
- **Handling**:
  - Chunks carry sequence numbers (`seq: 0, 1, 2, ...`) and are stored in Redis Stream `ragbait:stream:results:{job_id}` (10-minute TTL).
  - Client reconnects with `Last-Event-ID: <seq>`.
  - Worker B fetches `XREAD STREAMS ragbait:stream:results:{job_id} <last_seq>` and replays missed chunks in order.
  - Incoming chunks with `seq < expected_seq` are dropped to suppress duplicates.

### 4.7 Client Cancellation
- **State**: Client navigates away or hits stop button during generation.
- **Handling**:
  - Worker B detects `request.is_disconnected()`.
  - Worker B publishes a cancellation message to `ragbait:stream:jobs:{node_id}` (`JOB_CANCEL`).
  - Worker A forwards `JOB_CANCEL` to the Node Agent over WebSocket.
  - The Node Agent terminates local inference against Ollama immediately, freeing GPU/CPU resources.
  - Concurrency lease in Redis is decremented immediately.

---

## 5. Storage Lifecycle, Backup Realities & Cryptographic Erasure

RagBait distinguishes between operational storage lifecycles and cryptographic key destruction:

1. **Backup Retention**: Full database backups are kept on a **30-day scheduled retention policy**.
2. **WAL Retention**: Write-Ahead Logging archives for point-in-time recovery are retained for **7 days**.
3. **Snapshot Expiry vs Physical Overwrite**: After 30 days, backup snapshots reach their retention deadline and are marked for deletion by cloud storage lifecycle rules. Physical block overwrite depends on the cloud storage provider's garbage collection; RagBait does not claim immediate physical zeroing.
4. **Cryptographic Key Invalidation (True Erasure)**:
   - When the Master KEK is rotated, all active DEKs in `private.api_key_secrets` are rewrapped under the new KEK (`key_version = key_version + 1`).
   - The old KEK is permanently destroyed.
   - Once the old KEK is destroyed, all historical database backup snapshots containing DEKs wrapped under that KEK become permanently and mathematically undecryptable, achieving cryptographic forward secrecy regardless of physical disk persistence.
