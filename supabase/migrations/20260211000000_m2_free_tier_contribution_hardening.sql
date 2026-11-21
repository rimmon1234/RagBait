-- ==============================================================================
-- RagBait M2 Hardening: Free-Tier Contribution Integrity & Metadata Protection
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Add Credential Billing Tier & Verification Provenance Columns
-- ------------------------------------------------------------------------------
ALTER TABLE public.api_key_metadata
  ADD COLUMN IF NOT EXISTS billing_tier text NOT NULL DEFAULT 'unknown'
    CHECK (billing_tier IN ('verified_free_only', 'paid', 'unknown')),
  ADD COLUMN IF NOT EXISTS billing_verified_at timestamptz,
  ADD COLUMN IF NOT EXISTS billing_verification_error text,
  ADD COLUMN IF NOT EXISTS billing_verification_source text;

CREATE INDEX IF NOT EXISTS idx_api_key_metadata_billing_tier
  ON public.api_key_metadata(billing_tier, status, sharing_scope);

-- ------------------------------------------------------------------------------
-- 2. Restrict Direct Client Permissions on public.api_key_metadata
-- ------------------------------------------------------------------------------
-- Revoke table-level UPDATE from authenticated users.
-- Direct PostgREST client updates are only permitted on user-editable non-security columns (nickname).
REVOKE UPDATE ON public.api_key_metadata FROM authenticated;
GRANT UPDATE (nickname) ON public.api_key_metadata TO authenticated;

-- Ensure vault service role maintains full operational privileges
GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES ON public.api_key_metadata TO ragbait_vault_service;

-- ------------------------------------------------------------------------------
-- 3. Comprehensive Database Defense-in-Depth Trigger
-- ------------------------------------------------------------------------------
-- Prevents authenticated role from forging validation status, active status,
-- sharing scope, consent, account scope, or billing tier via direct SQL/PostgREST.
CREATE OR REPLACE FUNCTION public.protect_credential_metadata_tampering()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  IF current_user = 'authenticated' THEN
    IF TG_OP = 'INSERT' THEN
      -- Force all server-managed attributes to unverified / safe defaults on direct insert
      NEW.validation_status := 'unvalidated';
      NEW.last_validated_at := NULL;
      NEW.validation_error_code := NULL;
      NEW.status := 'disabled';
      NEW.sharing_scope := 'owner_only';
      NEW.shared_consent := false;
      NEW.sharing_consent_timestamp := NULL;
      NEW.consent_version := NULL;
      NEW.account_scope_id := NULL;
      NEW.account_scope_verified := false;
      NEW.account_scope_source := NULL;
      NEW.account_scope_verified_at := NULL;
      NEW.billing_tier := 'unknown';
      NEW.billing_verified_at := NULL;
      NEW.billing_verification_error := NULL;
      NEW.billing_verification_source := NULL;
    ELSIF TG_OP = 'UPDATE' THEN
      -- Reject any direct modification of server-managed or security attributes
      IF (
        NEW.owner_id IS DISTINCT FROM OLD.owner_id OR
        NEW.provider_id IS DISTINCT FROM OLD.provider_id OR
        NEW.masked_key IS DISTINCT FROM OLD.masked_key OR
        NEW.key_fingerprint IS DISTINCT FROM OLD.key_fingerprint OR
        NEW.validation_status IS DISTINCT FROM OLD.validation_status OR
        NEW.last_validated_at IS DISTINCT FROM OLD.last_validated_at OR
        NEW.validation_error_code IS DISTINCT FROM OLD.validation_error_code OR
        NEW.status IS DISTINCT FROM OLD.status OR
        NEW.sharing_scope IS DISTINCT FROM OLD.sharing_scope OR
        NEW.shared_consent IS DISTINCT FROM OLD.shared_consent OR
        NEW.sharing_consent_timestamp IS DISTINCT FROM OLD.sharing_consent_timestamp OR
        NEW.consent_version IS DISTINCT FROM OLD.consent_version OR
        NEW.account_scope_id IS DISTINCT FROM OLD.account_scope_id OR
        NEW.account_scope_verified IS DISTINCT FROM OLD.account_scope_verified OR
        NEW.account_scope_source IS DISTINCT FROM OLD.account_scope_source OR
        NEW.account_scope_verified_at IS DISTINCT FROM OLD.account_scope_verified_at OR
        NEW.billing_tier IS DISTINCT FROM OLD.billing_tier OR
        NEW.billing_verified_at IS DISTINCT FROM OLD.billing_verified_at OR
        NEW.billing_verification_error IS DISTINCT FROM OLD.billing_verification_error OR
        NEW.billing_verification_source IS DISTINCT FROM OLD.billing_verification_source OR
        NEW.daily_request_limit IS DISTINCT FROM OLD.daily_request_limit OR
        NEW.monthly_request_limit IS DISTINCT FROM OLD.monthly_request_limit OR
        NEW.daily_spending_limit_cents IS DISTINCT FROM OLD.daily_spending_limit_cents OR
        NEW.monthly_spending_limit_cents IS DISTINCT FROM OLD.monthly_spending_limit_cents
      ) THEN
        RAISE EXCEPTION 'Direct client modification of server-managed credential security attributes is prohibited'
          USING ERRCODE = '42501';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tr_api_key_metadata_account_scope_protect ON public.api_key_metadata;
DROP TRIGGER IF EXISTS tr_api_key_metadata_tampering_protect ON public.api_key_metadata;
CREATE TRIGGER tr_api_key_metadata_tampering_protect
BEFORE INSERT OR UPDATE ON public.api_key_metadata
FOR EACH ROW EXECUTE FUNCTION public.protect_credential_metadata_tampering();
