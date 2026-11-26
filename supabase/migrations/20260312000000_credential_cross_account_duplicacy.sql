-- ==============================================================================
-- RagBait Security Hardening: Cross-Account API Key Duplicacy Prevention
-- ==============================================================================
-- Closes the delete-and-reuse and cross-provider bypass vectors where two
-- different accounts attempt to register the same API key (either concurrently,
-- under a different provider ID, or after the original owner deletes the key)
-- in order to satisfy the AI Chat contribution gate multiple times.

-- ------------------------------------------------------------------------------
-- 1. Global Fingerprint Uniqueness on Active Credential Metadata
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'uq_global_key_fingerprint'
      AND conrelid = 'public.api_key_metadata'::regclass
  ) THEN
    ALTER TABLE public.api_key_metadata
      ADD CONSTRAINT uq_global_key_fingerprint UNIQUE (key_fingerprint);
  END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 2. Immutable Cross-Account Fingerprint Claim Registry (Private Schema)
-- ------------------------------------------------------------------------------
-- Persists which account first registered a given HMAC key_fingerprint even if
-- that account later deletes the credential from public.api_key_metadata.
-- Only ragbait_vault_service may read or write this table; anon/authenticated have
-- zero privileges.
CREATE TABLE IF NOT EXISTS private.credential_fingerprint_claims (
  key_fingerprint text PRIMARY KEY,
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE RESTRICT,
  first_owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  first_credential_id uuid NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credential_fingerprint_claims_owner
  ON private.credential_fingerprint_claims(first_owner_id);

REVOKE ALL ON TABLE private.credential_fingerprint_claims FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE private.credential_fingerprint_claims TO ragbait_vault_service;

-- Backfill existing credentials safely
INSERT INTO private.credential_fingerprint_claims (
  key_fingerprint,
  provider_id,
  first_owner_id,
  first_credential_id,
  claimed_at
)
SELECT
  key_fingerprint,
  provider_id,
  owner_id,
  id,
  created_at
FROM public.api_key_metadata
ON CONFLICT (key_fingerprint) DO NOTHING;
