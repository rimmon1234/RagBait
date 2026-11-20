# RagBait M2.8 — Shared Community Local Inference & Ollama Node Agent

## 1. Architectural Overview

M2.8 introduces shared community local inference to RagBait. Building upon M2.7's private browser loopback inference, M2.8 allows students to voluntarily register their computer as a community inference node and share selected local Ollama models with approved peers.

```
+----------------------------------------------------------------------------------------------------+
|                                    CONTRIBUTOR MACHINE (Local)                                     |
|                                                                                                    |
|   +-----------------------+           Loopback Only (127.0.0.1:11434)      +-------------------+   |
|   |  Ollama Node Agent    |  <===========================================>  |  Local Ollama     |   |
|   |  (apps/node-agent)    |           (Outbound HTTP, No External Ports)   |  (Llama, Mistral) |   |
|   +-----------------------+                                                +-------------------+   |
|               ^                                                                                    |
|               | Outbound-Only WSS + Ed25519 Mutual Auth (Challenge-Response)                       |
+---------------|------------------------------------------------------------------------------------+
                |
                v
+----------------------------------------------------------------------------------------------------+
|                                      RAGBAIT SERVER & BACKEND                                      |
|                                                                                                    |
|   +-----------------------+       Server Ed25519 Sign       +----------------------------------+   |
|   |  FastAPI Community    |  ---------------------------->  |  Redis Streams Engine            |   |
|   |  Module & Sweeper     |                                 |  - stream:node:{id}:jobs         |   |
|   +-----------------------+                                 |  - stream:job:{id}:chunks        |   |
|               |                                             |  - pubsub:job:{id}               |   |
|               | Audit, Nodes, Limits, Authoritative State    +----------------------------------+   |
|               v                                                                                    |
|   +-----------------------+                                                                        |
|   |  PostgreSQL / Supabase| (Authoritative lifecycle state; ZERO prompt or completion text stored) |
|   +-----------------------+                                                                        |
+----------------------------------------------------------------------------------------------------+
                ^
                | SSE Streaming (/jobs/{id}/stream) with Last-Event-ID resume
                |
+----------------------------------------------------------------------------------------------------+
|                                      REQUESTER BROWSER (Web)                                       |
|                                                                                                    |
|   +-----------------------+    Dual Consent Required:    +-------------------------------------+   |
|   |  Next.js Web Client   | ---------------------------> |  CommunityConsentModal              |   |
|   |  (/local-inference)   |                              |  (Explicit Peer Routing Disclosure) |   |
|   +-----------------------+                              +-------------------------------------+   |
+----------------------------------------------------------------------------------------------------+
```

---

## 2. Core Security & Privacy Guarantees

### A. Zero Permanent Storage of Prompts or Completions
- Neither requester prompts nor assistant completions are stored in PostgreSQL tables, database logs, or file storage.
- PostgreSQL stores only:
  - Authoritative job lifecycle states (`QUEUED`, `ASSIGNED`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`).
  - Cryptographic verification metadata: `payload_digest` (SHA-256), `hard_deadline`, and lease status.
  - Telemetry: reported input/output token counts and elapsed milliseconds.
- Job payloads and token chunks travel ephemerally through Redis Streams (`stream:node:{id}:jobs` and `stream:job:{id}:chunks`) with a strict 1-hour TTL and maximum stream length (`MAXLEN ~ 2000`).
- The local node agent stores only metadata in its local SQLite database (`ragbait_node_jobs.db`), strictly recording job ID, model name, status, and duration for deduplication and local diagnostics.

### B. Outbound-Only Node Architecture
- The contributor node agent connects **outward** to the RagBait server over secure WebSockets (`wss://api.ragbait.edu/api/v1/community/ws`).
- The contributor's computer never opens inbound listening ports, never uses UPnP, and never requires reverse SSH or third-party tunneling.
- The node agent interacts with Ollama strictly via loopback (`http://127.0.0.1:11434`), preventing any external network access to the contributor's Ollama daemon.

### C. Cryptographic Authentication & Authorization
- **Node Identity**: Each node agent generates an Ed25519 private key locally upon first initialization (`~/.ragbait-node/node_key.pem`, permissions `0600`).
- **Challenge-Response Handshake**: The server issues a high-entropy 32-byte nonce on WebSocket connection. The node signs the canonical payload:
  `RAGBAIT-NODE-AUTH:v1:{node_id}:{nonce_hex}:{session_id}:{timestamp}`
  The server verifies the signature using the node's registered public key before promoting the connection.
- **Server-Signed Job Authorizations**: Every dispatched job carries a cryptographic ticket signed by the server's Ed25519 private key:
  `RAGBAIT-JOB-AUTH:v1:{job_id}:{node_id}:{model_name}:{payload_digest}:{max_output_tokens}:{hard_deadline_ts}:{auth_nonce}`
  The node agent independently computes the SHA-256 digest of the incoming messages and verifies the server's signature before calling Ollama. Unauthorized or tampered jobs are instantly dropped.

### D. Dual Explicit Consent
1. **Contributor Consent (Model Allowlist)**:
   - When a node registers, all discovered local models default to unshared (`enabled = false`).
   - Contributor must explicitly toggle sharing for each specific model on the dashboard (`/community-nodes`).
   - Contributor retains a master "Community Sharing" switch, pause button, and permanent revocation.
2. **Requester Consent (Privacy Disclosure)**:
   - Requesters must explicitly select community mode; RagBait never automatically routes prompts to community nodes behind the user's back.
   - Before dispatching, the requester is shown an interactive disclosure dialog (`CommunityConsentModal`) explaining that compute executes on peer hardware.
   - The backend enforces `consent_accepted: true` at the API boundary, returning HTTP 400 Bad Request if consent is missing.

### E. Honest Ledger & Billing Accounting
- Community inference is powered by volunteer student compute and electricity.
- All completed community jobs record `usage_provenance = 'device_reported'` in the usage ledger.
- Cost charged to the student's cloud budget is exactly \$0.00.
- Usage metrics track tokens and execution time transparently without artificial billing markups.

---

## 3. Contributor Setup & CLI Reference

### Installation
The node agent is distributed as a lightweight Python package managed with `uv`:

```bash
cd apps/node-agent
uv sync
```

### Pairing with RagBait Account
1. Open the RagBait Web UI and navigate to **Community Nodes** (`/community-nodes`).
2. Click **Pair New Node** to generate a 128-bit single-use pairing code (e.g., `RB-A1B2C3D4-E5F60718-9A0B1C2D-3E4F5A6B`).
3. In terminal, run:
```bash
uv run ragbait-node pair --server http://127.0.0.1:8000 --code RB-A1B2C3D4-E5F60718-9A0B1C2D-3E4F5A6B --models llama3.2:3b,mistral:7b
```
This will:
- Generate a local Ed25519 keypair if one does not exist (`~/.ragbait-node/node_key.pem`).
- Send the public key and device metadata to the server.
- Save configuration and allowed models locally (`~/.ragbait-node/config.json`).

### Running the Agent Daemon
```bash
uv run ragbait-node run
# Or override allowed models locally:
uv run ragbait-node run --models llama3.2:3b
```
The agent will:
1. Verify Ollama is running on `http://127.0.0.1:11434`.
2. Connect to the RagBait WebSocket endpoint (`/api/v1/community/ws`).
3. Complete cryptographic challenge-response authentication.
4. Sync discovered local models to the server.
5. Send heartbeats every 15 seconds.
6. Await server-signed jobs, stream completions back via WebSocket, and omit internal thinking content.

### Status and Unpairing
```bash
# Check current registration status
uv run ragbait-node status

# Unpair and remove local configuration
uv run ragbait-node unpair
```

---

## 4. API Endpoints Reference

### Contributor Endpoints
- `POST /api/v1/community/nodes/pair-code`: Generate single-use 5-minute pairing code.
- `POST /api/v1/community/nodes/pair`: Complete agent registration with public key.
- `GET /api/v1/community/nodes`: List owned community nodes.
- `GET /api/v1/community/nodes/{node_id}`: Get node details and model allowlist.
- `PATCH /api/v1/community/nodes/{node_id}`: Update display name, limits, or master sharing.
- `PUT /api/v1/community/nodes/{node_id}/models`: Toggle sharing per model.
- `POST /api/v1/community/nodes/{node_id}/pause`: Pause node.
- `POST /api/v1/community/nodes/{node_id}/resume`: Resume paused node.
- `DELETE /api/v1/community/nodes/{node_id}`: Permanently revoke node.

### Requester Endpoints
- `GET /api/v1/community/models`: List available models offered by online community nodes.
- `POST /api/v1/community/jobs/submit`: Submit inference request (requires `consent_accepted: true`).
- `GET /api/v1/community/jobs/{job_id}`: Inspect authoritative job status and token telemetry.
- `POST /api/v1/community/jobs/{job_id}/cancel`: Cancel queued or running job.
- `GET /api/v1/community/jobs/{job_id}/stream`: Server-Sent Events (SSE) token chunk stream (authenticated, requester-only).

---

## 5. Redis Deployment & Prompt Privacy Policies

RagBait community streaming operates with explicit prompt privacy policies:

### Option A: Volatile In-Memory Queues (Recommended for Strict Student Privacy)
- **Configuration:** Dedicated Redis instance configured with `appendonly no` and `save ""` (pure in-memory).
- **Privacy Property:** Prompts and completed tokens **never** touch persistent disk storage. Memory is wiped on container termination.
- **Reliability:** If Redis restarts, all active in-flight stream queues are lost. PostgreSQL reconciles lost jobs as `OUTCOME_UNKNOWN` or `EXPIRED`, and the requester must resubmit.

### Option B: Persistent Queues (Durability over Strict Ephemerality)
- **Configuration:** Redis runs with AOF enabled (`appendonly yes`) on persistent storage volumes.
- **Privacy Property:** Prompts and completions are written to disk. Mitigations require:
  1. Full disk volume encryption (LUKS / encrypted EBS) at rest.
  2. Strict OS file permissions (`0600`) on `/data`.
  3. Aggressive AOF rewrite schedules (`auto-aof-rewrite-percentage 50`).
  4. Explicit exclusion of Redis data volumes from unencrypted backups.
- **Reliability:** In-flight queues survive Redis restarts, but prompt bytes exist on physical storage until compacted.

> Default production recommendation is **Option A** for ephemeral prompt transmission.
