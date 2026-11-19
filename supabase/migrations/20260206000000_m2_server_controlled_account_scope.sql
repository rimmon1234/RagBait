-- ==============================================================================
-- RagBait M2.4 Final Security Refinement: Server-Controlled Account Scope Identity
-- ==============================================================================

-- 1. Add verification provenance columns to public.api_key_metadata
ALTER TABLE public.api_key_metadata
  ADD COLUMN IF NOT EXISTS account_scope_verified boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS account_scope_source text,
  ADD COLUMN IF NOT EXISTS account_scope_verified_at timestamptz;

-- 2. Add verification status column to llm_usage_reservations
ALTER TABLE public.llm_usage_reservations
  ADD COLUMN IF NOT EXISTS account_scope_verified boolean NOT NULL DEFAULT false;

-- 3. Reset any unverified existing account_scope_id to NULL to prevent quota/cooldown poisoning
UPDATE public.api_key_metadata
SET account_scope_id = NULL,
    account_scope_verified = false,
    account_scope_source = NULL,
    account_scope_verified_at = NULL
WHERE account_scope_verified = false;

-- 4. Database Trigger: Protect account_scope metadata against direct client tampering
CREATE OR REPLACE FUNCTION public.protect_account_scope_metadata()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  -- When modified by authenticated user role, prevent manual tampering with account scope
  IF current_user = 'authenticated' THEN
    IF TG_OP = 'INSERT' THEN
      NEW.account_scope_id := NULL;
      NEW.account_scope_verified := false;
      NEW.account_scope_source := NULL;
      NEW.account_scope_verified_at := NULL;
    ELSIF TG_OP = 'UPDATE' THEN
      NEW.account_scope_id := OLD.account_scope_id;
      NEW.account_scope_verified := OLD.account_scope_verified;
      NEW.account_scope_source := OLD.account_scope_source;
      NEW.account_scope_verified_at := OLD.account_scope_verified_at;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tr_api_key_metadata_account_scope_protect ON public.api_key_metadata;
CREATE TRIGGER tr_api_key_metadata_account_scope_protect
BEFORE INSERT OR UPDATE ON public.api_key_metadata
FOR EACH ROW EXECUTE FUNCTION public.protect_account_scope_metadata();
