-- ==============================================================================
-- RagBait M2.5 TPD & Gateway Provenance pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(12);

-- 1. Columns on public.llm_usage_budget_counters
SELECT has_column(
  'public', 'llm_usage_budget_counters', 'reserved_tokens',
  'llm_usage_budget_counters must have reserved_tokens column'
);
SELECT has_column(
  'public', 'llm_usage_budget_counters', 'actual_tokens',
  'llm_usage_budget_counters must have actual_tokens column'
);

-- 2. Columns on public.llm_usage_reservations
SELECT has_column(
  'public', 'llm_usage_reservations', 'resolved_model_id',
  'llm_usage_reservations must have resolved_model_id column'
);
SELECT has_column(
  'public', 'llm_usage_reservations', 'upstream_provider',
  'llm_usage_reservations must have upstream_provider column'
);
SELECT has_column(
  'public', 'llm_usage_reservations', 'upstream_generation_id',
  'llm_usage_reservations must have upstream_generation_id column'
);
SELECT has_column(
  'public', 'llm_usage_reservations', 'usage_source',
  'llm_usage_reservations must have usage_source column'
);

-- 3. Check constraints
SELECT has_check(
  'public', 'llm_usage_budget_counters',
  'llm_usage_budget_counters must have check constraints'
);

SELECT has_check(
  'public', 'llm_usage_reservations',
  'llm_usage_reservations must have check constraints'
);

-- 4. Constraint integrity tests: negative tokens must be rejected
SELECT throws_ok(
  $$
    INSERT INTO public.llm_usage_budget_counters (
      credential_id, period_type, period_start,
      reserved_requests, dispatched_requests,
      reserved_cost_cents, actual_cost_cents,
      reserved_tokens, actual_tokens
    ) VALUES (
      '00000000-0000-0000-0000-000000000001', 'daily', now(),
      0, 0, 0, 0, -10, 0
    )
  $$,
  '23514', -- check_violation
  NULL,
  'Negative reserved_tokens must violate check constraint'
);

SELECT throws_ok(
  $$
    INSERT INTO public.llm_usage_budget_counters (
      credential_id, period_type, period_start,
      reserved_requests, dispatched_requests,
      reserved_cost_cents, actual_cost_cents,
      reserved_tokens, actual_tokens
    ) VALUES (
      '00000000-0000-0000-0000-000000000001', 'daily', now(),
      0, 0, 0, 0, 0, -5
    )
  $$,
  '23514', -- check_violation
  NULL,
  'Negative actual_tokens must violate check constraint'
);

-- 5. Vault role isolation: ragbait_vault_service must not have SELECT on usage tables
SELECT throws_ok(
  $$
    SET ROLE ragbait_vault_service;
    SELECT * FROM public.llm_usage_budget_counters LIMIT 1;
  $$,
  '42501', -- insufficient_privilege
  NULL,
  'ragbait_vault_service must not have SELECT access to llm_usage_budget_counters'
);

SELECT throws_ok(
  $$
    SET ROLE ragbait_vault_service;
    SELECT * FROM public.llm_usage_reservations LIMIT 1;
  $$,
  '42501', -- insufficient_privilege
  NULL,
  'ragbait_vault_service must not have SELECT access to llm_usage_reservations'
);

SELECT * FROM finish();
ROLLBACK;
