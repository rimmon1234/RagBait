-- ==============================================================================
-- RagBait Local Development Global Roles (Supabase DB Reset Globals)
-- ==============================================================================
-- In production, the login role is provisioned securely through an out-of-band
-- secret management operation (e.g. scripts/provision_vault_user.py).
-- For local development, this file provisions the local login role.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_user') THEN
    CREATE ROLE ragbait_vault_user WITH LOGIN PASSWORD 'vault_service_secret_pass';
  ELSE
    ALTER ROLE ragbait_vault_user WITH LOGIN PASSWORD 'vault_service_secret_pass';
  END IF;
END $$;
