-- ==============================================================================
-- RagBait M2.5: Provenance Safe Defaults & Lifecycle Integrity
-- ==============================================================================

-- 1. Update check constraint on usage_source to include 'pending' as a valid lifecycle state
ALTER TABLE public.llm_usage_reservations
  DROP CONSTRAINT IF EXISTS ck_reservations_usage_source;

ALTER TABLE public.llm_usage_reservations
  ADD CONSTRAINT ck_reservations_usage_source
  CHECK (usage_source IN ('pending', 'authoritative', 'derived', 'estimated', 'unknown'));

-- 2. Alter column default on usage_source from 'authoritative' to 'pending'
-- This prevents newly created reservations from prematurely asserting authoritative usage
-- before upstream dispatch and token extraction have occurred.
ALTER TABLE public.llm_usage_reservations
  ALTER COLUMN usage_source SET DEFAULT 'pending';

-- 3. Correct historical uncompleted reservations that were defaulted to 'authoritative'
UPDATE public.llm_usage_reservations
SET usage_source = 'pending'
WHERE status IN ('reserved', 'dispatched') AND usage_source = 'authoritative';

-- 4. Correct historical reservations in error or outcome_unknown status with zero actual tokens
UPDATE public.llm_usage_reservations
SET usage_source = 'unknown'
WHERE status IN ('outcome_unknown', 'expired') AND (actual_input_tokens IS NULL OR actual_input_tokens = 0);
