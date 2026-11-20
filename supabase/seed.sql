-- ==============================================================================
-- RagBait Local Development Seed
-- ==============================================================================
-- Grant vault capability role to local development login role

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_user')
     AND EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_service') THEN
    GRANT ragbait_vault_service TO ragbait_vault_user;
  END IF;
END $$;
