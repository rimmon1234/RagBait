# RagBait M2.8 — Two-Account Manual Verification Guide

This guide describes how to manually test and verify Shared Community Local Inference with two distinct student accounts (User A: Contributor, User B: Requester).

---

## Prerequisites

1. **Docker containers running**:
   - Supabase PostgreSQL on port 54322 (`npx supabase status`).
   - Redis container `ragbait-redis` on port 6379 (`docker ps`).
2. **Local Ollama daemon running**:
   - Running at `http://127.0.0.1:11434` with at least one model pulled (e.g. `ollama pull llama3.2:3b`).
3. **Backend API running**:
   - `cd apps/api; uv run uvicorn app.main:app --port 8000`
4. **Frontend running**:
   - `pnpm --filter ragbait-web dev` (accessible at `http://localhost:3000`).

---

## Scenario: Contributor (User A) and Requester (User B)

### Step 1: User A Pairs Node Agent
1. Sign in to `http://localhost:3000` as **User A** (`user_a@university.edu`).
2. In the top navigation bar, click **Community Nodes** (`/community-nodes`).
3. Click the **Pair New Node** button.
   - An 8-character pairing code appears (e.g. `RB-A1B2C3`) with a 5-minute countdown timer.
4. On User A's computer (or in a new terminal window), navigate to `apps/node-agent`:
   ```bash
   cd apps/node-agent
   uv run ragbait-node pair --server http://127.0.0.1:8000 --code RB-A1B2C3
   ```
   *Expected Output:*
   ```
   [*] Loaded Ed25519 identity key
   [*] Public Key: <64-char hex>
   [+] Successfully paired with RagBait!
       Node ID:      <UUID>
       Display Name: <Device Name>
       Status:       active
   ```
5. Click **Done** in the browser modal.
   - The new node card appears with status badge **ACTIVE / ONLINE**.

---

### Step 2: User A Enables Model Sharing
1. On the node card, locate the **Discovered Local Models** section.
2. Ensure the master **Community Sharing** toggle is checked.
3. Check the **Share** checkbox next to `llama3.2:3b` (or another installed model).
4. Start the node agent daemon in the terminal:
   ```bash
   uv run ragbait-node run
   ```
   *Expected Output:*
   ```
   [*] Local Ollama running: version 0.40.16
   [*] Discovered 2 local models: ['llama3.2:3b', ...]
   [*] Connecting to ws://127.0.0.1:8000/api/v1/community/ws...
   [+] WebSocket connected. Awaiting challenge...
   [*] Received challenge, signing with Ed25519 identity key...
   [+] Authenticated successfully with RagBait server!
   [*] Heartbeat loop started (interval: 15s)
   [*] Awaiting jobs...
   ```

---

### Step 3: User B Submits Inference with Explicit Consent
1. In an Incognito window (or secondary browser), navigate to `http://localhost:3000` and sign in as **User B** (`user_b@university.edu`).
2. Click **Local Inference** (`/local-inference`) in the navigation bar.
3. Switch from **Private (This Computer)** to **Community Network (Peer Nodes)** tab.
   - The status banner confirms: *Shared Community Inference Network Active*.
   - The model dropdown automatically lists `llama3.2:3b (1 node online)`.
4. Type a test prompt in the input box:
   `"Explain the concept of community computing in two sentences."`
5. Click **Send** (or press Enter).
   - **Consent Modal Triggered**: The *Community Inference Privacy Disclosure* modal appears.
   - The *Agree & Continue* button is disabled.
6. Check the required consent checkbox:
   `"I understand that my prompt will be processed by a community contributor node, and I explicitly consent to routing this request."`
7. Click **Agree & Continue**.
   - The modal closes.
   - The prompt is dispatched.

---

### Step 4: Verification of In-Flight Streaming & Telemetry
1. In User A's terminal (`ragbait-node run`), observe the job dispatch:
   ```
   [*] Received job <UUID> for model llama3.2:3b (max_output_tokens=2048)
   [*] Verified server Ed25519 authorization signature
   [*] Verified payload SHA-256 digest
   [*] Streaming inference from Ollama loopback...
   [+] Completed job <UUID> (184 tokens, 1420ms)
   ```
2. In User B's browser:
   - Tokens stream smoothly in real-time into the assistant chat bubble.
   - The right-hand sidebar updates with **Device-Reported / Community Node** telemetry:
     - Prompt Tokens
     - Output Tokens
     - Generation Speed (tokens/sec)
     - Total Latency
3. Inspect PostgreSQL via `npx supabase db query` or table inspection:
   ```sql
   SELECT id, status, payload_digest, reported_input_tokens, reported_output_tokens
   FROM public.community_inference_jobs;
   ```
   - Confirm that the job status is `COMPLETED`.
   - Confirm that **ZERO prompt or completion text** exists in the table.

---

### Step 5: Verify User B Job Cancellation
1. Submit a prompt requesting a long essay:
   `"Write a 1000-word comprehensive history of operating systems."`
2. While tokens are actively streaming, click the red **Stop** button.
   - Streaming halts immediately.
   - Assistant response shows `*(Generation stopped by user)*`.
3. In User A's terminal, observe:
   ```
   [*] Job <UUID> cancelled by server/requester
   ```
4. Query the database:
   ```sql
   SELECT id, status FROM public.community_inference_jobs ORDER BY created_at DESC LIMIT 1;
   ```
   - Confirm status is `CANCELLED`.

---

### Step 6: Verify User A Node Pause & Revocation
1. Switch back to User A's browser at `/community-nodes`.
2. Click the **Pause** button on the node card.
   - Status badge turns yellow (`PAUSED`).
3. Switch to User B's browser and click **Refresh Models**.
   - The paused model disappears from available community models.
4. User A clicks **Revoke**.
   - Node status becomes red (`REVOKED`).
   - Terminal agent receives disconnect and exits gracefully.
