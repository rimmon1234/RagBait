-- ==============================================================================
-- RagBait Initial Schema Migration: M0 Foundation & M1 Authentication
-- ==============================================================================

-- 1. Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------------------------
-- 2. Private Schema Isolation
-- ------------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS app_private;
REVOKE ALL ON SCHEMA app_private FROM PUBLIC, anon;
GRANT USAGE ON SCHEMA app_private TO authenticated, service_role, postgres;

-- ------------------------------------------------------------------------------
-- 3. Generic Helper Functions (SECURITY INVOKER)
-- ------------------------------------------------------------------------------

-- Generic updated_at timestamp refresher (SECURITY INVOKER)
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  NEW.updated_at = pg_catalog.now();
  RETURN NEW;
END;
$$;

-- ------------------------------------------------------------------------------
-- 4. Profiles Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email text NOT NULL,
  display_name text NOT NULL,
  role text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin', 'super_admin')),
  approval_status text NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('pending', 'approved', 'rejected', 'suspended')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_profiles_email ON public.profiles(email);
CREATE INDEX idx_profiles_role ON public.profiles(role);
CREATE INDEX idx_profiles_status ON public.profiles(approval_status);

CREATE TRIGGER tr_profiles_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Trigger preventing non-admin modification of role and approval_status
-- STRICTLY SECURITY INVOKER to observe effective caller context
CREATE OR REPLACE FUNCTION public.protect_profile_roles_and_status()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  caller_role text;
BEGIN
  IF (OLD.role IS DISTINCT FROM NEW.role OR OLD.approval_status IS DISTINCT FROM NEW.approval_status) THEN
    SELECT current_user INTO caller_role;

    -- Only postgres superuser, supabase_admin, or service_role is allowed to alter role and approval_status
    IF (caller_role NOT IN ('postgres', 'supabase_admin', 'service_role')) THEN
      RAISE EXCEPTION 'Unauthorized: Caller (user=%) cannot modify role or approval_status', caller_role
        USING ERRCODE = '42501'; -- insufficient_privilege
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER tr_protect_profile_roles_and_status
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.protect_profile_roles_and_status();

-- Column-level permissions: users can ONLY update display_name
REVOKE UPDATE ON public.profiles FROM authenticated;
GRANT SELECT ON public.profiles TO authenticated;
GRANT UPDATE (display_name) ON public.profiles TO authenticated;

-- ------------------------------------------------------------------------------
-- 5. Non-recursive Admin Check Helper (SECURITY DEFINER in app_private)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app_private.is_admin(user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = user_id
      AND role IN ('admin', 'super_admin')
      AND approval_status = 'approved'
  );
$$;

-- Restrict execution of is_admin to authorized roles
REVOKE ALL ON FUNCTION app_private.is_admin(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app_private.is_admin(uuid) TO authenticated, service_role, postgres;

-- ------------------------------------------------------------------------------
-- 6. Access Requests Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.access_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  decision_reason text,
  reviewed_by uuid REFERENCES public.profiles(id),
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Partial unique index ensures at most one pending request at any time, while preserving history
CREATE UNIQUE INDEX idx_access_requests_user_pending ON public.access_requests (user_id) WHERE status = 'pending';
CREATE INDEX idx_access_requests_status ON public.access_requests(status);
CREATE INDEX idx_access_requests_user_id ON public.access_requests(user_id);

CREATE TRIGGER tr_access_requests_updated_at
BEFORE UPDATE ON public.access_requests
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

GRANT SELECT, INSERT ON public.access_requests TO authenticated;
REVOKE UPDATE, DELETE ON public.access_requests FROM authenticated;

-- ------------------------------------------------------------------------------
-- 7. User Presence Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.user_presence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  session_id text NOT NULL,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  client_info jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT uq_user_presence_session UNIQUE (user_id, session_id)
);

CREATE INDEX idx_presence_expires_at ON public.user_presence(expires_at);
CREATE INDEX idx_presence_last_seen ON public.user_presence(last_seen_at DESC);

GRANT SELECT, INSERT, UPDATE ON public.user_presence TO authenticated;
REVOKE DELETE ON public.user_presence FROM authenticated;

-- ------------------------------------------------------------------------------
-- 8. Audit Logs Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id uuid REFERENCES public.profiles(id),
  action text NOT NULL,
  target_type text NOT NULL,
  target_id uuid,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_actor_id ON public.audit_logs(actor_id);
CREATE INDEX idx_audit_logs_target_id ON public.audit_logs(target_id);
CREATE INDEX idx_audit_logs_created_at ON public.audit_logs(created_at DESC);

GRANT SELECT ON public.audit_logs TO authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.audit_logs FROM authenticated;

-- ------------------------------------------------------------------------------
-- 9. Auth User Trigger (SECURITY DEFINER, executes in trigger context only)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  extracted_name text;
BEGIN
  extracted_name := coalesce(
    NEW.raw_user_meta_data->>'full_name',
    NEW.raw_user_meta_data->>'name',
    pg_catalog.split_part(NEW.email, '@', 1)
  );

  INSERT INTO public.profiles (id, email, display_name, role, approval_status)
  VALUES (
    NEW.id,
    NEW.email,
    extracted_name,
    'user',
    'pending'
  )
  ON CONFLICT (id) DO NOTHING;

  RETURN NEW;
END;
$$;

-- Revoke direct execution from clients
REVOKE ALL ON FUNCTION public.handle_new_user() FROM PUBLIC, anon, authenticated;

-- Triggered after new user insert in auth.users
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ------------------------------------------------------------------------------
-- 10. Row Level Security Policies (Using app_private.is_admin)
-- ------------------------------------------------------------------------------
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.access_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_presence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- Profiles Policies
CREATE POLICY profiles_select_own_or_admin
ON public.profiles FOR SELECT
USING (auth.uid() = id OR app_private.is_admin(auth.uid()));

CREATE POLICY profiles_update_own
ON public.profiles FOR UPDATE
USING (auth.uid() = id)
WITH CHECK (auth.uid() = id);

-- Access Requests Policies
CREATE POLICY access_requests_select_own_or_admin
ON public.access_requests FOR SELECT
USING (auth.uid() = user_id OR app_private.is_admin(auth.uid()));

CREATE POLICY access_requests_insert_own
ON public.access_requests FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- User Presence Policies
CREATE POLICY user_presence_select_own_or_admin
ON public.user_presence FOR SELECT
USING (auth.uid() = user_id OR app_private.is_admin(auth.uid()));

CREATE POLICY user_presence_insert_own
ON public.user_presence FOR INSERT
WITH CHECK (auth.uid() = user_id);

CREATE POLICY user_presence_update_own
ON public.user_presence FOR UPDATE
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

-- Audit Logs Policies
CREATE POLICY audit_logs_select_admin_only
ON public.audit_logs FOR SELECT
USING (app_private.is_admin(auth.uid()));
