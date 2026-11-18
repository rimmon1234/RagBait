-- ==============================================================================
-- RagBait M2.4 Admission Control & Usage Ledger pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(10);

-- 1. Verify tables exist
SELECT has_table('public', 'llm_usage_reservations', 'public.llm_usage_reservations table must exist');
SELECT has_table('public', 'llm_usage_events', 'public.llm_usage_events table must exist');
SELECT has_table('public', 'llm_usage_budget_counters', 'public.llm_usage_budget_counters table must exist');

-- 2. Verify RLS is enabled on all ledger tables
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'llm_usage_reservations' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on llm_usage_reservations'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'llm_usage_events' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on llm_usage_events'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'llm_usage_budget_counters' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on llm_usage_budget_counters'
);

-- 3. Verify unique constraint for idempotency
SELECT col_is_unique(
  'public', 'llm_usage_reservations', ARRAY['user_id', 'idempotency_key'],
  'llm_usage_reservations must have UNIQUE(user_id, idempotency_key)'
);

-- 4. Verify primary key on budget counters
SELECT col_is_pk(
  'public', 'llm_usage_budget_counters', ARRAY['credential_id', 'period_type', 'period_start'],
  'llm_usage_budget_counters PK must be (credential_id, period_type, period_start)'
);

-- 5. Verify immutability trigger on llm_usage_events
SELECT has_trigger(
  'public', 'llm_usage_events', 'tr_llm_usage_events_immutable',
  'llm_usage_events must have immutability trigger tr_llm_usage_events_immutable'
);

-- 6. Verify anonymous access is denied
SELECT throws_ok(
  $$
    SET ROLE anon;
    INSERT INTO public.llm_usage_events (
      reservation_id, event_type, user_id, credential_id
    ) VALUES (
      gen_random_uuid(), 'reservation_created', gen_random_uuid(), gen_random_uuid()
    );
  $$,
  '42501',
  NULL,
  'Anon must not be able to insert into llm_usage_events'
);

SELECT * FROM finish();
ROLLBACK;
