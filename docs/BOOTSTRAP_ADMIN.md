# RagBait Super-Admin Bootstrapping Procedure

## Overview

To guarantee production security, RagBait contains **no public setup endpoint**, auto-promotion endpoint, or initial admin creation route.

The initial `super_admin` must be explicitly bootstrapped by the database owner executing a verified SQL command directly in PostgreSQL.

---

## Prerequisites

1. The target administrator must register an account through RagBait (`/register`).
2. The user must verify their email address via OTP or Magic Link (`email_confirmed_at` must not be null).
3. The database operator must obtain the user's Auth UUID from `auth.users` or the Supabase dashboard.

---

## Verified Bootstrap SQL Script

Execute the following SQL transaction in the Supabase SQL editor or direct psql connection:

```sql
DO $$
DECLARE
  target_user_id uuid := 'REPLACE_WITH_TARGET_USER_UUID';
  target_user_email text;
  is_confirmed boolean;
  existing_super_admin_count integer;
BEGIN
  -- 1. Verify target user exists and is email-verified
  SELECT email, (email_confirmed_at IS NOT NULL)
  INTO target_user_email, is_confirmed
  FROM auth.users
  WHERE id = target_user_id;

  IF target_user_email IS NULL THEN
    RAISE EXCEPTION 'Target user UUID % does not exist in auth.users', target_user_id;
  END IF;

  IF NOT is_confirmed THEN
    RAISE EXCEPTION 'Target user % has not confirmed their email address', target_user_email;
  END IF;

  -- 2. Check for existing super-admins
  SELECT count(*)
  INTO existing_super_admin_count
  FROM public.profiles
  WHERE role = 'super_admin';

  IF existing_super_admin_count > 0 THEN
    RAISE NOTICE 'A super_admin already exists. Proceeding with promotion for %.', target_user_email;
  END IF;

  -- 3. Promote profile to super_admin and approved
  UPDATE public.profiles
  SET
    role = 'super_admin',
    approval_status = 'approved',
    updated_at = now()
  WHERE id = target_user_id;

  -- 4. Mark or insert access request as approved
  INSERT INTO public.access_requests (user_id, status, decision_reason, reviewed_by, reviewed_at)
  VALUES (target_user_id, 'approved', 'Initial super-admin bootstrap', target_user_id, now())
  ON CONFLICT (user_id) WHERE status = 'pending'
  DO UPDATE SET
    status = 'approved',
    decision_reason = 'Initial super-admin bootstrap',
    reviewed_by = target_user_id,
    reviewed_at = now();

  -- 5. Record initial audit log entry
  INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
  VALUES (
    target_user_id,
    'user.bootstrap_super_admin',
    'user',
    target_user_id,
    jsonb_build_object('email', target_user_email, 'executed_by', current_user)
  );

  RAISE NOTICE 'Successfully bootstrapped user % (%) to super_admin.', target_user_email, target_user_id;
END $$;
```

---

## Verification

After executing:
1. Verify profile status:
   ```sql
   SELECT id, email, role, approval_status FROM public.profiles WHERE id = 'REPLACE_WITH_TARGET_USER_UUID';
   ```
   Should display `role = 'super_admin'` and `approval_status = 'approved'`.
2. Target user logs in at `/login` and navigates to `/admin`.
