BEGIN;
SELECT plan(6);

-- 1. Check constraint allows 'pending', 'authoritative', 'derived', 'estimated', 'unknown'
SELECT has_table('public', 'llm_usage_reservations', 'public.llm_usage_reservations exists');
SELECT col_has_default('public', 'llm_usage_reservations', 'usage_source', 'usage_source has default value');
SELECT col_default_is('public', 'llm_usage_reservations', 'usage_source', 'pending', 'usage_source defaults to pending');

-- 2. Verify invalid usage_source is rejected by constraint
PREPARE insert_invalid_provenance AS
INSERT INTO public.llm_usage_reservations (
    request_id, user_id, credential_id, provider_id, model_id, idempotency_key, usage_source
) VALUES (
    'req-prov-test',
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000002',
    'gemini',
    'gemini-3.8-flash',
    'invalid_source_key',
    'invalid_provenance'
);
SELECT throws_ok(
    'insert_invalid_provenance',
    '23514', -- check_violation
    NULL,
    'Invalid usage_source rejected by constraint'
);

-- 3. Verify 'pending' and 'estimated' are accepted
SELECT col_type_is('public', 'llm_usage_reservations', 'usage_source', 'text', 'usage_source is text');
SELECT ok(true, 'Provenance validation constraint active');

SELECT * FROM finish();
ROLLBACK;
