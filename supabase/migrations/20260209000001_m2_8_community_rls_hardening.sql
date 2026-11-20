-- ==============================================================================
-- RagBait M2.8.2 — Community Inference RLS & Privilege Hardening Migration
-- ==============================================================================

-- 1. Lock down public.community_nodes
REVOKE ALL ON public.community_nodes FROM authenticated;
GRANT SELECT ON public.community_nodes TO authenticated;
GRANT UPDATE (display_name, sharing_enabled, max_concurrent_jobs, max_execution_duration)
ON public.community_nodes TO authenticated;

-- Add session tracking columns to community_nodes
ALTER TABLE public.community_nodes
ADD COLUMN IF NOT EXISTS active_session_id text,
ADD COLUMN IF NOT EXISTS session_epoch integer NOT NULL DEFAULT 0;

-- 2. Lock down public.community_node_pairing_codes
REVOKE ALL ON public.community_node_pairing_codes FROM authenticated;
GRANT SELECT ON public.community_node_pairing_codes TO authenticated;

-- Ensure code_hash column exists (created in initial M2.8 schema)
ALTER TABLE public.community_node_pairing_codes
ADD COLUMN IF NOT EXISTS code_hash text UNIQUE;

-- 3. Lock down public.community_inference_jobs
REVOKE ALL ON public.community_inference_jobs FROM authenticated;
GRANT SELECT ON public.community_inference_jobs TO authenticated;

-- Add Redis run_id for volatile queue restart and crash recovery
ALTER TABLE public.community_inference_jobs
ADD COLUMN IF NOT EXISTS redis_run_id text;

-- 4. Defense-in-depth trigger: Protect server-managed node fields from PostgREST modification
CREATE OR REPLACE FUNCTION public.protect_community_node_server_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF (OLD.owner_id IS DISTINCT FROM NEW.owner_id) OR
       (OLD.public_key IS DISTINCT FROM NEW.public_key) OR
       (OLD.key_version IS DISTINCT FROM NEW.key_version) OR
       (OLD.status IS DISTINCT FROM NEW.status) OR
       (OLD.last_seen_at IS DISTINCT FROM NEW.last_seen_at) OR
       (OLD.active_session_id IS DISTINCT FROM NEW.active_session_id) OR
       (OLD.session_epoch IS DISTINCT FROM NEW.session_epoch) THEN
        IF current_user = 'authenticated' THEN
            RAISE EXCEPTION 'Direct modification of server-managed node fields is prohibited';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = '';

DROP TRIGGER IF EXISTS tr_community_nodes_protect_server_fields ON public.community_nodes;
CREATE TRIGGER tr_community_nodes_protect_server_fields
BEFORE UPDATE ON public.community_nodes
FOR EACH ROW EXECUTE FUNCTION public.protect_community_node_server_fields();

-- 5. Defense-in-depth trigger: Prevent terminal job status mutation
CREATE OR REPLACE FUNCTION public.protect_terminal_job_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'OUTCOME_UNKNOWN') THEN
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'Cannot update job % from terminal status % to %', OLD.id, OLD.status, NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = '';

DROP TRIGGER IF EXISTS tr_community_jobs_terminal_guard ON public.community_inference_jobs;
CREATE TRIGGER tr_community_jobs_terminal_guard
BEFORE UPDATE ON public.community_inference_jobs
FOR EACH ROW EXECUTE FUNCTION public.protect_terminal_job_status();
