-- ==============================================================================
-- RagBait M2.1 Migration: Provider, Model Registry & Dynamic Quota Catalog
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. LLM Providers Registry
-- ------------------------------------------------------------------------------
CREATE TABLE public.llm_providers (
  id text PRIMARY KEY, -- 'gemini', 'groq', 'openrouter', 'cerebras', 'openai', 'xai', 'ollama'
  name text NOT NULL,
  runtime_type text NOT NULL CHECK (runtime_type IN ('cloud_api', 'local_runtime')),
  description text,
  is_enabled boolean NOT NULL DEFAULT true,
  requires_account_scope boolean NOT NULL DEFAULT false, -- True for Groq (org-wide limits)
  account_tier text NOT NULL DEFAULT 'standard' CHECK (account_tier IN ('free', 'pay_as_you_go', 'enterprise', 'standard')),
  billing_status text NOT NULL DEFAULT 'active' CHECK (billing_status IN ('active', 'exhausted', 'delinquent', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER tr_llm_providers_updated_at
BEFORE UPDATE ON public.llm_providers
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 2. Administrator-Controlled LLM Model Registry
-- ------------------------------------------------------------------------------
CREATE TABLE public.llm_models (
  id text PRIMARY KEY, -- 'gemini-3.8-flash', 'openai/gpt-oss-120b', 'qwen/qwen3.8-27b', 'openrouter/free'
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE CASCADE,
  family text NOT NULL, -- 'gemini', 'gpt-oss', 'qwen', 'llama', 'router', etc.
  display_name text NOT NULL,
  availability_status text NOT NULL DEFAULT 'active' CHECK (availability_status IN ('active', 'disabled', 'deprecated', 'shutdown')),
  release_stage text NOT NULL DEFAULT 'ga' CHECK (release_stage IN ('ga', 'preview', 'experimental', 'deprecated')),
  is_routing_alias boolean NOT NULL DEFAULT false,
  alias_target text, -- Dynamic routing target if is_routing_alias = true
  free_tier_eligible boolean NOT NULL DEFAULT false,
  input_cost_per_million_cents integer NOT NULL DEFAULT 0,
  output_cost_per_million_cents integer NOT NULL DEFAULT 0,
  capabilities text[] NOT NULL DEFAULT '{"streaming"}',
  context_window integer NOT NULL,
  max_output_tokens integer NOT NULL,
  modalities_input text[] NOT NULL DEFAULT '{"text"}',
  modalities_output text[] NOT NULL DEFAULT '{"text"}',
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
CREATE INDEX idx_llm_models_family ON public.llm_models(family);
CREATE INDEX idx_llm_models_free_tier ON public.llm_models(free_tier_eligible) WHERE free_tier_eligible = true;

CREATE TRIGGER tr_llm_models_updated_at
BEFORE UPDATE ON public.llm_models
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 3. Dynamic Provider Quota & Provenance Registry
-- ------------------------------------------------------------------------------
CREATE TABLE public.provider_quotas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE CASCADE,
  scope_type text NOT NULL CHECK (scope_type IN ('credential', 'project', 'organization', 'account', 'node')),
  scope_id text NOT NULL, -- 'global', 'org_groq_123', 'gcp_proj_456'
  model_id text REFERENCES public.llm_models(id) ON DELETE CASCADE, -- NULL applies provider-wide
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
CREATE INDEX idx_provider_quotas_model ON public.provider_quotas(model_id) WHERE model_id IS NOT NULL;

CREATE TRIGGER tr_provider_quotas_updated_at
BEFORE UPDATE ON public.provider_quotas
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 4. Permissions & Row Level Security (RLS)
-- ------------------------------------------------------------------------------
ALTER TABLE public.llm_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_quotas ENABLE ROW LEVEL SECURITY;

-- Read policies: Any authenticated user can read providers, models, and quotas
CREATE POLICY llm_providers_select ON public.llm_providers
  FOR SELECT TO authenticated
  USING (true);

CREATE POLICY llm_models_select ON public.llm_models
  FOR SELECT TO authenticated
  USING (true);

CREATE POLICY provider_quotas_select ON public.provider_quotas
  FOR SELECT TO authenticated
  USING (true);

-- Mutation policies: Only administrators can modify registries
CREATE POLICY llm_providers_admin_mutate ON public.llm_providers
  FOR ALL TO authenticated
  USING (app_private.is_admin(auth.uid()))
  WITH CHECK (app_private.is_admin(auth.uid()));

CREATE POLICY llm_models_admin_mutate ON public.llm_models
  FOR ALL TO authenticated
  USING (app_private.is_admin(auth.uid()))
  WITH CHECK (app_private.is_admin(auth.uid()));

CREATE POLICY provider_quotas_admin_mutate ON public.provider_quotas
  FOR ALL TO authenticated
  USING (app_private.is_admin(auth.uid()))
  WITH CHECK (app_private.is_admin(auth.uid()));

-- Restrict direct table mutation from authenticated users
REVOKE INSERT, UPDATE, DELETE ON public.llm_providers FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.llm_models FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.provider_quotas FROM authenticated;

-- Grant select to authenticated role
GRANT SELECT ON public.llm_providers TO authenticated;
GRANT SELECT ON public.llm_models TO authenticated;
GRANT SELECT ON public.provider_quotas TO authenticated;

-- ------------------------------------------------------------------------------
-- 5. Seed Data (Modern Verified Models & Quota Provenance)
-- ------------------------------------------------------------------------------

-- Seed Providers
INSERT INTO public.llm_providers (id, name, runtime_type, description, requires_account_scope, account_tier, billing_status)
VALUES
  ('gemini', 'Google Gemini', 'cloud_api', 'Google DeepMind multimodal AI API', false, 'pay_as_you_go', 'active'),
  ('groq', 'GroqCloud', 'cloud_api', 'Groq LPU ultra-fast open-weight inference', true, 'free', 'active'),
  ('openrouter', 'OpenRouter', 'cloud_api', 'Unified multi-provider AI model aggregator', false, 'pay_as_you_go', 'active'),
  ('cerebras', 'Cerebras', 'cloud_api', 'Cerebras wafer-scale engine inference', false, 'free', 'active'),
  ('openai', 'OpenAI', 'cloud_api', 'OpenAI project-scoped service accounts', false, 'pay_as_you_go', 'active'),
  ('xai', 'xAI / Grok', 'cloud_api', 'xAI developer platform', false, 'pay_as_you_go', 'active'),
  ('ollama', 'Ollama Local Runtime', 'local_runtime', 'Student-contributed local computer compute', false, 'standard', 'active')
ON CONFLICT (id) DO UPDATE SET
  name = EXCLUDED.name,
  runtime_type = EXCLUDED.runtime_type,
  requires_account_scope = EXCLUDED.requires_account_scope,
  account_tier = EXCLUDED.account_tier,
  billing_status = EXCLUDED.billing_status;

-- Seed Models
INSERT INTO public.llm_models (
  id, provider_id, family, display_name, availability_status, release_stage,
  is_routing_alias, alias_target, free_tier_eligible,
  input_cost_per_million_cents, output_cost_per_million_cents,
  capabilities, context_window, max_output_tokens, modalities_input, modalities_output,
  source_documentation
)
VALUES
  (
    'gemini-3.8-flash', 'gemini', 'gemini', 'Google Gemini 3.8 Flash', 'active', 'ga',
    false, null, true,
    15, 60,
    ARRAY['streaming', 'tools', 'vision'], 1048576, 8192, ARRAY['text', 'image'], ARRAY['text'],
    'https://ai.google.dev/pricing'
  ),
  (
    'gemini-3.5-flash-lite', 'gemini', 'gemini', 'Google Gemini 3.5 Flash-Lite', 'active', 'ga',
    false, null, true,
    8, 30,
    ARRAY['streaming', 'tools'], 1048576, 8192, ARRAY['text'], ARRAY['text'],
    'https://ai.google.dev/pricing'
  ),
  (
    'openai/gpt-oss-120b', 'groq', 'gpt-oss', 'OpenAI GPT-OSS 120B (Groq)', 'active', 'ga',
    false, null, true,
    15, 60,
    ARRAY['streaming', 'reasoning', 'tools'], 131072, 65536, ARRAY['text'], ARRAY['text'],
    'https://console.groq.com/docs/models'
  ),
  (
    'openai/gpt-oss-20b', 'groq', 'gpt-oss', 'OpenAI GPT-OSS 20B (Groq)', 'active', 'ga',
    false, null, true,
    8, 30,
    ARRAY['streaming', 'reasoning', 'tools'], 131072, 65536, ARRAY['text'], ARRAY['text'],
    'https://console.groq.com/docs/models'
  ),
  (
    'qwen/qwen3.8-27b', 'groq', 'qwen', 'Qwen 3.8 27B (Groq Preview)', 'active', 'preview',
    false, null, true,
    20, 60,
    ARRAY['streaming', 'vision', 'tools', 'reasoning'], 131072, 32768, ARRAY['text', 'image'], ARRAY['text'],
    'https://console.groq.com/docs/models'
  ),
  (
    'openrouter/free', 'openrouter', 'router', 'OpenRouter Free Router (Dynamic Alias)', 'active', 'ga',
    true, null, true,
    0, 0,
    ARRAY['streaming'], 131072, 8192, ARRAY['text'], ARRAY['text'],
    'https://openrouter.ai/docs/quickstart'
  ),
  (
    'openrouter/meta-llama/llama-3.3-70b-instruct:free', 'openrouter', 'llama', 'Meta Llama 3.3 70B Instruct (OpenRouter Free)', 'active', 'ga',
    false, null, true,
    0, 0,
    ARRAY['streaming'], 131072, 8192, ARRAY['text'], ARRAY['text'],
    'https://openrouter.ai/models/meta-llama/llama-3.3-70b-instruct:free'
  ),
  (
    'cerebras/gpt-oss-120b', 'cerebras', 'gpt-oss', 'Cerebras GPT-OSS 120B', 'active', 'ga',
    false, null, true,
    85, 120,
    ARRAY['streaming', 'reasoning'], 131072, 8192, ARRAY['text'], ARRAY['text'],
    'https://cerebras.ai/pricing'
  ),
  (
    'gpt-4o', 'openai', 'gpt-4o', 'OpenAI GPT-4o (Project Service Account)', 'active', 'ga',
    false, null, false,
    250, 1000,
    ARRAY['streaming', 'vision', 'tools', 'json_mode'], 128000, 16384, ARRAY['text', 'image'], ARRAY['text'],
    'https://platform.openai.com/docs/models/gpt-4o'
  ),
  (
    'qwen2.5:7b', 'ollama', 'qwen', 'Qwen 2.5 7B (Local Node)', 'active', 'ga',
    false, null, true,
    0, 0,
    ARRAY['streaming'], 32768, 4096, ARRAY['text'], ARRAY['text'],
    'https://ollama.com/library/qwen2.5'
  )
ON CONFLICT (id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  availability_status = EXCLUDED.availability_status,
  release_stage = EXCLUDED.release_stage,
  is_routing_alias = EXCLUDED.is_routing_alias,
  alias_target = EXCLUDED.alias_target,
  free_tier_eligible = EXCLUDED.free_tier_eligible,
  capabilities = EXCLUDED.capabilities,
  context_window = EXCLUDED.context_window,
  max_output_tokens = EXCLUDED.max_output_tokens,
  source_documentation = EXCLUDED.source_documentation;

-- Seed Quotas
INSERT INTO public.provider_quotas (
  provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
  source_documentation, confidence, provenance
)
VALUES
  -- Gemini 3.8 Flash Free Quota
  ('gemini', 'project', 'global', 'gemini-3.8-flash', 'rpm', 15, 'https://ai.google.dev/pricing', 'high', 'provider_published'),
  ('gemini', 'project', 'global', 'gemini-3.8-flash', 'tpm', 1000000, 'https://ai.google.dev/pricing', 'high', 'provider_published'),
  ('gemini', 'project', 'global', 'gemini-3.8-flash', 'rpd', 1500, 'https://ai.google.dev/pricing', 'high', 'provider_published'),
  -- Groq Organization Free Envelope
  ('groq', 'organization', 'global', null, 'rpm', 30, 'https://console.groq.com/docs/rate-limits', 'high', 'provider_published'),
  ('groq', 'organization', 'global', null, 'rpd', 1000, 'https://console.groq.com/docs/rate-limits', 'high', 'provider_published'),
  -- Groq GPT-OSS 120B Model Quota
  ('groq', 'organization', 'global', 'openai/gpt-oss-120b', 'tpm', 6000, 'https://console.groq.com/docs/rate-limits', 'high', 'provider_published'),
  -- OpenRouter Free Account Quotas
  ('openrouter', 'account', 'global', 'openrouter/free', 'rpm', 20, 'https://openrouter.ai/docs/quickstart', 'high', 'provider_published'),
  ('openrouter', 'account', 'global', 'openrouter/free', 'rpd', 50, 'https://openrouter.ai/docs/quickstart', 'high', 'provider_published'),
  ('openrouter', 'account', 'global', 'openrouter/meta-llama/llama-3.3-70b-instruct:free', 'rpm', 20, 'https://openrouter.ai/docs/quickstart', 'high', 'provider_published'),
  ('openrouter', 'account', 'global', 'openrouter/meta-llama/llama-3.3-70b-instruct:free', 'rpd', 50, 'https://openrouter.ai/docs/quickstart', 'high', 'provider_published'),
  -- Cerebras Free Trial Quota
  ('cerebras', 'organization', 'global', null, 'rpm', 30, 'https://cerebras.ai/pricing', 'high', 'provider_published')
ON CONFLICT (provider_id, scope_id, model_id, limit_type) DO UPDATE SET
  limit_value = EXCLUDED.limit_value,
  source_documentation = EXCLUDED.source_documentation,
  confidence = EXCLUDED.confidence,
  provenance = EXCLUDED.provenance;

-- ------------------------------------------------------------------------------
-- 6. Audit Logs Foreign Key Integrity (Preserve Immutable Audit History)
-- ------------------------------------------------------------------------------
ALTER TABLE public.audit_logs
  DROP CONSTRAINT IF EXISTS audit_logs_actor_id_fkey,
  ADD CONSTRAINT audit_logs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.profiles(id) ON DELETE SET NULL;
