-- ==============================================================================
-- RagBait M2.1 Registry & Security pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(22);

-- 1. Table existence
SELECT has_table('public', 'llm_providers', 'public.llm_providers should exist');
SELECT has_table('public', 'llm_models', 'public.llm_models should exist');
SELECT has_table('public', 'provider_quotas', 'public.provider_quotas should exist');

-- 2. Row Level Security active
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'llm_providers' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on llm_providers'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'llm_models' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on llm_models'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'provider_quotas' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on provider_quotas'
);

-- 3. SELECT permissions for authenticated role
SELECT is(
  has_table_privilege('authenticated', 'public.llm_providers', 'SELECT'),
  true,
  'authenticated role should have SELECT on llm_providers'
);
SELECT is(
  has_table_privilege('authenticated', 'public.llm_models', 'SELECT'),
  true,
  'authenticated role should have SELECT on llm_models'
);
SELECT is(
  has_table_privilege('authenticated', 'public.provider_quotas', 'SELECT'),
  true,
  'authenticated role should have SELECT on provider_quotas'
);

-- 4. Mutation permissions revoked from authenticated role
SELECT is(
  has_table_privilege('authenticated', 'public.llm_providers', 'INSERT'),
  false,
  'authenticated role must NOT have INSERT on llm_providers'
);
SELECT is(
  has_table_privilege('authenticated', 'public.llm_models', 'INSERT'),
  false,
  'authenticated role must NOT have INSERT on llm_models'
);
SELECT is(
  has_table_privilege('authenticated', 'public.provider_quotas', 'INSERT'),
  false,
  'authenticated role must NOT have INSERT on provider_quotas'
);
SELECT is(
  has_table_privilege('authenticated', 'public.llm_models', 'UPDATE'),
  false,
  'authenticated role must NOT have UPDATE on llm_models'
);
SELECT is(
  has_table_privilege('authenticated', 'public.llm_models', 'DELETE'),
  false,
  'authenticated role must NOT have DELETE on llm_models'
);

-- 5. Foreign keys & Constraints
SELECT fk_ok(
  'public', 'llm_models', 'provider_id',
  'public', 'llm_providers', 'id'
);
SELECT fk_ok(
  'public', 'provider_quotas', 'provider_id',
  'public', 'llm_providers', 'id'
);
SELECT col_is_unique(
  'public', 'provider_quotas', ARRAY['provider_id', 'scope_id', 'model_id', 'limit_type'],
  'provider_quotas should have unique scope constraint'
);

-- 6. Model Catalogue Invariants
-- Requirement: openrouter/free has is_routing_alias = true and alias_target IS NULL
SELECT is(
  (SELECT is_routing_alias FROM public.llm_models WHERE id = 'openrouter/free'),
  true,
  'openrouter/free must be a routing alias'
);
SELECT is(
  (SELECT alias_target FROM public.llm_models WHERE id = 'openrouter/free'),
  NULL::text,
  'openrouter/free must NOT have a fixed alias_target'
);

-- Requirement: separate concrete free model exists
SELECT is(
  (SELECT count(*)::integer FROM public.llm_models WHERE id = 'openrouter/meta-llama/llama-3.3-70b-instruct:free'),
  1,
  'openrouter/meta-llama/llama-3.3-70b-instruct:free must be registered as a separate model'
);

-- Requirement: qwen/qwen3.8-27b has release_stage = 'preview'
SELECT is(
  (SELECT release_stage FROM public.llm_models WHERE id = 'qwen/qwen3.8-27b'),
  'preview'::text,
  'qwen/qwen3.8-27b must be in preview release stage'
);

-- 7. Historical record preservation on disablement
-- Changing availability_status to disabled updates is_active to false without deleting the record
UPDATE public.llm_models SET availability_status = 'disabled' WHERE id = 'cerebras/gpt-oss-120b';
SELECT is(
  (SELECT is_active FROM public.llm_models WHERE id = 'cerebras/gpt-oss-120b'),
  false,
  'disabled model must have is_active = false'
);

SELECT * FROM finish();
ROLLBACK;
