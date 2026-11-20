-- ==============================================================================
-- RagBait M2.8 Migration: Shared Community Local Inference & Ollama Node Agent
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Community Inference Nodes Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.community_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 2 AND 64),
  public_key text NOT NULL UNIQUE CHECK (public_key ~ '^[0-9a-f]{64}$'),
  key_version text NOT NULL DEFAULT 'ed25519-v1',
  agent_version text NOT NULL DEFAULT '0.1.0',
  os text,
  architecture text,
  ollama_version text,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'active', 'paused', 'offline', 'revoked')),
  sharing_enabled boolean NOT NULL DEFAULT false,
  max_concurrent_jobs integer NOT NULL DEFAULT 1 CHECK (max_concurrent_jobs BETWEEN 1 AND 4),
  max_execution_duration integer NOT NULL DEFAULT 300 CHECK (max_execution_duration BETWEEN 30 AND 600),
  last_seen_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);

CREATE INDEX idx_community_nodes_owner ON public.community_nodes(owner_id);
CREATE INDEX idx_community_nodes_status ON public.community_nodes(status);
CREATE INDEX idx_community_nodes_sharing ON public.community_nodes(sharing_enabled) WHERE sharing_enabled = true;

CREATE TRIGGER tr_community_nodes_updated_at
BEFORE UPDATE ON public.community_nodes
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 2. Explicit Model Sharing Allowlist Table (Zero Implicit Sharing)
-- ------------------------------------------------------------------------------
CREATE TABLE public.community_node_models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL REFERENCES public.community_nodes(id) ON DELETE CASCADE,
  model_name text NOT NULL CHECK (char_length(model_name) BETWEEN 2 AND 128),
  enabled boolean NOT NULL DEFAULT false,
  parameter_size text,
  quantization_level text,
  size_bytes bigint,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_community_node_model UNIQUE (node_id, model_name)
);

CREATE INDEX idx_community_node_models_node ON public.community_node_models(node_id);
CREATE INDEX idx_community_node_models_enabled ON public.community_node_models(model_name) WHERE enabled = true;

CREATE TRIGGER tr_community_node_models_updated_at
BEFORE UPDATE ON public.community_node_models
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 3. Pairing Codes Table (Single-Use, Short Expiry)
-- ------------------------------------------------------------------------------
CREATE TABLE public.community_node_pairing_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  code_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  used boolean NOT NULL DEFAULT false,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_community_pairing_owner ON public.community_node_pairing_codes(owner_id);
CREATE INDEX idx_community_pairing_hash ON public.community_node_pairing_codes(code_hash);
CREATE INDEX idx_community_pairing_expires ON public.community_node_pairing_codes(expires_at) WHERE used = false;

-- ------------------------------------------------------------------------------
-- 4. Community Inference Jobs Table (Authoritative State & Metadata Only)
-- ------------------------------------------------------------------------------
CREATE TABLE public.community_inference_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  node_id uuid NOT NULL REFERENCES public.community_nodes(id) ON DELETE RESTRICT,
  model_name text NOT NULL,
  status text NOT NULL DEFAULT 'QUEUED'
    CHECK (status IN ('QUEUED', 'CLAIMED', 'SENT', 'AGENT_ACCEPTED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'OUTCOME_UNKNOWN')),
  payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  authorization_key_id text NOT NULL DEFAULT 'k1',
  consent_policy_version text NOT NULL DEFAULT '2026-02-community-v1',
  accepted_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz,
  lease_expires_at timestamptz,
  hard_deadline_at timestamptz NOT NULL,
  failure_code text,
  failure_details text,
  reported_input_tokens integer CHECK (reported_input_tokens IS NULL OR reported_input_tokens >= 0),
  reported_output_tokens integer CHECK (reported_output_tokens IS NULL OR reported_output_tokens >= 0),
  duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
  tokens_per_second numeric(7, 2),
  usage_provenance text NOT NULL DEFAULT 'device_reported'
    CHECK (usage_provenance IN ('device_reported', 'estimated', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_community_jobs_requester ON public.community_inference_jobs(requester_id);
CREATE INDEX idx_community_jobs_node ON public.community_inference_jobs(node_id);
CREATE INDEX idx_community_jobs_status ON public.community_inference_jobs(status);
CREATE INDEX idx_community_jobs_created ON public.community_inference_jobs(created_at DESC);

CREATE TRIGGER tr_community_inference_jobs_updated_at
BEFORE UPDATE ON public.community_inference_jobs
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 5. Community Node Audit Events Table (Tamper-Resistant)
-- ------------------------------------------------------------------------------
CREATE TABLE public.community_node_audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id uuid REFERENCES public.community_nodes(id) ON DELETE SET NULL,
  actor_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  event_type text NOT NULL
    CHECK (event_type IN ('node_paired', 'node_paused', 'node_resumed', 'node_revoked', 'sharing_toggled', 'models_updated', 'auth_failed', 'policy_violation', 'job_cancelled')),
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_community_audit_node ON public.community_node_audit_events(node_id);
CREATE INDEX idx_community_audit_actor ON public.community_node_audit_events(actor_id);
CREATE INDEX idx_community_audit_created ON public.community_node_audit_events(created_at DESC);

CREATE TRIGGER tr_community_node_audit_events_immutable
BEFORE UPDATE OR DELETE ON public.community_node_audit_events
FOR EACH ROW EXECUTE FUNCTION public.prevent_ledger_tampering();

-- ------------------------------------------------------------------------------
-- 6. Grants & Permissions
-- ------------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON public.community_nodes TO authenticated;
REVOKE DELETE ON public.community_nodes FROM authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.community_node_models TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.community_node_pairing_codes TO authenticated;
REVOKE DELETE ON public.community_node_pairing_codes FROM authenticated;

GRANT SELECT ON public.community_inference_jobs TO authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.community_inference_jobs FROM authenticated;

GRANT SELECT ON public.community_node_audit_events TO authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.community_node_audit_events FROM authenticated;

-- ------------------------------------------------------------------------------
-- 7. Row Level Security Policies
-- ------------------------------------------------------------------------------
ALTER TABLE public.community_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.community_node_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.community_node_pairing_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.community_inference_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.community_node_audit_events ENABLE ROW LEVEL SECURITY;

-- community_nodes: owner or admin can read; owner can update configuration
CREATE POLICY community_nodes_select_own_or_admin
ON public.community_nodes FOR SELECT
USING (auth.uid() = owner_id OR app_private.is_admin(auth.uid()));

CREATE POLICY community_nodes_insert_own
ON public.community_nodes FOR INSERT
WITH CHECK (auth.uid() = owner_id);

CREATE POLICY community_nodes_update_own
ON public.community_nodes FOR UPDATE
USING (auth.uid() = owner_id)
WITH CHECK (auth.uid() = owner_id);

-- community_node_models: owner can CRUD; approved users can query active models of sharing-enabled nodes
CREATE POLICY community_node_models_select
ON public.community_node_models FOR SELECT
USING (
  node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid())
  OR app_private.is_admin(auth.uid())
  OR (enabled = true AND node_id IN (
    SELECT id FROM public.community_nodes
    WHERE sharing_enabled = true AND status = 'active'
  ))
);

CREATE POLICY community_node_models_insert_own
ON public.community_node_models FOR INSERT
WITH CHECK (node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid()));

CREATE POLICY community_node_models_update_own
ON public.community_node_models FOR UPDATE
USING (node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid()))
WITH CHECK (node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid()));

CREATE POLICY community_node_models_delete_own
ON public.community_node_models FOR DELETE
USING (node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid()));

-- community_node_pairing_codes: owner only
CREATE POLICY community_pairing_codes_select_own
ON public.community_node_pairing_codes FOR SELECT
USING (auth.uid() = owner_id OR app_private.is_admin(auth.uid()));

CREATE POLICY community_pairing_codes_insert_own
ON public.community_node_pairing_codes FOR INSERT
WITH CHECK (auth.uid() = owner_id);

CREATE POLICY community_pairing_codes_update_own
ON public.community_node_pairing_codes FOR UPDATE
USING (auth.uid() = owner_id)
WITH CHECK (auth.uid() = owner_id);

-- community_inference_jobs: requester or node owner or admin can read
CREATE POLICY community_inference_jobs_select
ON public.community_inference_jobs FOR SELECT
USING (
  requester_id = auth.uid()
  OR node_id IN (SELECT id FROM public.community_nodes WHERE owner_id = auth.uid())
  OR app_private.is_admin(auth.uid())
);

-- community_node_audit_events: actor or admin can read
CREATE POLICY community_node_audit_events_select
ON public.community_node_audit_events FOR SELECT
USING (actor_id = auth.uid() OR app_private.is_admin(auth.uid()));
