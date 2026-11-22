-- ==============================================================================
-- RagBait Free-Tier Contribution Hardening pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(15);

-- 1. Check columns exist
SELECT has_column('public', 'api_key_metadata', 'billing_tier', 'public.api_key_metadata must have billing_tier column');
SELECT has_column('public', 'api_key_metadata', 'billing_verified_at', 'public.api_key_metadata must have billing_verified_at column');
SELECT has_column('public', 'api_key_metadata', 'billing_verification_error', 'public.api_key_metadata must have billing_verification_error column');
SELECT has_column('public', 'api_key_metadata', 'billing_verification_source', 'public.api_key_metadata must have billing_verification_source column');

-- 2. Verify Table Permissions for authenticated role
SELECT is(
  has_table_privilege('authenticated', 'public.api_key_metadata', 'UPDATE'),
  false,
  'authenticated role must NOT have table-level UPDATE on public.api_key_metadata'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'nickname', 'UPDATE'),
  true,
  'authenticated role MUST have column-level UPDATE on nickname'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'validation_status', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on validation_status'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'status', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on status'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'sharing_scope', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on sharing_scope'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'shared_consent', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on shared_consent'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'billing_tier', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on billing_tier'
);
SELECT is(
  has_column_privilege('authenticated', 'public.api_key_metadata', 'account_scope_id', 'UPDATE'),
  false,
  'authenticated role must NOT have column-level UPDATE on account_scope_id'
);

-- 3. Verify ragbait_vault_service permissions
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.api_key_metadata', 'UPDATE'),
  true,
  'ragbait_vault_service must have UPDATE on public.api_key_metadata'
);

-- 4. Test authenticated INSERT attribute sanitization (trigger test)
-- Create a test user
INSERT INTO auth.users (id, instance_id, email, aud, role)
VALUES ('11111111-1111-1111-1111-111111111111', '00000000-0000-0000-0000-000000000000', 'tamper_test@studyhub.edu', 'authenticated', 'authenticated')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.profiles (id, email, display_name, role, approval_status)
VALUES ('11111111-1111-1111-1111-111111111111', 'tamper_test@studyhub.edu', 'Tamper Tester', 'user', 'approved')
ON CONFLICT (id) DO NOTHING;

-- Switch to authenticated role
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = '11111111-1111-1111-1111-111111111111';

-- Attempt insert with forged security values
INSERT INTO public.api_key_metadata (
  id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
  validation_status, status, sharing_scope, shared_consent, billing_tier
) VALUES (
  '22222222-2222-2222-2222-222222222222',
  '11111111-1111-1111-1111-111111111111',
  'openrouter',
  'Forged Key',
  'sk-or-...1234',
  'fp_tamper_test_123',
  'valid',
  'active',
  'community_shared',
  true,
  'verified_free_only'
);

-- Reset role to check inserted row
RESET ROLE;

SELECT is(
  (SELECT validation_status FROM public.api_key_metadata WHERE id = '22222222-2222-2222-2222-222222222222'),
  'unvalidated',
  'Direct authenticated INSERT must have validation_status sanitized to unvalidated'
);

SELECT is(
  (SELECT billing_tier FROM public.api_key_metadata WHERE id = '22222222-2222-2222-2222-222222222222'),
  'unknown',
  'Direct authenticated INSERT must have billing_tier sanitized to unknown'
);

SELECT * FROM finish();
ROLLBACK;
