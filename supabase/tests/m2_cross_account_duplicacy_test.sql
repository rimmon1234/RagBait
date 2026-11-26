-- ==============================================================================
-- RagBait pgTAP Tests: Cross-Account API Key Duplicacy Prevention
-- ==============================================================================

BEGIN;
SELECT plan(8);

-- 1. Global unique constraint exists on public.api_key_metadata(key_fingerprint)
SELECT col_is_unique(
  'public', 'api_key_metadata', ARRAY['key_fingerprint'],
  'public.api_key_metadata must enforce global uniqueness on key_fingerprint'
);

-- 2. Legacy per-provider constraint remains intact
SELECT col_is_unique(
  'public', 'api_key_metadata', ARRAY['provider_id', 'key_fingerprint'],
  'public.api_key_metadata must retain (provider_id, key_fingerprint) unique constraint'
);

-- 3. Private fingerprint claim table exists and is strictly isolated
SELECT has_table('private', 'credential_fingerprint_claims', 'private.credential_fingerprint_claims should exist');

SELECT is(
  has_table_privilege('anon', 'private.credential_fingerprint_claims', 'SELECT'),
  false,
  'anon must NOT have SELECT on private.credential_fingerprint_claims'
);
SELECT is(
  has_table_privilege('authenticated', 'private.credential_fingerprint_claims', 'SELECT'),
  false,
  'authenticated must NOT have SELECT on private.credential_fingerprint_claims'
);
SELECT is(
  has_table_privilege('authenticated', 'private.credential_fingerprint_claims', 'INSERT'),
  false,
  'authenticated must NOT have INSERT on private.credential_fingerprint_claims'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.credential_fingerprint_claims', 'SELECT'),
  true,
  'ragbait_vault_service must have SELECT on private.credential_fingerprint_claims'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.credential_fingerprint_claims', 'INSERT'),
  true,
  'ragbait_vault_service must have INSERT on private.credential_fingerprint_claims'
);

SELECT * FROM finish();
ROLLBACK;
