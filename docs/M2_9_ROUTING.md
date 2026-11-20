# RagBait M2.9 — Unified Intelligent Inference Routing

## 1. Overview & Purpose

RagBait M2.9 introduces a unified, deterministic, privacy-aware inference routing layer that coordinates cloud providers (M2.1–M2.5) and community local Ollama nodes (M2.8) without replacing existing infrastructure or modifying student private local loopback inference (M2.7).

The router selects the optimal eligible execution candidate based on:
- Requested routing mode (`cloud_only`, `auto`, `community_only`).
- Requested model and required capabilities (context window, max output tokens, streaming support).
- Validated provider and community contributor node availability.
- Explicit requester consent and contributor terms.
- Real-time rate limits, concurrency limits, and cooldown status.
- Exact integer microcent pricing metadata.

---

## 2. Core Architecture & Pipeline

```
+-----------------------------------------------------------------------------------+
|                           POST /api/v1/llm/chat/completions                       |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 1. Idempotency Check (user_id + idempotency_key + payload_hash SHA-256)           |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 2. Candidate Discovery (apps/api/app/modules/routing/discovery.py)                |
|    - Cloud: active, validated user & community-shared keys in Supabase/Vault      |
|    - Community: online nodes in Redis, capacity, heartbeat within 30s             |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 3. Hard Candidate Filtering (apps/api/app/modules/routing/policy.py)              |
|    - 15 deterministic exclusion filters (capabilities, cooldown, concurrency)     |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 4. Deterministic Ranking Comparator (5-tier stable sort)                          |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 5. Pre-Dispatch Check & Dispatch Commitment (apps/api/app/modules/routing/executor)|
|    - Pre-dispatch: Admission reservation, Vault credential decryption            |
|      (Candidate-specific failure -> safe fallback to Candidate #2)                |
|    - Post-dispatch commitment (mark_dispatched): NO FALLBACK ALLOWED              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 6. Execution & Streaming                                                          |
|    - Cloud: Provider adapter streaming SSE                                        |
|    - Community: Redis Streams job dispatch -> SSE bridge                          |
|    - Headers: X-RagBait-Routing-Mode, Selected-Source, Selected-Model, Reason      |
+-----------------------------------------------------------------------------------+
```

---

## 3. Privacy Boundaries & Consent Matrix

The router enforces strict privacy boundaries and never crosses them:

| Routing Mode | Requires Community Opt-In | Can Select Cloud | Can Select Community | Fallback Behavior on Missing Community Nodes |
|---|---|---|---|---|
| `cloud_only` (default) | Ignored | Yes | **Never** | Fails closed with 409 Conflict if no cloud key |
| `auto` | Required for community consideration | Yes | Only if opted in | Falls back to cloud providers; never uses community without opt-in |
| `community_only` | Mandatory (`community_opt_in=True` + valid consent version) | **Never** | Yes | **Never falls back to cloud**; raises 409 Conflict |

---

## 4. Deterministic Ranking Comparator

Eligible candidates are sorted using a 5-tier deterministic tuple comparator:

1. **User Key Ownership Tier**:
   - `0`: User's own direct validated API credential.
   - `1`: Community-shared credential pool or contributor node.
2. **Estimated Cost Microcents**:
   - Integer microcents `(input_tokens * input_cost_per_m + output_tokens * output_cost_per_m) / 1,000,000`.
   - Free-tier cloud models: `0` microcents.
   - Community nodes: `0` microcents.
   - Missing pricing on metered models: Represented as `None` (sorted behind priced candidates, never assumed free).
3. **Queue Health & Available Capacity**:
   - Cloud: `concurrency_limit - current_inflight_count`.
   - Community: `max_concurrent_jobs - current_active_jobs`.
   - Sorted in descending order (higher headroom preferred).
4. **Context Window Headroom**:
   - `model.context_window - estimated_input_tokens` (descending order).
5. **Alphabetical Tie-Breaker**:
   - `(provider_id, model_id, candidate_id)` lexicographically ascending.

---

## 5. 15 Hard Candidate Filters

A candidate is strictly eliminated if any of the following 15 conditions are met:

1. Model is disabled (`is_active = false`).
2. Model has reached scheduled retirement (`shutdown_at <= now()`).
3. Model is marked as deprecated (`lifecycle_status = 'deprecated'`).
4. Provider is administratively disabled (`is_active = false`).
5. Model context window is smaller than estimated prompt tokens.
6. Model max output tokens is smaller than requested `max_output_tokens`.
7. Candidate does not support streaming when `stream = true` was requested.
8. Candidate does not support non-streaming when `stream = false` was requested.
9. Metered cloud model lacks an authorized active validated credential.
10. Community-shared credential has `shared_consent = false` or sharing is disallowed by provider policy.
11. Provider, model, or credential is in active cooldown.
12. Provider, model, user, or credential has exhausted its rate limit (RPM).
13. Model or provider concurrency limit is currently saturated.
14. Contributor node is offline or has missed heartbeat (>30 seconds).
15. Contributor node has reached `max_concurrent_jobs`.

---

## 6. API Contracts

### `POST /api/v1/routing/preview`
Simulates routing policy without committing reservations or dispatching jobs:
```json
{
  "messages": [{"role": "user", "content": "Explain quantum tunneling"}],
  "routing_mode": "auto",
  "community_opt_in": true,
  "params": {
    "temperature": 0.7,
    "max_output_tokens": 1024
  }
}
```
Response:
```json
{
  "routing_mode": "auto",
  "selected_candidate": {
    "candidate_id": "cand-gemini-3.8-flash",
    "source_type": "cloud",
    "provider_id": "gemini",
    "model_id": "gemini-3.8-flash",
    "estimated_cost_microcents": 0,
    "is_user_owned_credential": true
  },
  "evaluated_candidates_count": 6,
  "eligible_candidates_count": 4,
  "decision_reason": "Ranked #1 by deterministic comparator (user-owned credential, lowest cost microcents)",
  "all_candidates": [...]
}
```

### `POST /api/v1/llm/chat/completions` (Backward-Compatible)
Accepts optional routing extensions while maintaining 100% backward compatibility:
- `routing_mode`: `"auto"` | `"cloud_only"` | `"community_only"`.
- `community_opt_in`: `boolean`.
- `community_consent_version`: `string`.
- `candidate_preference`: `"cost"` | `"speed"` | `"balanced"`.

Returns safe response headers:
- `X-RagBait-Routing-Mode`
- `X-RagBait-Selected-Source`
- `X-RagBait-Selected-Model`
- `X-RagBait-Decision-Reason`

---

## 7. Telemetry & Zero Leak Guarantee

In compliance with M2 privacy guarantees:
- Telemetry headers and audit logs NEVER expose prompt text, completion text, raw API keys, or contributor hardware identifiers.
- Idempotency records verify user ownership and request payload hash equality (`SHA-256`), preventing cross-user collisions and duplicate dispatch with altered payloads.
