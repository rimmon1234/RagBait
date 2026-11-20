-- ==============================================================================
-- RagBait Migration: M2.8 Community Local Compute Deprecation
--
-- Authoritative Product Policy:
-- A student's locally installed Ollama models and local computing resources
-- must remain exclusively available to that student. RagBait strictly prohibits
-- donating, pooling, sharing, or exposing local models or Ollama instances
-- for use by other students.
--
-- Cloud API credential pooling (M2.2, M2.3) remains fully intact and functional
-- for explicitly contributed, provider-approved free-tier credentials.
-- ==============================================================================

-- 1. Deactivate all existing community nodes
UPDATE public.community_nodes
SET status = 'offline',
    sharing_enabled = false,
    active_session_id = NULL;

-- 2. Expire all pending pairing codes immediately
UPDATE public.community_node_pairing_codes
SET expires_at = now()
WHERE expires_at > now();

-- 3. Mark any non-terminal community inference jobs as failed
UPDATE public.community_inference_jobs
SET status = 'FAILED',
    failure_details = 'Feature discontinued: Local model sharing has been disabled in RagBait product architecture'
WHERE status IN ('QUEUED', 'DISPATCHED', 'PROCESSING');

-- 4. Enforce at Row-Level Security that NO user can create new community nodes or pairing codes
DROP POLICY IF EXISTS "community_nodes_insert_own" ON public.community_nodes;
CREATE POLICY "community_nodes_insert_disabled"
  ON public.community_nodes
  FOR INSERT
  WITH CHECK (false);

DROP POLICY IF EXISTS "community_pairing_codes_insert_own" ON public.community_node_pairing_codes;
CREATE POLICY "community_pairing_codes_insert_disabled"
  ON public.community_node_pairing_codes
  FOR INSERT
  WITH CHECK (false);

DROP POLICY IF EXISTS "community_node_models_insert_own" ON public.community_node_models;
CREATE POLICY "community_node_models_insert_disabled"
  ON public.community_node_models
  FOR INSERT
  WITH CHECK (false);

-- 5. Record deprecation audit event in community_node_audit_events if any node existed
INSERT INTO public.community_node_audit_events (
    node_id,
    event_type,
    details
)
SELECT id, 'NODE_DEPRECATED', '{"reason": "Product architecture correction: Private local inference only"}'::jsonb
FROM public.community_nodes;
