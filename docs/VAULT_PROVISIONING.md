# Credential Vault Database Role & User Provisioning

This document outlines the security architecture and deployment procedure for the RagBait M2 Credential Vault.

---

## 1. Role Architecture & Separation of Concerns

The credential vault uses strict separation between the **database capability role** and the **database login user**:

| Role Name | Type | Privileges & Scope | Provisioning Method |
| :--- | :--- | :--- | :--- |
| `ragbait_vault_service` | `NOLOGIN`, `NOBYPASSRLS` | Owns DDL grants on `private.api_key_secrets`, `public.api_key_metadata`, and `USAGE` on schema `private`. Zero access to `public.profiles`, `public.audit_logs`, or other application tables. | Defined in migration `20260202000000_m2_credential_vault.sql`. Never has a password. |
| `ragbait_vault_user` | `LOGIN`, `NOBYPASSRLS` | Member of `ragbait_vault_service`. Inherits vault capabilities; cannot bypass RLS; cannot access user profiles or system audit logs. | Provisioned out-of-band via secure operational script (`scripts/provision_vault_user.py` for staging/production) or `supabase/roles.sql` for local dev. |

---

## 2. Local Development Provisioning

For local Supabase development:
1. `supabase/roles.sql` creates the local login user `ragbait_vault_user`.
2. `supabase/seed.sql` grants `ragbait_vault_service` to `ragbait_vault_user`.
3. `VAULT_DATABASE_URL` defaults to:
   ```
   postgresql://ragbait_vault_user:vault_service_secret_pass@127.0.0.1:54322/postgres
   ```

---

## 3. Staging and Production Provisioning

In staging and production, **passwords are never committed to git or included in database migrations**.

### Step 1: Run Database Migrations
Migrations define the `ragbait_vault_service` capability role with `NOLOGIN`:
```bash
npx supabase db push
```

### Step 2: Provision Vault Login User
Run the provisioning script using an administrative connection (e.g. from a secure bastion or CI runner with temporary administrative credentials):
```bash
python scripts/provision_vault_user.py \
  --admin-dsn "postgresql://postgres:$ADMIN_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" \
  --username "ragbait_vault_user"
```

The script will:
- Generate a 36-character cryptographically secure password (or use `--password` from your secret manager).
- Create or alter the user with `LOGIN NOBYPASSRLS`.
- Grant `ragbait_vault_service` to `ragbait_vault_user`.

### Step 3: Configure Application Secrets
Export the resulting connection DSN into your environment's secret manager (e.g., Google Cloud Secret Manager / AWS Secrets Manager):
```bash
VAULT_DATABASE_URL="postgresql://ragbait_vault_user:$GENERATED_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
```

### Step 4: Verify Privileges and Isolation
Verify that the provisioned user has only the required access:
```bash
python scripts/provision_vault_user.py \
  --verify-only \
  --vault-dsn "$VAULT_DATABASE_URL"
```

The verification checks confirm:
1. `pg_has_role(current_user, 'ragbait_vault_service', 'MEMBER')` is `true`.
2. `rolbypassrls` is `false`.
3. Access to `private.api_key_secrets` and `public.api_key_metadata` is permitted.
4. Access to `public.profiles` is denied (`permission denied for table profiles`).
5. Access to `public.audit_logs` is denied (`permission denied for table audit_logs`).
