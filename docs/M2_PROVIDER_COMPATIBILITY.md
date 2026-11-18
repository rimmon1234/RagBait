# RagBait M2 — Provider Compatibility, Quota Governance & Administrator Model Registry

## 1. Provider Eligibility Architecture

RagBait enforces an **eligibility-first** credential ingestion model. Because AI providers enforce varying terms of service, data-privacy policies, quota scopes, and security boundaries, the gateway never blindly accepts personal API keys. 

Every credential submitted by a user or administrator is evaluated against an immutable **Provider Eligibility Matrix** before it can be stored, verified, or routed.

```
Credential Submission
        │
        ▼
┌────────────────────────────────────────────────────────┐
│             Provider Policy Enforcement Engine         │
├────────────────────────────────────────────────────────┤
│ 1. Provider & Credential Format Match                  │
│ 2. Terms of Service & Key Sharing Compliance Check    │
│ 3. Key Permissions & Capability Probe                  │
│ 4. Organization / Project Quota Scope Detection        │
│ 5. Contributor Explicit Sharing Consent                │
└────────────────────────────────────────────────────────┘
        │
        ├─────────────────────────────┬───────────────────────────┐
        ▼                             ▼                           ▼
[Eligible Shared Pool]      [Eligible Owner-Only]         [REJECTED]
(Meets sharing terms &      (Valid personal key,        (Violates ToS,
 contributor consented)      not shareable by ToS)       unsupported key)
```

---

## 2. Dynamic Provider Quota & Provenance Model

Provider quotas are **dynamic operational attributes**, not universal or permanent constants. They depend on account tiers, cloud project associations, organization memberships, and specific models. Adding multiple API keys from the same provider account **does not increase throughput** when quotas are enforced at the organization or project level.

### 2.1 Provenance Classification Rules
RagBait enforces strict criteria for quota provenance. **A quota is NEVER marked `account_confirmed` merely because it appears in public documentation.**

- `account_confirmed`: Verified directly against an authenticated user account via API response headers (e.g. `x-ratelimit-*`), account dashboard inspection, or account-specific rate-limit probes.
- `provider_published`: Extracted from official published documentation or pricing pages.
- `observed`: Empirically recorded from operational gateway metrics under active load.
- `estimated`: Derived from comparable tiers or community benchmarks when official figures are absent.
- `unknown`: Default initial state before verification.

### 2.2 Dynamic Quota Record Schema (`public.provider_quotas`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | uuid | Primary key identifier |
| `provider_id` | text | Unique provider identifier (`gemini`, `groq`, `openrouter`, `cerebras`, `openai`, `xai`, `ollama`) |
| `scope_type` | text | Quota boundary: `'credential'`, `'project'`, `'organization'`, `'account'`, `'node'` |
| `scope_id` | text | Identifier of the quota entity (e.g. Groq Org ID, GCP Project ID, OpenRouter Account ID, or `'global'`) |
| `model_id` | text | Specific model (or `NULL` if quota applies across all models) |
| `limit_type` | text | Metric: `'rpm'`, `'tpm'`, `'rpd'`, `'tpd'`, `'concurrency'`, `'credit_cents'` |
| `limit_value` | bigint | Numeric threshold enforced by the provider |
| `source_documentation` | text | URL / documentation reference or header specification |
| `last_verification_time` | timestamptz | When this quota was last verified via probe or response headers |
| `confidence` | text | Assessment confidence: `'high'`, `'medium'`, `'low'` |
| `provenance` | text | Provenance category: `'account_confirmed'`, `'provider_published'`, `'observed'`, `'estimated'`, `'unknown'` |
| `is_active` | boolean | Whether this quota rule is currently enforced in admission control |

---

## 3. Administrator-Controlled Model Registry & Catalogue

To prevent disruption when providers deprecate or shut down models, RagBait implements a **dynamic, administrator-controlled model registry**. Models are never hardcoded as permanent production constants.

### 3.1 Model Lifecycle States & Disablement Policy
Models in `public.llm_models` transition through four lifecycle states:
- `active`: Eligible for new dispatches and user selection.
- `disabled`: Temporarily deactivated by administrator (e.g. during provider degradation); new requests rejected with `503 Model Disabled`.
- `deprecated`: Maintained for existing sessions, but hidden from default model pickers. A `replacement_model_id` is advertised.
- `shutdown`: Upstream provider has terminated the model. Requests return `410 Gone (Model Retired)`. **Crucially, disabling or shutting down a model NEVER deletes historical usage accounting records in `public.llm_requests`.**

### 3.2 Provider Model Discovery & Verification Pipeline
1. **Automated Provider Discovery**:
   - Background worker executes periodic capability probes against provider catalogue endpoints (e.g. `GET https://api.openai.com/v1/models`, `GET https://api.groq.com/openai/v1/models`, `GET https://openrouter.ai/api/v1/models`).
   - Discovers new model identifiers, context lengths, and deprecation flags emitted in HTTP response headers.
2. **Scheduled Verification Checks**:
   - A daily cron task validates active models against provider endpoints using a non-mutating single-token ping (`max_tokens: 1`).
   - If an endpoint returns HTTP `404 Model Not Found` or `410 Gone`, the gateway marks the model `shutdown` and alerts administrators.
3. **Manual Administrator Verification**:
   - Super Admins can add, update, rebadge, or disable models via `/api/v1/admin/models` without server restarts or database redeployments.

---

## 4. Provider-by-Provider Analysis & Active Catalogue

### 4.1 Google Gemini (Google AI Studio & Vertex AI)
- **Authentication**: Secret key passed via HTTP header `x-goog-api-key`.
- **Catalogue & Deprecation Status**:
  - **Retired Models (REMOVED FROM SEED DATA)**:
    - `gemini-1.5-flash`, `gemini-1.5-pro`, `gemini-1.5-flash-8b`: **Shut down and retired** by Google.
    - `gemini-2.0-flash-exp`: **Retired experimental variant**.
  - **Currently Supported Production Alternatives**:
    - `gemini-3.8-flash`: Current flagship high-throughput multimodal model (Context: 1,048,576 tokens; Output: 8,192 tokens; Release stage: `ga`).
    - `gemini-3.5-flash-lite`: Ultra-low latency, cost-efficient model (Context: 1,048,576 tokens; Output: 8,192 tokens; Release stage: `ga`).
- **Quota Scopes & Verified Limits** (Source: [Google AI Studio Pricing](https://ai.google.dev/pricing)):
  - Enforced at the **Google Cloud Project** level, sharing project quotas across all keys.
  - Verified Free Tier Limits: 15 RPM / 1,000,000 TPM / 1,500 RPD (Provenance: `provider_published`).
- **Mandatory Privacy Disclosure**:
  > [!WARNING]
  > Google AI Studio Free Tier prompts and responses may be reviewed by human reviewers and used to train Google models. The UI must display an explicit disclosure before dispatch.

### 4.2 GroqCloud
- **Authentication**: Bearer token starting with `gsk_`.
- **Quota Scope**: Strictly **Organization-wide**. All keys in the same organization share quotas.
- **Catalogue & Deprecation Status**:
  - **Retired Models (REMOVED FROM SEED DATA)**:
    - `llama-3.3-70b-versatile`: Shut down on free tiers.
    - `llama-3.1-8b-instant`: Shut down on developer tiers.
  - **Currently Supported Production Alternatives**:
    - `openai/gpt-oss-120b`: High-capacity open-weight reasoning model (~500 tps; Context: 131,072; Max Output: 65,536; Provenance: `provider_published`).
    - `openai/gpt-oss-20b`: Ultra-fast reasoning model (~1000 tps; Context: 131,072; Max Output: 65,536; Provenance: `provider_published`).
    - `qwen/qwen3.8-27b`: Advanced tool use and reasoning model (Context: 131,072; Max Output: 32,768; Provenance: `provider_published`).
- **Verified Base Quotas**:
  - Free Tier Envelope: 30 RPM / 1,000 RPD organization-wide ceiling. Cooldown applied to `account_scope_id` upon HTTP 429.

### 4.3 OpenRouter
- **Authentication**: Bearer token starting with `sk-or-v1-`.
- **Catalogue & Quota Mechanics**:
  - Models ending in `:free` (e.g. `openrouter/free` router or open-weight free tiers).
  - **Free Accounts (<$10 lifetime credit purchase)**: **20 RPM / 50 RPD** (Provenance: `provider_published`).
  - **Upgraded Accounts ($10+ lifetime credit purchase)**: **20 RPM / 1,000 RPD** (Provenance: `provider_published`).
  - Real-time headers: `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`. Dynamically parsed as `account_confirmed`.

### 4.4 Cerebras
- **Authentication**: Bearer token starting with `csk-`.
- **Catalogue & Dual-Bucket Architecture**:
  - `cerebras/gpt-oss-120b`, `cerebras/llama-3.1-8b`.
  - Rate limits enforced on two buckets: *Uncached TPM* (cache misses) vs *Total TPM*.
  - Free Trial: 30 RPM, introductory token limits (Provenance: `provider_published`). Developer Tier unlocked with $10 deposit.

### 4.5 OpenAI (Strict Compliance Architecture)
- **Policy**: Personal key pooling strictly forbidden by OpenAI ToS §2.
- **Multi-Stage Verification**:
  1. Regex format check: `^sk-proj-[A-Za-z0-9_-]{48,}$` (rejecting `org-...` and legacy `sk-...`).
  2. Non-mutating verification probe: `GET https://api.openai.com/v1/models`.
  3. Header inspection: `openai-organization` and `openai-project` verified to belong to an authorized project-scoped service account.

---

## 5. Local & Open-Weight LLM Infrastructure

### 5.1 Local Inference Runtime Support

| Runtime | Default Port | Connection Protocol | Native Features | RagBait Phase |
| :--- | :--- | :--- | :--- | :--- |
| **Ollama** | 11434 | WSS Reverse Relay | Local model tagging, quantized GGUF weights, CPU/GPU auto-offload | **Phase M2.7 (Private) & M2.8 (Shared)** |
| **vLLM** | 8000 | WSS Reverse Relay | Continuous batching, PagedAttention, high concurrent throughput | **Phase M2.8** |
| **LM Studio** | 1234 | WSS Reverse Relay | Desktop developer server, OpenAI API schema compatibility | **Phase M2.8** |

### 5.2 Two Explicit Access Modes
- **Mode A (Private Local Node)**: Contributor-owned; exclusive to contributor session; zero community exposure.
- **Mode B (Shared Community Node)**: Contributor opts in specific models; dual-consent gate requires requester opt-in (`user_ai_preferences.allow_community_nodes = true`). Prompt processed in volatile memory.
