-- Disable any deprecated Gemini 2.x placeholder entries safely (without violating reservation FKs)
DELETE FROM public.provider_quotas
WHERE provider_id = 'gemini'
  AND model_id IN ('gemini-2.5-flash', 'gemini-2.5-flash-lite');

UPDATE public.llm_models
SET availability_status = 'disabled',
    replacement_model_id = 'gemini-3.5-flash-lite',
    updated_at = now()
WHERE id IN ('gemini-2.5-flash', 'gemini-2.5-flash-lite');

-- Ensure gemini-3.8-flash remains active and register/update Gemini 3.1 / 3.5 models
UPDATE public.llm_models
SET availability_status = 'active',
    replacement_model_id = NULL,
    updated_at = now()
WHERE id IN ('gemini-3.8-flash', 'gemini-3.5-flash-lite');

INSERT INTO public.llm_models (
  id, provider_id, family, display_name, availability_status, release_stage,
  is_routing_alias, alias_target, free_tier_eligible,
  input_cost_per_million_cents, output_cost_per_million_cents,
  capabilities, context_window, max_output_tokens, modalities_input, modalities_output,
  source_documentation
)
VALUES
  (
    'gemini-3.5-flash-lite', 'gemini', 'gemini', 'Google Gemini 3.5 Flash-Lite', 'active', 'ga',
    false, null, true,
    0, 0,
    ARRAY['streaming', 'tools'], 1048576, 8192, ARRAY['text'], ARRAY['text'],
    'https://ai.google.dev/gemini-api/docs/models'
  ),
  (
    'gemini-3.1-flash-lite', 'gemini', 'gemini', 'Google Gemini 3.1 Flash-Lite', 'active', 'ga',
    false, null, true,
    0, 0,
    ARRAY['streaming', 'tools', 'vision'], 1048576, 8192, ARRAY['text', 'image'], ARRAY['text'],
    'https://ai.google.dev/gemini-api/docs/models'
  ),
  (
    'gemini-3.1-pro-preview', 'gemini', 'gemini', 'Google Gemini 3.1 Pro Preview', 'active', 'preview',
    false, null, true,
    0, 0,
    ARRAY['streaming', 'tools', 'vision', 'reasoning'], 1048576, 8192, ARRAY['text', 'image'], ARRAY['text'],
    'https://ai.google.dev/gemini-api/docs/models'
  )
ON CONFLICT (id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  availability_status = EXCLUDED.availability_status,
  release_stage = EXCLUDED.release_stage,
  is_routing_alias = EXCLUDED.is_routing_alias,
  alias_target = EXCLUDED.alias_target,
  free_tier_eligible = EXCLUDED.free_tier_eligible,
  input_cost_per_million_cents = EXCLUDED.input_cost_per_million_cents,
  output_cost_per_million_cents = EXCLUDED.output_cost_per_million_cents,
  capabilities = EXCLUDED.capabilities,
  context_window = EXCLUDED.context_window,
  max_output_tokens = EXCLUDED.max_output_tokens,
  modalities_input = EXCLUDED.modalities_input,
  modalities_output = EXCLUDED.modalities_output,
  source_documentation = EXCLUDED.source_documentation,
  replacement_model_id = NULL,
  updated_at = now();

INSERT INTO public.provider_quotas (
  provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
  source_documentation, confidence, provenance
)
SELECT v.provider_id, v.scope_type, v.scope_id, v.model_id, v.limit_type, v.limit_value,
       v.source_documentation, v.confidence, v.provenance
FROM (
  VALUES
    ('gemini', 'project', 'global', 'gemini-3.5-flash-lite', 'rpm', 30, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.5-flash-lite', 'tpm', 1000000, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.5-flash-lite', 'rpd', 1500, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-flash-lite', 'rpm', 30, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-flash-lite', 'tpm', 1000000, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-flash-lite', 'rpd', 1500, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-pro-preview', 'rpm', 5, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-pro-preview', 'tpm', 250000, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published'),
    ('gemini', 'project', 'global', 'gemini-3.1-pro-preview', 'rpd', 100, 'https://ai.google.dev/gemini-api/docs/rate-limits', 'high', 'provider_published')
) AS v(provider_id, scope_type, scope_id, model_id, limit_type, limit_value, source_documentation, confidence, provenance)
WHERE NOT EXISTS (
  SELECT 1 FROM public.provider_quotas pq
  WHERE pq.provider_id = v.provider_id
    AND pq.scope_type = v.scope_type
    AND pq.scope_id = v.scope_id
    AND pq.model_id = v.model_id
    AND pq.limit_type = v.limit_type
);
