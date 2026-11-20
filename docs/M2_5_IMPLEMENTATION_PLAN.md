# RagBait M2.5 — Implementation Plan: Core Provider Adapters & Real Inference Gateway

**Version**: 1.0.0  
**Milestone**: M2.5  
**Target Completion**: 2026-02-07  

---

## 1. Objectives & Scope

### 1.1 In-Scope Deliverables
1. **Additive Database Migration**:
   - Add `reserved_tokens bigint NOT NULL DEFAULT 0` and `actual_tokens bigint NOT NULL DEFAULT 0` to `public.llm_usage_budget_counters` for daily Tokens Per Day (TPD) tracking.
   - Add `resolved_model_id text`, `upstream_provider text`, `upstream_generation_id text`, and `usage_source text DEFAULT 'authoritative'` to `public.llm_usage_reservations`.
2. **Server-Side Token Estimator**:
   - Conservative token counter for incoming messages + reserved maximum output tokens.
   - Safe heuristic tokenizer (character/word + safety factor) ensuring zero reliance on client claims.
3. **Mandatory TPD Enforcement**:
   - Atomic evaluation against `provider_quotas` where `limit_type = 'tpd'` in row-locked PostgreSQL transactions.
   - Bounded rollback and release upon cancellation.
4. **Provider Adapters**:
   - `GeminiAdapter`: Native Google Gemini REST & SSE API (`:generateContent`, `:streamGenerateContent?alt=sse`, `x-goog-api-key`, `usageMetadata`).
   - `GroqAdapter`: OpenAI-compatible chat completions with `include_usage: true` and rate-limit header parsing.
   - `OpenRouterAdapter`: Dynamic alias resolution (`openrouter/free` $\to$ resolved upstream model) and generation ID capture.
   - `CerebrasAdapter`: Fast streaming and reasoning token breakdown extraction.
5. **Shared Transport & Security**:
   - Async HTTP client with strict timeouts, fixed hostnames, redirect blocking, and connection pooling.
   - No credential logging or prompt leakage.
6. **Unified Generation Endpoint**:
   - `POST /api/v1/llm/chat/completions` supporting both SSE streaming and non-streaming JSON responses.
7. **Comprehensive Test Suite**:
   - pgTAP database tests for TPD counters and constraints.
   - Pytest unit and integration tests covering adapters, token estimation, SSE fragmentation, dynamic aliases, TPD limits, and security boundaries.

### 1.2 Out of Scope
- No multi-provider routing or automatic fallback.
- No automatic retries or credential rotations.
- No local node execution (Ollama, vLLM, LM Studio, Node Agent).
- No vector retrieval, document embeddings, or RAG pipeline (M3/M4).
- No student study chat UI (M5).
- Multimodal inputs, tools, and function calling.

---

## 2. Architecture & Module Layout

```
apps/api/app/modules/llm_gateway/
├── __init__.py
├── router.py                  # Existing admin/catalog router + POST /chat/completions
├── schemas.py                 # Registry schemas + ChatCompletionRequest/Response + SSE schemas
├── service.py                 # Existing registry operations + gateway orchestration
├── interfaces.py              # BaseProviderAdapter abstract class & normalized data structures
├── token_estimation.py        # Conservative server-side token estimator
├── http_client.py             # Shared httpx.AsyncClient with lifecycle hooks
├── streaming.py               # Robust SSE parser (fragmentation, UTF-8, [DONE], usage frames)
├── errors.py                  # Error taxonomy & 429 Retry-After cooldown mapping
└── providers/
    ├── __init__.py
    ├── gemini.py              # Native Gemini adapter
    ├── groq.py                # Groq adapter
    ├── openrouter.py          # OpenRouter adapter with dynamic alias resolution
    └── cerebras.py            # Cerebras adapter with reasoning token breakdown
```

---

## 3. Database Migration Plan (`20260207000000_m2_5_tpd_and_gateway_ledger.sql`)

### 3.1 Schema Additions
1. **`public.llm_usage_budget_counters`**:
   - `reserved_tokens bigint NOT NULL DEFAULT 0`
   - `actual_tokens bigint NOT NULL DEFAULT 0`
   - Check constraint `reserved_tokens >= 0` and `actual_tokens >= 0`.
2. **`public.llm_usage_reservations`**:
   - `resolved_model_id text`
   - `upstream_provider text`
   - `upstream_generation_id text`
   - `usage_source text NOT NULL DEFAULT 'authoritative'` CHECK (`usage_source IN ('authoritative', 'derived', 'estimated', 'unknown')`)
3. **Stored Procedure Updates**:
   - Update `reserve_budget_transactional` to accept `tpd_limit` (if applicable), lock daily counter row, verify `actual_tokens + reserved_tokens + estimated_tokens <= tpd_limit`, and increment `reserved_tokens`.
   - Update `settle_usage_transactional` to decrement `reserved_tokens` by estimated tokens and increment `actual_tokens` by actual tokens.
   - Update `release_reservation_transactional` and `expire_reservation_transactional` to refund `reserved_tokens`.

---

## 4. End-to-End Inference Gateway Lifecycle (10 Steps)

1. **Authentication**: Verify Supabase JWT; require active, approved user status.
2. **Model/Provider Validation**: Verify model exists in registry, is `active`, and matches requested provider.
3. **Credential Authorization**: Look up credential in metadata. Verify ownership (`owner_id == user_id`) or verify explicit community-sharing policy (cloud community dispatch is restricted by provider policy).
4. **Token Estimation**: Server-side estimation of input tokens + max output tokens.
5. **Quota & TPD Checks**: Check TPD limit from `provider_quotas` before reserving.
6. **M2.4 Admission Control**: Acquire Redis rate slot, Redis concurrency lease, and PostgreSQL budget/TPD reservation.
7. **Credential Decryption**: Retrieve raw API key just-in-time via `vault.retrieve_decrypted_credential`.
8. **Dispatch Commitment**: Immediately execute `mark_dispatched(reservation_id)` before upstream network call.
9. **Upstream Execution**: Dispatch HTTP request to provider adapter (streaming or non-streaming).
10. **Settlement**:
    - On success: Settle actual tokens and exact microcents, release lease, reconcile Redis token slots.
    - On mid-stream error/timeout: Call `mark_outcome_unknown_transactional` to preserve conservative accounting.
    - On pre-dispatch error: Call `release_reservation` to refund reserved tokens and microcents.

---

## 5. Verification & Testing Matrix

| Test Suite | File | Coverage |
| :--- | :--- | :--- |
| **pgTAP Database Tests** | `supabase/tests/m2_5_tpd_test.sql` | TPD counter columns, constraints, RLS, reservation fields |
| **Token Estimator Tests** | `apps/api/tests/test_gateway_token_estimation.py` | Input message estimation, system prompt, output reservation |
| **SSE Parser Tests** | `apps/api/tests/test_gateway_streaming.py` | Fragmented buffers, multibyte UTF-8 across boundaries, `[DONE]`, usage-only frames |
| **Provider Unit Tests** | `apps/api/tests/test_gateway_gemini.py`<br>`test_gateway_groq.py`<br>`test_gateway_openrouter.py`<br>`test_gateway_cerebras.py` | Request serialization, auth headers, response parsing, error mapping, usage extraction |
| **TPD Enforcement Tests** | `apps/api/tests/test_gateway_tpd.py` | Daily token limits, atomic rejection when daily token cap exceeded, rollover |
| **End-to-End Lifecycle Tests** | `apps/api/tests/test_gateway_lifecycle.py` | Full 10-step dispatch, streaming response, non-streaming response, settlement |
| **Security & Isolation Tests** | `apps/api/tests/test_gateway_security.py` | Key redaction in logs/events, unapproved user rejection, community-share gating |
