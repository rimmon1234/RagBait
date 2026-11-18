# RagBait M2 — Database Schema Specification

## 1. Schema Overview

RagBait M2 introduces unified database infrastructure for managing cloud provider credentials, dynamic quota scopes, local inference nodes, model definitions, usage ledgers, and privacy preferences.

All database modifications follow the project's engineering principles:
- **Additive & Versioned**: Placed in migration `20260201000000_ragbait_m2_llm_gateway.sql`. The existing initial migration is preserved unchanged.
- **Strict Separation of Concerns**: Public metadata is cleanly separated from private encrypted secrets (`private` schema).
- **Least-Privilege Vault Isolation**: `private.api_key_secrets` is accessible strictly to a dedicated database role `ragbait_vault_service` using a separate connection pool.
- **Row Level Security (RLS)**: Enforced across all new public tables.
- **Privacy by Default**: Prompt text and generated completions are excluded from tracking and audit tables.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              PUBLIC SCHEMA (RLS ACTIVE)                         │
├────────────────────────┬─────────────────────────┬──────────────────────────────┤
│ llm_providers          │ api_key_metadata        │ inference_nodes              │
│ llm_models             │ provider_quotas         │ inference_node_models        │
│ user_ai_preferences    │ api_key_usage           │ inference_node_usage         │
│ llm_requests           │                         │                              │
└────────────────────────┴─────────────────────────┴──────────────────────────────┘
                                      │ (1:1 Foreign Key)
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                  PRIVATE SCHEMA (RESTRICTED TO ragbait_vault_service)           │
├─────────────────────────────────────────────────────────────────────────────────┤
│ private.api_key_secrets (encrypted_dek, dek_auth_tag, ciphertext, nonce, v)     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Table Definitions

### 2.1 Provider, Model & Quota Registries

#### `public.llm_providers`
Stores supported cloud providers and local runtimes.
```sql
CREATE TABLE public.llm_providers (
  id text PRIMARY KEY, -- 'gemini', 'groq', 'openrouter', 'cerebras', 'openai', 'grok', 'ollama'
  name text NOT NULL,
  provider_type text NOT NULL CHECK (provider_type IN ('cloud_free', 'cloud_paid', 'local_runtime')),
  is_enabled boolean NOT NULL DEFAULT true,
  requires_account_scope boolean NOT NULL DEFAULT false, -- True for Groq (org-level quota)
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

#### `public.llm_models`
Exposes administrator-controlled capabilities, context limits, lifecycle status, and cost categories.
```sql
CREATE TABLE public.llm_models (
  id text PRIMARY KEY, -- 'gemini-3.8-flash', 'openai/gpt-oss-120b', 'qwen2.5:7b'
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE CASCADE,
  family text NOT NULL, -- 'gemini', 'gpt-oss', 'qwen', 'llama', etc.
  display_name text NOT NULL,
  availability_status text NOT NULL DEFAULT 'active' CHECK (availability_status IN ('active', 'disabled', 'deprecated', 'shutdown')),
  release_stage text NOT NULL DEFAULT 'ga' CHECK (release_stage IN ('ga', 'preview', 'experimental', 'deprecated')),
  capabilities text[] NOT NULL DEFAULT '{"streaming"}',
  context_window integer NOT NULL,
  max_output_tokens integer NOT NULL,
  modalities_input text[] NOT NULL DEFAULT '{"text"}',
  modalities_output text[] NOT NULL DEFAULT '{"text"}',
  pricing_category text NOT NULL CHECK (pricing_category IN ('free_tier', 'paid', 'local')),
  source_documentation text,
  last_verification_time timestamptz NOT NULL DEFAULT now(),
  shutdown_at timestamptz,
  replacement_model_id text REFERENCES public.llm_models(id) ON DELETE SET NULL,
  is_active boolean GENERATED ALWAYS AS (availability_status = 'active') STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_models_provider ON public.llm_models(provider_id);
CREATE INDEX idx_llm_models_availability ON public.llm_models(availability_status);
CREATE INDEX idx_llm_models_pricing ON public.llm_models(pricing_category);
```

#### `public.provider_quotas`
Dynamic, verified quota tracking per provider, organization/project scope, and model.
```sql
CREATE TABLE public.provider_quotas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE CASCADE,
  scope_type text NOT NULL CHECK (scope_type IN ('credential', 'project', 'organization', 'account', 'node')),
  scope_id text NOT NULL, -- e.g. 'org_groq_lab', 'gcp_proj_123', 'openrouter_acc_456', or 'global'
  model_id text REFERENCES public.llm_models(id) ON DELETE CASCADE, -- Specific model ID or NULL for provider-wide
  limit_type text NOT NULL CHECK (limit_type IN ('rpm', 'tpm', 'rpd', 'tpd', 'concurrency', 'credit_cents')),
  limit_value bigint NOT NULL,
  source_documentation text,
  last_verification_time timestamptz NOT NULL DEFAULT now(),
  confidence text NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high', 'medium', 'low')),
  provenance text NOT NULL DEFAULT 'provider_published' CHECK (provenance IN ('account_confirmed', 'provider_published', 'observed', 'estimated', 'unknown')),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_provider_quota_scope UNIQUE (provider_id, scope_id, model_id, limit_type)
);

CREATE INDEX idx_provider_quotas_lookup ON public.provider_quotas(provider_id, scope_id);
```

---

### 2.2 Credential Vault Tables

#### `public.api_key_metadata`
Stores non-sensitive credential configuration, quotas, and consent.
```sql
CREATE TABLE public.api_key_metadata (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  provider_id text NOT NULL REFERENCES public.llm_providers(id),
  nickname text NOT NULL,
  masked_key text NOT NULL, -- 'gsk_...9a2f'
  key_fingerprint text NOT NULL, -- HMAC-SHA256(pepper, raw_key)
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'revoked', 'rate_limited', 'quota_exhausted')),
  sharing_scope text NOT NULL DEFAULT 'owner_only' CHECK (sharing_scope IN ('owner_only', 'community_shared')),
  shared_consent boolean NOT NULL DEFAULT false,
  sharing_consent_timestamp timestamptz,
  account_scope_id text, -- Identifier for shared quota grouping (e.g. Groq org)
  daily_request_limit integer NOT NULL DEFAULT 500,
  monthly_request_limit integer NOT NULL DEFAULT 15000,
  daily_spending_limit_cents integer, -- Optional budget cap in cents
  monthly_spending_limit_cents integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_provider_fingerprint UNIQUE (provider_id, key_fingerprint)
);

CREATE INDEX idx_api_key_owner ON public.api_key_metadata(owner_id);
CREATE INDEX idx_api_key_provider_status ON public.api_key_metadata(provider_id, status);
CREATE INDEX idx_api_key_account_scope ON public.api_key_metadata(account_scope_id) WHERE account_scope_id IS NOT NULL;
```

#### `private.api_key_secrets`
Stores encrypted payload material. Completely isolated from public schemas and restricted to `ragbait_vault_service`.
```sql
CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

-- Dedicated least-privilege capability role for the credential vault (NOLOGIN)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_service') THEN
    CREATE ROLE ragbait_vault_service WITH NOLOGIN NOBYPASSRLS;
  ELSE
    ALTER ROLE ragbait_vault_service WITH NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA private TO ragbait_vault_service;

CREATE TABLE private.api_key_secrets (
  id uuid PRIMARY KEY REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  encrypted_dek bytea NOT NULL, -- Ephemeral DEK wrapped with Master KEK via AES-256-GCM
  dek_nonce bytea NOT NULL,     -- 96-bit AES-GCM IV for wrapped DEK
  dek_auth_tag bytea NOT NULL,  -- 16-byte authentication tag for wrapped DEK
  ciphertext bytea NOT NULL,    -- AES-256-GCM ciphertext of raw key + 16-byte auth tag
  nonce bytea NOT NULL,         -- 96-bit AES-GCM IV for raw key
  data_encryption_version integer NOT NULL DEFAULT 1,
  kek_version integer NOT NULL DEFAULT 1,
  wrapping_context_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON TABLE private.api_key_secrets FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE private.api_key_secrets TO ragbait_vault_service;
```

---

### 2.3 Local Inference Node Tables

#### `public.inference_nodes`
Tracks contributor machines connected via the RagBait Node Agent using **Ed25519 asymmetric public keys**.
```sql
CREATE TABLE public.inference_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  node_name text NOT NULL,
  runtime text NOT NULL CHECK (runtime IN ('ollama', 'vllm', 'lmstudio')),
  status text NOT NULL DEFAULT 'offline' CHECK (status IN ('online', 'offline', 'busy', 'paused', 'revoked')),
  is_revoked boolean NOT NULL DEFAULT false,
  last_heartbeat_at timestamptz,
  session_token_expires_at timestamptz, -- Expiration of active WSS challenge session
  ed25519_public_key text NOT NULL, -- Base64-encoded Ed25519 public key (private key never touches server)
  max_concurrency integer NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 8),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_inference_nodes_owner ON public.inference_nodes(owner_id);
CREATE INDEX idx_inference_nodes_status ON public.inference_nodes(status);
```

#### `public.inference_node_models`
Discovered local models and contributor sharing opt-in preferences.
```sql
CREATE TABLE public.inference_node_models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL REFERENCES public.inference_nodes(id) ON DELETE CASCADE,
  model_tag text NOT NULL, -- 'qwen2.5:7b', 'llama3.1:8b'
  is_shared boolean NOT NULL DEFAULT false, -- Mode A (false) vs Mode B (true)
  max_model_concurrency integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_node_model UNIQUE (node_id, model_tag)
);

CREATE INDEX idx_node_models_shared ON public.inference_node_models(model_tag, is_shared) WHERE is_shared = true;
```

---

### 2.4 User Preferences & Usage Accounting

#### `public.user_ai_preferences`
Tracks requester privacy settings and community node execution consent.
```sql
CREATE TABLE public.user_ai_preferences (
  user_id uuid PRIMARY KEY REFERENCES public.profiles(id) ON DELETE CASCADE,
  allow_community_nodes boolean NOT NULL DEFAULT false, -- Dual-consent requirement
  preferred_model_id text REFERENCES public.llm_models(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

#### `public.llm_requests`
Audit record and persisted state machine for every inference dispatch. Note: **Zero prompts or completion text stored**.
```sql
CREATE TABLE public.llm_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE SET NULL,
  resource_type text NOT NULL CHECK (resource_type IN ('cloud_api', 'local_node')),
  credential_id uuid REFERENCES public.api_key_metadata(id) ON DELETE SET NULL,
  node_id uuid REFERENCES public.inference_nodes(id) ON DELETE SET NULL,
  provider_id text NOT NULL,
  model text NOT NULL, -- Kept as plain text to preserve history even if model is deprecated
  input_tokens integer NOT NULL DEFAULT 0,
  output_tokens integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  estimated_cost_cents integer NOT NULL DEFAULT 0,
  latency_ms integer NOT NULL DEFAULT 0,
  status text NOT NULL CHECK (status IN ('queued', 'claimed', 'sent', 'agent_accepted', 'running', 'success', 'failed', 'timeout', 'rate_limited', 'cancelled')),
  error_code text,
  was_fallback boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_requests_user ON public.llm_requests(user_id, created_at DESC);
CREATE INDEX idx_llm_requests_credential ON public.llm_requests(credential_id, created_at DESC) WHERE credential_id IS NOT NULL;
CREATE INDEX idx_llm_requests_node ON public.llm_requests(node_id, created_at DESC) WHERE node_id IS NOT NULL;
CREATE INDEX idx_llm_requests_status ON public.llm_requests(status);
```

#### `public.api_key_usage` & `public.inference_node_usage`
Aggregated daily rollups for contributor dashboards and spending caps.
```sql
CREATE TABLE public.api_key_usage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  credential_id uuid NOT NULL REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  usage_date date NOT NULL DEFAULT CURRENT_DATE,
  request_count integer NOT NULL DEFAULT 0,
  total_input_tokens bigint NOT NULL DEFAULT 0,
  total_output_tokens bigint NOT NULL DEFAULT 0,
  total_cost_cents integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_credential_daily_usage UNIQUE (credential_id, usage_date)
);

CREATE TABLE public.inference_node_usage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL REFERENCES public.inference_nodes(id) ON DELETE CASCADE,
  usage_date date NOT NULL DEFAULT CURRENT_DATE,
  job_count integer NOT NULL DEFAULT 0,
  total_duration_seconds bigint NOT NULL DEFAULT 0,
  total_tokens_generated bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_node_daily_usage UNIQUE (node_id, usage_date)
);
```

---

## 3. Row Level Security (RLS) Policies

```sql
ALTER TABLE public.llm_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_key_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inference_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inference_node_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_ai_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_key_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inference_node_usage ENABLE ROW LEVEL SECURITY;

-- 1. Providers, Models & Quotas: Readable by all authenticated users
CREATE POLICY llm_providers_select ON public.llm_providers FOR SELECT TO authenticated USING (true);
CREATE POLICY llm_models_select ON public.llm_models FOR SELECT TO authenticated USING (true);
CREATE POLICY provider_quotas_select ON public.provider_quotas FOR SELECT TO authenticated USING (true);

-- 2. API Key Metadata: Users can only see and manage their own credentials
CREATE POLICY api_key_metadata_select ON public.api_key_metadata
  FOR SELECT TO authenticated
  USING (auth.uid() = owner_id);

CREATE POLICY api_key_metadata_insert ON public.api_key_metadata
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = owner_id);

CREATE POLICY api_key_metadata_update ON public.api_key_metadata
  FOR UPDATE TO authenticated
  USING (auth.uid() = owner_id)
  WITH CHECK (auth.uid() = owner_id);

CREATE POLICY api_key_metadata_delete ON public.api_key_metadata
  FOR DELETE TO authenticated
  USING (auth.uid() = owner_id);

-- 3. Inference Nodes: Owner-only management
CREATE POLICY inference_nodes_own ON public.inference_nodes
  FOR ALL TO authenticated
  USING (auth.uid() = owner_id)
  WITH CHECK (auth.uid() = owner_id);

-- 4. Node Models: Accessible if user owns the parent node
CREATE POLICY node_models_own ON public.inference_node_models
  FOR ALL TO authenticated
  USING (EXISTS (SELECT 1 FROM public.inference_nodes n WHERE n.id = node_id AND n.owner_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.inference_nodes n WHERE n.id = node_id AND n.owner_id = auth.uid()));

-- 5. AI Preferences: Own record only
CREATE POLICY user_ai_preferences_own ON public.user_ai_preferences
  FOR ALL TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- 6. Requests: Users see own dispatches; Admins see aggregate logs
CREATE POLICY llm_requests_select ON public.llm_requests
  FOR SELECT TO authenticated
  USING (auth.uid() = user_id OR app_private.is_admin(auth.uid()));

-- 7. Usage: Owners see own credential/node rollups; Admins see all rollups
CREATE POLICY api_key_usage_select ON public.api_key_usage
  FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM public.api_key_metadata k WHERE k.id = credential_id AND k.owner_id = auth.uid()) OR app_private.is_admin(auth.uid()));

CREATE POLICY node_usage_select ON public.inference_node_usage
  FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM public.inference_nodes n WHERE n.id = node_id AND n.owner_id = auth.uid()) OR app_private.is_admin(auth.uid()));
```
