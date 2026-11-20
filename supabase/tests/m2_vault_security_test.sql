-- ==============================================================================
-- RagBait M2.2 Credential Vault Database Security & Isolation pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(34);

-- 1. Dedicated Database Roles & Membership
SELECT has_role('ragbait_vault_service', 'Role ragbait_vault_service should exist');
SELECT is(
  (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ragbait_vault_service'),
  false,
  'ragbait_vault_service must be a NOLOGIN capability role'
);
SELECT is(
  (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'ragbait_vault_user'),
  true,
  'ragbait_vault_user must have LOGIN privilege'
);
SELECT is(
  pg_has_role('ragbait_vault_user', 'ragbait_vault_service', 'MEMBER'),
  true,
  'ragbait_vault_user must be a member of ragbait_vault_service'
);
SELECT is(
  (SELECT rolbypassrls FROM pg_roles WHERE rolname = 'ragbait_vault_service'),
  false,
  'ragbait_vault_service must NOT have BYPASSRLS'
);

-- 2. Schema Existence & Isolation
SELECT has_schema('private', 'Schema private should exist');

SELECT is(
  has_schema_privilege('anon', 'private', 'USAGE'),
  false,
  'anon must NOT have USAGE on schema private'
);
SELECT is(
  has_schema_privilege('authenticated', 'private', 'USAGE'),
  false,
  'authenticated must NOT have USAGE on schema private'
);
SELECT is(
  has_schema_privilege('ragbait_vault_service', 'private', 'USAGE'),
  true,
  'ragbait_vault_service must have USAGE on schema private'
);

-- 3. Table Existence & Column Structure
SELECT has_table('public', 'api_key_metadata', 'public.api_key_metadata should exist');
SELECT has_table('private', 'api_key_secrets', 'private.api_key_secrets should exist');
SELECT columns_are(
  'private', 'api_key_secrets',
  ARRAY['id', 'encrypted_dek', 'dek_nonce', 'dek_auth_tag', 'ciphertext', 'nonce', 'data_encryption_version', 'kek_version', 'wrapping_context_version', 'created_at', 'updated_at'],
  'private.api_key_secrets columns must include data_encryption_version, kek_version, wrapping_context_version'
);

-- 4. Row Level Security on public.api_key_metadata
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'api_key_metadata' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on public.api_key_metadata'
);

-- Permissions on public.api_key_metadata: anon must have no access
SELECT is(
  has_table_privilege('anon', 'public.api_key_metadata', 'SELECT'),
  false,
  'anon must NOT have SELECT on public.api_key_metadata'
);
SELECT is(
  has_table_privilege('anon', 'public.api_key_metadata', 'INSERT'),
  false,
  'anon must NOT have INSERT on public.api_key_metadata'
);
SELECT is(
  has_table_privilege('anon', 'public.api_key_metadata', 'UPDATE'),
  false,
  'anon must NOT have UPDATE on public.api_key_metadata'
);
SELECT is(
  has_table_privilege('anon', 'public.api_key_metadata', 'DELETE'),
  false,
  'anon must NOT have DELETE on public.api_key_metadata'
);

-- 5. Permissions on private.api_key_secrets
SELECT is(
  has_table_privilege('anon', 'private.api_key_secrets', 'SELECT'),
  false,
  'anon must NOT have SELECT on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('authenticated', 'private.api_key_secrets', 'SELECT'),
  false,
  'authenticated must NOT have SELECT on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('authenticated', 'private.api_key_secrets', 'INSERT'),
  false,
  'authenticated must NOT have INSERT on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('authenticated', 'private.api_key_secrets', 'UPDATE'),
  false,
  'authenticated must NOT have UPDATE on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('authenticated', 'private.api_key_secrets', 'DELETE'),
  false,
  'authenticated must NOT have DELETE on private.api_key_secrets'
);

-- Permissions granted to ragbait_vault_service on private.api_key_secrets
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.api_key_secrets', 'SELECT'),
  true,
  'ragbait_vault_service must have SELECT on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.api_key_secrets', 'INSERT'),
  true,
  'ragbait_vault_service must have INSERT on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.api_key_secrets', 'UPDATE'),
  true,
  'ragbait_vault_service must have UPDATE on private.api_key_secrets'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'private.api_key_secrets', 'DELETE'),
  true,
  'ragbait_vault_service must have DELETE on private.api_key_secrets'
);

-- Least-Privilege: ragbait_vault_service must NOT access unrelated tables
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.profiles', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.profiles'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.audit_logs', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.audit_logs'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.access_requests', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.access_requests'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.user_presence', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.user_presence'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.llm_models', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.llm_models'
);
SELECT is(
  has_table_privilege('ragbait_vault_service', 'public.provider_quotas', 'SELECT'),
  false,
  'ragbait_vault_service must NOT have SELECT on public.provider_quotas'
);

-- 6. Foreign Key & Unique Constraints
SELECT fk_ok(
  'private', 'api_key_secrets', 'id',
  'public', 'api_key_metadata', 'id'
);
SELECT col_is_unique(
  'public', 'api_key_metadata', ARRAY['provider_id', 'key_fingerprint'],
  'public.api_key_metadata must have unique constraint on (provider_id, key_fingerprint)'
);

SELECT * FROM finish();
ROLLBACK;
