-- ==============================================================================
-- RagBait M2.4 Focused Audit Refinement: Exact Financial Microcents Precision
-- ==============================================================================

-- 1. Add exact microcents precision columns to reservations
ALTER TABLE public.llm_usage_reservations
  ADD COLUMN IF NOT EXISTS estimated_cost_microcents bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS actual_cost_microcents bigint;

-- Backfill from cents (1 cent = 1,000,000 microcents)
UPDATE public.llm_usage_reservations
SET estimated_cost_microcents = (estimated_cost_cents::bigint * 1000000)
WHERE estimated_cost_microcents = 0 AND estimated_cost_cents > 0;

UPDATE public.llm_usage_reservations
SET actual_cost_microcents = (actual_cost_cents::bigint * 1000000)
WHERE actual_cost_microcents IS NULL AND actual_cost_cents IS NOT NULL;

-- 2. Add exact microcents delta to usage events
ALTER TABLE public.llm_usage_events
  ADD COLUMN IF NOT EXISTS cost_microcents_delta bigint NOT NULL DEFAULT 0;

-- 3. Add exact microcents aggregates to budget counters
ALTER TABLE public.llm_usage_budget_counters
  ADD COLUMN IF NOT EXISTS reserved_cost_microcents bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS actual_cost_microcents bigint NOT NULL DEFAULT 0;

UPDATE public.llm_usage_budget_counters
SET reserved_cost_microcents = (reserved_cost_cents::bigint * 1000000),
    actual_cost_microcents = (actual_cost_cents::bigint * 1000000)
WHERE (reserved_cost_microcents = 0 AND reserved_cost_cents > 0)
   OR (actual_cost_microcents = 0 AND actual_cost_cents > 0);
