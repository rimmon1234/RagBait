-- ==============================================================================
-- RagBait M2.2 Migration: Encrypted Credential Vault & Dedicated Database Role
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Dedicated Least-Privilege Vault Database Role (NOLOGIN Capability Role)
-- ------------------------------------------------------------------------------
-- The capability role owns database permissions for the credential vault.
-- Login roles are provisioned separately per environment (e.g. roles.sql locally,
-- or scripts/provision_vault_user.py in production) and granted membership.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_service') THEN
    CREATE ROLE ragbait_vault_service WITH NOLOGIN NOBYPASSRLS;
  ELSE
    ALTER ROLE ragbait_vault_service WITH NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 2. Public Credential Metadata Table (Row Level Security Active)
-- ------------------------------------------------------------------------------
CREATE TABLE public.api_key_metadata (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE RESTRICT,
  nickname text NOT NULL,
  masked_key text NOT NULL,
  key_fingerprint text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'revoked', 'rate_limited', 'quota_exhausted')),
  sharing_scope text NOT NULL DEFAULT 'owner_only' CHECK (sharing_scope IN ('owner_only', 'community_shared')),
  shared_consent boolean NOT NULL DEFAULT false,
  sharing_consent_timestamp timestamptz,
  account_scope_id text,
  daily_request_limit integer NOT NULL DEFAULT 500,
  monthly_request_limit integer NOT NULL DEFAULT 15000,
  daily_spending_limit_cents integer,
  monthly_spending_limit_cents integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_provider_fingerprint UNIQUE (provider_id, key_fingerprint)
);

CREATE INDEX idx_api_key_metadata_owner ON public.api_key_metadata(owner_id);
CREATE INDEX idx_api_key_metadata_provider_status ON public.api_key_metadata(provider_id, status);
CREATE INDEX idx_api_key_metadata_account_scope ON public.api_key_metadata(account_scope_id) WHERE account_scope_id IS NOT NULL;

CREATE TRIGGER tr_api_key_metadata_updated_at
BEFORE UPDATE ON public.api_key_metadata
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- RLS on public.api_key_metadata
ALTER TABLE public.api_key_metadata ENABLE ROW LEVEL SECURITY;

CREATE POLICY api_key_metadata_owner_select ON public.api_key_metadata
  FOR SELECT TO authenticated
  USING (owner_id = auth.uid());

CREATE POLICY api_key_metadata_owner_insert ON public.api_key_metadata
  FOR INSERT TO authenticated
  WITH CHECK (owner_id = auth.uid());

CREATE POLICY api_key_metadata_owner_update ON public.api_key_metadata
  FOR UPDATE TO authenticated
  USING (owner_id = auth.uid())
  WITH CHECK (owner_id = auth.uid());

CREATE POLICY api_key_metadata_owner_delete ON public.api_key_metadata
  FOR DELETE TO authenticated
  USING (owner_id = auth.uid());

-- Admins can view metadata for platform monitoring
CREATE POLICY api_key_metadata_admin_select ON public.api_key_metadata
  FOR SELECT TO authenticated
  USING (app_private.is_admin(auth.uid()));

-- Dedicated vault service role has full access to metadata
CREATE POLICY api_key_metadata_vault_service_all ON public.api_key_metadata
  FOR ALL TO ragbait_vault_service
  USING (true)
  WITH CHECK (true);

-- Grants on public.api_key_metadata
REVOKE ALL ON TABLE public.api_key_metadata FROM anon, PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.api_key_metadata TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES ON public.api_key_metadata TO ragbait_vault_service;
GRANT USAGE ON SCHEMA public TO ragbait_vault_service;

-- ------------------------------------------------------------------------------
-- 3. Isolated Private Schema & Secrets Table
-- ------------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA private TO ragbait_vault_service;

CREATE TABLE private.api_key_secrets (
  id uuid PRIMARY KEY REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  encrypted_dek bytea NOT NULL,
  dek_nonce bytea NOT NULL,
  dek_auth_tag bytea NOT NULL,
  ciphertext bytea NOT NULL,
  nonce bytea NOT NULL,
  data_encryption_version integer NOT NULL DEFAULT 1,
  kek_version integer NOT NULL DEFAULT 1,
  wrapping_context_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER tr_api_key_secrets_updated_at
BEFORE UPDATE ON private.api_key_secrets
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Restrict private table access strictly to ragbait_vault_service
REVOKE ALL ON TABLE private.api_key_secrets FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE private.api_key_secrets TO ragbait_vault_service;
