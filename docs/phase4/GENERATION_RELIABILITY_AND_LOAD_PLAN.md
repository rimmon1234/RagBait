# RagBait Phase 4 — Generation Reliability and Load Plan

**Document Version:** 1.0.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Date:** November 22, 2026  
**Status:** Approved Operational Specification  

---

## 1. Generation Request State Machine & Lifecycle

Every assistant generation request transitions through an explicit, auditable state machine. A generation is never permitted to hang indefinitely in an indeterminate state.

```mermaid
stateDiagram-v2
    [*] --> accepted: Client POST /messages
    accepted --> retrieving: Idempotency & Auth passed
    retrieving --> preparing_context: Hybrid search finished
    preparing_context --> streaming: Context budgeted & LLM stream opened
    
    streaming --> completed: LLM finished & Citations validated
    streaming --> cancelled: Client abort signal or disconnect
    streaming --> failed: Provider error or timeout
    
    retrieving --> failed: Embedding/DB search error
    accepted --> rejected: Concurrency/Quota limit
    
    completed --> [*]
    cancelled --> [*]
    failed --> [*]
    rejected --> [*]
```

### 1.1 State Definitions
- `accepted`: Request payload validated, idempotency key checked, concurrency lease claimed.
- `retrieving`: Hybrid semantic and keyword queries executing against PostgreSQL.
- `preparing_context`: Prompt context budget calculated, evidence selected, XML prompt assembled.
- `streaming`: Upstream provider connection established, emitting SSE `text_delta` events.
- `completed`: Upstream finish reason `stop` received, citations reconciled, message updated.
- `cancelled`: Client explicitly cancelled or browser disconnected; partial text preserved.
- `failed`: Upstream error, context overflow, or timeout encountered with normalized error logged.
- `rejected`: Inadmissible request (e.g. concurrent generation already active in thread).

---

## 2. Concurrency Governance & Redis Semaphores

In a multi-user college environment, multiple students query the assistant simultaneously. RagBait enforces bounded concurrency using **Redis Distributed Semaphores**:

```mermaid
flowchart TD
    Req["Incoming Message Generation Request"]
    
    ConvLock{"1. Conversation Lock:
    Is generation already active for conversation_id?"}
    UserLock{"2. Per-User Semaphore:
    Does student have < 2 concurrent generations?"}
    GlobalLock{"3. Global Provider Semaphore:
    Are total cloud generations < 10?"}
    
    Proceed["Claim Leases (TTL 60s) & Execute Generation"]
    RejectConv["HTTP 409 Conflict:
    'Generation already active in this thread'"]
    RejectUser["HTTP 429 Too Many Requests:
    'Maximum 2 concurrent chats per student'"]
    QueueWait["Enter Bounded Wait Queue (Max 30s)"]
    
    Req --> ConvLock
    ConvLock -- Yes --> RejectConv
    ConvLock -- No --> UserLock
    
    UserLock -- No --> RejectUser
    UserLock -- Yes --> GlobalLock
    
    GlobalLock -- Capacity Available --> Proceed
    GlobalLock -- Saturated --> QueueWait
    QueueWait -- Slot Frees Within 30s --> Proceed
    QueueWait -- Timeout 30s --> RejectUser
```

### 2.1 Concurrency Limits
1. **Per-Conversation Mutex**: **1 active generation** per conversation. Enforces strict message sequence numbering and prevents interleaved assistant responses.
2. **Per-User Concurrency**: **2 simultaneous generations** per student account. Prevents a single student from consuming platform capacity across multiple browser tabs.
3. **Global Cloud Concurrency**: **10 simultaneous cloud API calls**. Prevents upstream rate limit cascades (`HTTP 429`) against shared free-tier credentials.
4. **Lease TTL**: Every Redis lease has a strict 60-second TTL. If a server worker crashes or power fails, the lock automatically self-clears, preventing permanent deadlocks.

---

## 3. Streaming & Cancellation Protocol

### 3.1 Server-Sent Events (SSE) Protocol
- Standard HTTP/1.1 or HTTP/2 chunked transfer encoding via FastAPI `EventSourceResponse`.
- Native browser disconnect detection via `await request.is_disconnected()`. If a student closes their laptop lid or navigates away, the server detects socket closure and halts generation immediately.

### 3.2 Explicit Client Cancellation
- **Route**: `POST /api/v1/chat/conversations/{id}/messages/{message_id}/cancel`
- **Execution Flow**:
  1. The client aborts the active `fetch()` request via `AbortController`.
  2. The client fires the cancel endpoint.
  3. The backend sets an internal `asyncio.Event` cancellation flag.
  4. The upstream provider HTTP stream is closed via HTTP transport abort.
  5. The assistant message in `chat_messages` is updated to `status = 'cancelled'` with whatever partial text was emitted up to the abort point.
  6. The Redis conversation lock and concurrency semaphores are released immediately.

---

## 4. Idempotency & Retries

### 4.1 Client Idempotency Keys
- Every message submission requires an `idempotency_key` (e.g. `idem-msg-9b1deb4d-01`).
- If network jitter causes the client to resend the same prompt, the server detects the existing `idempotency_key` in `chat_messages`:
  - If generation is already completed: returns the existing message payload immediately.
  - If generation is still streaming: re-attaches to the active stream or returns HTTP 409.
  - **Zero duplicate assistant answers are ever created.**

### 4.2 Upstream Provider Retries
- **Transient Errors (500, 502, 503, 504)**: Automatic retry with exponential backoff and jitter ($1.0\text{s} \to 2.5\text{s}$, max 2 attempts).
- **Rate Limits (429)**: The router marks the credential or provider in cooldown and immediately attempts failover to the next eligible candidate in the pool.
- **Permanent Errors (400, 401, 403, 404, 422)**: Terminated immediately without retry, delivering an actionable error message to the student.

---

## 5. Multi-User Load Testing & Pilot Capacity Plan

### 5.1 Pilot User Cohort
- **Registered Students**: 30–100 students.
- **Expected Concurrency**: 5–10 concurrent generation streams during peak study periods.

### 5.2 Progressive Stress Profiles
| Profile | Concurrent Streams | Purpose | Latency Target (TTFT) | Target Total Duration |
| :--- | :--- | :--- | :--- | :--- |
| **Profile 1: Baseline** | 1 stream | Functional sanity & zero-contention baseline | $< 800\text{ms}$ | $< 4.5\text{s}$ |
| **Profile 2: Light** | 5 streams | Nominal study group concurrency | $< 1.2\text{s}$ | $< 6.0\text{s}$ |
| **Profile 3: Peak** | 10 streams | Maximum expected classroom pilot load | $< 2.0\text{s}$ | $< 8.5\text{s}$ |
| **Profile 4: Burst Stress** | 20 streams | Verification of bounded queue and 429 rejection | Bounded or 429 | Queue $\le 30\text{s}$ |

### 5.3 Failure Mode Matrix
| Failure Scenario | Immediate System Behavior | Student Experience |
| :--- | :--- | :--- |
| **Upstream Provider Down** | Router fails over to next candidate in pool; if all fail, throws `PROVIDER_UNAVAILABLE`. | Neutral banner: "All cloud providers temporarily busy. Retrying in 10s or switch to Private Local." |
| **Redis Server Down** | Concurrency falls back to local in-memory lock; degrades gracefully. | Chat continues with local process limits. |
| **Database Pool Full** | Fast failover via connection timeout (5s). | Error message: "Database temporarily saturated; please retry." |
| **Worker Crash Mid-Stream** | Lease expires after 60s; message marked `failed` on subsequent load. | Student sees "Generation interrupted" with a safe Retry button. |
