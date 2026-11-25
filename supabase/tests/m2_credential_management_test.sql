-- ==============================================================================
-- RagBait M2.3 Credential Management Database pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(8);

-- 1. Verify allows_community_sharing column exists on public.llm_providers
SELECT has_column(
  'public', 'llm_providers', 'allows_community_sharing',
  'public.llm_providers must have allows_community_sharing column'
);

-- 2. Verify approved community providers (OpenRouter, Gemini, Groq enabled; paid-only excluded)
SELECT is(
  (SELECT allows_community_sharing FROM public.llm_providers WHERE id = 'groq'),
  true,
  'Groq provider allows community sharing for free-tier key pooling (allows_community_sharing = true)'
);
SELECT is(
  (SELECT allows_community_sharing FROM public.llm_providers WHERE id = 'openrouter'),
  true,
  'OpenRouter provider should have allows_community_sharing = true'
);
SELECT is(
  (SELECT allows_community_sharing FROM public.llm_providers WHERE id = 'gemini'),
  true,
  'Gemini provider allows community sharing for free-tier key pooling (allows_community_sharing = true)'
);

-- 3. Verify validation and consent columns on public.api_key_metadata
SELECT has_column(
  'public', 'api_key_metadata', 'validation_status',
  'public.api_key_metadata must have validation_status column'
);
SELECT has_column(
  'public', 'api_key_metadata', 'last_validated_at',
  'public.api_key_metadata must have last_validated_at column'
);
SELECT has_column(
  'public', 'api_key_metadata', 'validation_error_code',
  'public.api_key_metadata must have validation_error_code column'
);
SELECT has_column(
  'public', 'api_key_metadata', 'consent_version',
  'public.api_key_metadata must have consent_version column'
);

SELECT * FROM finish();
ROLLBACK;
