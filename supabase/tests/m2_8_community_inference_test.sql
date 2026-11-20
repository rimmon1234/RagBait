-- ==============================================================================
-- RagBait M2.8 Community Local Inference Database pgTAP Tests
-- ==============================================================================

BEGIN;
SELECT plan(29);

-- 1. Table existence
SELECT has_table('public', 'community_nodes', 'public.community_nodes should exist');
SELECT has_table('public', 'community_node_models', 'public.community_node_models should exist');
SELECT has_table('public', 'community_node_pairing_codes', 'public.community_node_pairing_codes should exist');
SELECT has_table('public', 'community_inference_jobs', 'public.community_inference_jobs should exist');
SELECT has_table('public', 'community_node_audit_events', 'public.community_node_audit_events should exist');

-- 2. Row Level Security active on all 5 community tables
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'community_nodes' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on community_nodes'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'community_node_models' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on community_node_models'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'community_node_pairing_codes' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on community_node_pairing_codes'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'community_inference_jobs' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on community_inference_jobs'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'community_node_audit_events' AND n.nspname = 'public'),
  true,
  'RLS should be enabled on community_node_audit_events'
);

-- 3. Key column structure
SELECT has_column('public', 'community_nodes', 'public_key', 'community_nodes must have public_key');
SELECT has_column('public', 'community_nodes', 'sharing_enabled', 'community_nodes must have sharing_enabled');
SELECT has_column('public', 'community_nodes', 'active_session_id', 'community_nodes must have active_session_id');
SELECT has_column('public', 'community_nodes', 'session_epoch', 'community_nodes must have session_epoch');
SELECT has_column('public', 'community_node_pairing_codes', 'code_hash', 'community_node_pairing_codes must have code_hash');
SELECT has_column('public', 'community_inference_jobs', 'payload_digest', 'community_inference_jobs must have payload_digest');
SELECT has_column('public', 'community_inference_jobs', 'usage_provenance', 'community_inference_jobs must have usage_provenance');

-- 4. Privilege boundaries (SEC-03)
SELECT is(
  has_table_privilege('authenticated', 'public.community_nodes', 'INSERT'),
  false,
  'authenticated role should NOT have direct INSERT on community_nodes'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_nodes', 'DELETE'),
  false,
  'authenticated role should NOT have direct DELETE on community_nodes'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_node_pairing_codes', 'INSERT'),
  false,
  'authenticated role should NOT have direct INSERT on community_node_pairing_codes'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_node_pairing_codes', 'UPDATE'),
  false,
  'authenticated role should NOT have direct UPDATE on community_node_pairing_codes'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_inference_jobs', 'INSERT'),
  false,
  'authenticated role should NOT have direct INSERT on community_inference_jobs'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_inference_jobs', 'UPDATE'),
  false,
  'authenticated role should NOT have direct UPDATE on community_inference_jobs'
);
SELECT is(
  has_table_privilege('authenticated', 'public.community_inference_jobs', 'DELETE'),
  false,
  'authenticated role should NOT have direct DELETE on community_inference_jobs'
);
SELECT is(
  has_column_privilege('authenticated', 'public.community_nodes', 'display_name', 'UPDATE'),
  true,
  'authenticated role CAN update community_nodes.display_name'
);
SELECT is(
  has_column_privilege('authenticated', 'public.community_nodes', 'status', 'UPDATE'),
  false,
  'authenticated role should NOT have column-level UPDATE on community_nodes.status'
);
SELECT is(
  has_column_privilege('authenticated', 'public.community_nodes', 'public_key', 'UPDATE'),
  false,
  'authenticated role should NOT have column-level UPDATE on community_nodes.public_key'
);

-- 5. Trigger defense guards
SELECT has_trigger('public', 'community_nodes', 'tr_community_nodes_protect_server_fields', 'tr_community_nodes_protect_server_fields trigger should exist');
SELECT has_trigger('public', 'community_inference_jobs', 'tr_community_jobs_terminal_guard', 'tr_community_jobs_terminal_guard trigger should exist');

SELECT * FROM finish();
ROLLBACK;
