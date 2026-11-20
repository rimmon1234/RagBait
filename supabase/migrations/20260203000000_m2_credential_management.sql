-- ==============================================================================
-- RagBait M2.3 Migration: Owner-Only Credential Management & Validation Probes
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Provider Community Sharing Eligibility Flag
-- ------------------------------------------------------------------------------
-- Controls whether an AI provider is reviewed and approved for pooled community sharing.
ALTER TABLE public.llm_providers
  ADD COLUMN IF NOT EXISTS allows_community_sharing boolean NOT NULL DEFAULT false;

-- Mark verified cloud providers with established multi-tenant/shared key compliance
UPDATE public.llm_providers
  SET allows_community_sharing = true
  WHERE id IN ('groq', 'openrouter', 'cerebras');

-- ------------------------------------------------------------------------------
-- 2. Credential Metadata Extensions for Validation & Consent
-- ------------------------------------------------------------------------------
-- Set default status for newly created credentials to 'disabled' (requires explicit activation)
ALTER TABLE public.api_key_metadata
  ALTER COLUMN status SET DEFAULT 'disabled';

-- Add validation state, timestamps, error codes, and consent versions
ALTER TABLE public.api_key_metadata
  ADD COLUMN IF NOT EXISTS validation_status text NOT NULL DEFAULT 'unvalidated'
    CHECK (validation_status IN ('unvalidated', 'valid', 'invalid_credential', 'insufficient_permission', 'rate_limited', 'provider_unavailable', 'unsupported', 'validation_error')),
  ADD COLUMN IF NOT EXISTS last_validated_at timestamptz,
  ADD COLUMN IF NOT EXISTS validation_error_code text,
  ADD COLUMN IF NOT EXISTS consent_version text;

CREATE INDEX IF NOT EXISTS idx_api_key_metadata_validation ON public.api_key_metadata(validation_status);

-- ------------------------------------------------------------------------------
-- 3. Grants & Privileges
-- ------------------------------------------------------------------------------
-- Ensure authenticated users and the vault service role have access to the extended columns
GRANT SELECT, INSERT, UPDATE, DELETE ON public.api_key_metadata TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES ON public.api_key_metadata TO ragbait_vault_service;
