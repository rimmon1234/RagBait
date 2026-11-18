-- ==============================================================================
-- RagBait M2.4 Migration: Admission Control, Multi-Tier Rate Limiting & Usage Ledger
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Usage Reservations Table (Durable lifecycle record per admission attempt)
-- ------------------------------------------------------------------------------
CREATE TABLE public.llm_usage_reservations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  credential_id uuid NOT NULL REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  provider_id text NOT NULL REFERENCES public.llm_providers(id) ON DELETE RESTRICT,
  model_id text NOT NULL REFERENCES public.llm_models(id) ON DELETE RESTRICT,
  idempotency_key text NOT NULL,
  request_id text NOT NULL,
  account_scope_id text,
  status text NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved', 'dispatched', 'settled', 'released', 'outcome_unknown', 'reconciliation_required', 'expired')),
  estimated_input_tokens integer NOT NULL DEFAULT 0 CHECK (estimated_input_tokens >= 0),
  estimated_output_tokens integer NOT NULL DEFAULT 0 CHECK (estimated_output_tokens >= 0),
  estimated_cost_cents integer NOT NULL DEFAULT 0 CHECK (estimated_cost_cents >= 0),
  actual_input_tokens integer CHECK (actual_input_tokens IS NULL OR actual_input_tokens >= 0),
  actual_output_tokens integer CHECK (actual_output_tokens IS NULL OR actual_output_tokens >= 0),
  actual_cost_cents integer CHECK (actual_cost_cents IS NULL OR actual_cost_cents >= 0),
  currency text NOT NULL DEFAULT 'USD',
  pricing_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  lease_token text,
  lease_expires_at timestamptz,
  dispatched_at timestamptz,
  settled_at timestamptz,
  error_category text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_reservation_user_idempotency UNIQUE (user_id, idempotency_key)
);

CREATE INDEX idx_llm_reservations_user ON public.llm_usage_reservations(user_id);
CREATE INDEX idx_llm_reservations_cred ON public.llm_usage_reservations(credential_id);
CREATE INDEX idx_llm_reservations_status ON public.llm_usage_reservations(status);
CREATE INDEX idx_llm_reservations_created ON public.llm_usage_reservations(created_at);

CREATE TRIGGER tr_llm_usage_reservations_updated_at
BEFORE UPDATE ON public.llm_usage_reservations
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 2. Usage Events Table (Append-only immutable accounting event log)
-- ------------------------------------------------------------------------------
CREATE TABLE public.llm_usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reservation_id uuid NOT NULL REFERENCES public.llm_usage_reservations(id) ON DELETE CASCADE,
  event_type text NOT NULL
    CHECK (event_type IN ('reservation_created', 'dispatch_committed', 'reservation_released', 'usage_settled', 'outcome_unknown', 'usage_reconciled', 'reservation_expired')),
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  credential_id uuid NOT NULL REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  tokens_delta integer NOT NULL DEFAULT 0,
  cost_cents_delta integer NOT NULL DEFAULT 0,
  currency text NOT NULL DEFAULT 'USD',
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_events_reservation ON public.llm_usage_events(reservation_id);
CREATE INDEX idx_llm_events_user ON public.llm_usage_events(user_id);
CREATE INDEX idx_llm_events_cred ON public.llm_usage_events(credential_id);

-- Enforce immutability: block UPDATE and DELETE
CREATE OR REPLACE FUNCTION public.prevent_ledger_tampering()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  RAISE EXCEPTION 'Historical usage events are immutable and cannot be updated or deleted'
    USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER tr_llm_usage_events_immutable
BEFORE UPDATE OR DELETE ON public.llm_usage_events
FOR EACH ROW EXECUTE FUNCTION public.prevent_ledger_tampering();

-- ------------------------------------------------------------------------------
-- 3. Usage Budget Counters Table (Row-locked aggregates for daily/monthly limits)
-- ------------------------------------------------------------------------------
CREATE TABLE public.llm_usage_budget_counters (
  credential_id uuid NOT NULL REFERENCES public.api_key_metadata(id) ON DELETE CASCADE,
  period_type text NOT NULL CHECK (period_type IN ('daily', 'monthly')),
  period_start timestamptz NOT NULL,
  reserved_requests integer NOT NULL DEFAULT 0 CHECK (reserved_requests >= 0),
  dispatched_requests integer NOT NULL DEFAULT 0 CHECK (dispatched_requests >= 0),
  reserved_cost_cents integer NOT NULL DEFAULT 0 CHECK (reserved_cost_cents >= 0),
  actual_cost_cents integer NOT NULL DEFAULT 0 CHECK (actual_cost_cents >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (credential_id, period_type, period_start)
);

CREATE TRIGGER tr_llm_usage_budget_counters_updated_at
BEFORE UPDATE ON public.llm_usage_budget_counters
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 4. Permissions & Row Level Security (RLS)
-- ------------------------------------------------------------------------------
ALTER TABLE public.llm_usage_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_usage_budget_counters ENABLE ROW LEVEL SECURITY;

-- Select policies: Users can view their own reservations and events
CREATE POLICY llm_usage_reservations_user_select ON public.llm_usage_reservations
  FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY llm_usage_reservations_cred_owner_select ON public.llm_usage_reservations
  FOR SELECT TO authenticated
  USING (credential_id IN (SELECT id FROM public.api_key_metadata WHERE owner_id = auth.uid()));

CREATE POLICY llm_usage_events_user_select ON public.llm_usage_events
  FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY llm_usage_budget_counters_owner_select ON public.llm_usage_budget_counters
  FOR SELECT TO authenticated
  USING (credential_id IN (SELECT id FROM public.api_key_metadata WHERE owner_id = auth.uid()));

-- Admins can view all usage for monitoring
CREATE POLICY llm_usage_reservations_admin_select ON public.llm_usage_reservations
  FOR SELECT TO authenticated
  USING (app_private.is_admin(auth.uid()));

CREATE POLICY llm_usage_events_admin_select ON public.llm_usage_events
  FOR SELECT TO authenticated
  USING (app_private.is_admin(auth.uid()));

CREATE POLICY llm_usage_budget_counters_admin_select ON public.llm_usage_budget_counters
  FOR SELECT TO authenticated
  USING (app_private.is_admin(auth.uid()));

-- Revoke all from anon and public
REVOKE ALL ON TABLE public.llm_usage_reservations FROM anon, PUBLIC;
REVOKE ALL ON TABLE public.llm_usage_events FROM anon, PUBLIC;
REVOKE ALL ON TABLE public.llm_usage_budget_counters FROM anon, PUBLIC;

-- Authenticated users can read their authorized rows via RLS
GRANT SELECT ON public.llm_usage_reservations TO authenticated;
GRANT SELECT ON public.llm_usage_events TO authenticated;
GRANT SELECT ON public.llm_usage_budget_counters TO authenticated;
