-- ==============================================================================
-- RagBait Database & Security Regression Tests
-- ==============================================================================

BEGIN;
SELECT plan(16);

-- 1. Table existence
SELECT has_table('public', 'profiles', 'public.profiles should exist');
SELECT has_table('public', 'access_requests', 'public.access_requests should exist');
SELECT has_table('public', 'user_presence', 'public.user_presence should exist');
SELECT has_table('public', 'audit_logs', 'public.audit_logs should exist');

-- 2. Row Level Security active
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'profiles' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on profiles'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'access_requests' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on access_requests'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'user_presence' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on user_presence'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'audit_logs' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on audit_logs'
);

-- 3. Column-level privileges on public.profiles
SELECT is(
  has_column_privilege('authenticated', 'public.profiles', 'display_name', 'UPDATE'),
  true,
  'authenticated role should have UPDATE on display_name'
);

SELECT is(
  has_column_privilege('authenticated', 'public.profiles', 'role', 'UPDATE'),
  false,
  'authenticated role should NOT have UPDATE on role'
);

SELECT is(
  has_column_privilege('authenticated', 'public.profiles', 'approval_status', 'UPDATE'),
  false,
  'authenticated role should NOT have UPDATE on approval_status'
);

-- 4. Function Security Classification & Search Path Verification
-- app_private.is_admin must be SECURITY DEFINER with search_path = ''
SELECT is(
  (SELECT p.prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE p.proname = 'is_admin' AND n.nspname = 'app_private'),
  true,
  'app_private.is_admin must be SECURITY DEFINER'
);

SELECT is(
  (SELECT p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE p.proname = 'is_admin' AND n.nspname = 'app_private'),
  ARRAY['search_path=""']::text[],
  'app_private.is_admin must use empty search_path'
);

-- protect_profile_roles_and_status must be SECURITY INVOKER (prosecdef = false)
SELECT is(
  (SELECT p.prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE p.proname = 'protect_profile_roles_and_status' AND n.nspname = 'public'),
  false,
  'protect_profile_roles_and_status must be SECURITY INVOKER'
);

-- 5. Execution Privilege Restrictions
-- anon cannot execute app_private.is_admin
SELECT is(
  has_function_privilege('anon', 'app_private.is_admin(uuid)', 'EXECUTE'),
  false,
  'anon role must not have EXECUTE privilege on app_private.is_admin'
);

-- authenticated cannot directly execute handle_new_user
SELECT is(
  has_function_privilege('authenticated', 'public.handle_new_user()', 'EXECUTE'),
  false,
  'authenticated role must not have EXECUTE privilege on handle_new_user'
);

SELECT * FROM finish();
ROLLBACK;
