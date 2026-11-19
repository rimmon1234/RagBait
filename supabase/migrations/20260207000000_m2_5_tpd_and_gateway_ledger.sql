-- ==============================================================================
-- RagBait M2.5: Tokens Per Day (TPD) Quota Tracking & LLM Gateway Provenance
-- ==============================================================================

-- 1. Extend public.llm_usage_budget_counters with daily token aggregates
ALTER TABLE public.llm_usage_budget_counters
  ADD COLUMN IF NOT EXISTS reserved_tokens bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS actual_tokens bigint NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_counters_reserved_tokens_nonneg'
  ) THEN
    ALTER TABLE public.llm_usage_budget_counters
      ADD CONSTRAINT ck_counters_reserved_tokens_nonneg CHECK (reserved_tokens >= 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_counters_actual_tokens_nonneg'
  ) THEN
    ALTER TABLE public.llm_usage_budget_counters
      ADD CONSTRAINT ck_counters_actual_tokens_nonneg CHECK (actual_tokens >= 0);
  END IF;
END $$;

-- 2. Extend public.llm_usage_reservations with upstream gateway provenance
ALTER TABLE public.llm_usage_reservations
  ADD COLUMN IF NOT EXISTS resolved_model_id text,
  ADD COLUMN IF NOT EXISTS upstream_provider text,
  ADD COLUMN IF NOT EXISTS upstream_generation_id text,
  ADD COLUMN IF NOT EXISTS usage_source text NOT NULL DEFAULT 'authoritative';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_reservations_usage_source'
  ) THEN
    ALTER TABLE public.llm_usage_reservations
      ADD CONSTRAINT ck_reservations_usage_source
      CHECK (usage_source IN ('authoritative', 'derived', 'estimated', 'unknown'));
  END IF;
END $$;

-- 3. Ensure permissions preserve the vault security boundary:
-- ragbait_vault_service / ragbait_vault_user must NOT have access to usage tables
REVOKE ALL ON public.llm_usage_budget_counters FROM PUBLIC, ragbait_vault_service;
REVOKE ALL ON public.llm_usage_reservations FROM PUBLIC, ragbait_vault_service;
REVOKE ALL ON public.llm_usage_events FROM PUBLIC, ragbait_vault_service;

GRANT SELECT, INSERT, UPDATE ON public.llm_usage_budget_counters TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.llm_usage_reservations TO service_role;
GRANT SELECT, INSERT ON public.llm_usage_events TO service_role;

GRANT SELECT ON public.llm_usage_reservations TO authenticated;
GRANT SELECT ON public.llm_usage_events TO authenticated;
