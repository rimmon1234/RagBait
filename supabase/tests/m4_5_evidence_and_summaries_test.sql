-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.5: pgTAP Evidence Snapshots & Summaries Tests
-- Test: m4_5_evidence_and_summaries_test.sql
-- ==============================================================================

BEGIN;
SELECT plan(31);

-- ------------------------------------------------------------------------------
-- 1. Schema, Index, and RLS Existence
-- ------------------------------------------------------------------------------
SELECT has_table('public', 'chat_message_sources', 'public.chat_message_sources must exist');
SELECT has_table('public', 'chat_conversation_summaries', 'public.chat_conversation_summaries must exist');

SELECT has_index(
  'public',
  'chat_messages',
  'uq_chat_messages_conv_role_idem',
  'Unique idempotency index uq_chat_messages_conv_role_idem must exist on chat_messages'
);

SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'chat_message_sources' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on chat_message_sources'
);

SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'chat_conversation_summaries' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on chat_conversation_summaries'
);

-- ------------------------------------------------------------------------------
-- 2. Privilege Boundaries (anon blocked; authenticated read-only via RLS)
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('anon', 'public.chat_message_sources', 'SELECT'),
  false,
  'anon must NOT have SELECT on chat_message_sources'
);
SELECT is(
  has_table_privilege('anon', 'public.chat_conversation_summaries', 'SELECT'),
  false,
  'anon must NOT have SELECT on chat_conversation_summaries'
);
SELECT is(
  has_table_privilege('authenticated', 'public.chat_message_sources', 'INSERT'),
  false,
  'authenticated students must NOT have direct INSERT on chat_message_sources'
);
SELECT is(
  has_table_privilege('authenticated', 'public.chat_message_sources', 'UPDATE'),
  false,
  'authenticated students must NOT have direct UPDATE on chat_message_sources'
);
SELECT is(
  has_table_privilege('authenticated', 'public.chat_message_sources', 'DELETE'),
  false,
  'authenticated students must NOT have direct DELETE on chat_message_sources'
);
SELECT is(
  has_table_privilege('authenticated', 'public.chat_conversation_summaries', 'INSERT'),
  false,
  'authenticated students must NOT have direct INSERT on chat_conversation_summaries'
);
SELECT is(
  has_table_privilege('authenticated', 'public.chat_conversation_summaries', 'UPDATE'),
  false,
  'authenticated students must NOT have direct UPDATE WATERMARK on chat_conversation_summaries'
);

-- ------------------------------------------------------------------------------
-- 3. Fixtures Setup (postgres / service role)
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE m4_5_vars (
  s1_id uuid,
  s2_id uuid,
  subj_id uuid,
  topic_id uuid,
  doc_id uuid,
  chunk_id uuid,
  conv_id uuid,
  msg_user_id uuid,
  msg_asst_id uuid,
  source_id uuid
);
GRANT SELECT ON m4_5_vars TO authenticated;

DO $$
DECLARE
  v_s1 uuid := gen_random_uuid();
  v_s2 uuid := gen_random_uuid();
  v_subj uuid := gen_random_uuid();
  v_topic uuid := gen_random_uuid();
  v_doc uuid := gen_random_uuid();
  v_chunk uuid := gen_random_uuid();
  v_conv uuid := gen_random_uuid();
  v_mu uuid := gen_random_uuid();
  v_ma uuid := gen_random_uuid();
  v_src uuid := gen_random_uuid();
BEGIN
  INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
  VALUES
    (v_s1, '00000000-0000-0000-0000-000000000000', 'm45_s1_' || left(v_s1::text, 6) || '@test.edu', 'authenticated', 'authenticated', '{}'::jsonb),
    (v_s2, '00000000-0000-0000-0000-000000000000', 'm45_s2_' || left(v_s2::text, 6) || '@test.edu', 'authenticated', 'authenticated', '{}'::jsonb);

  UPDATE public.profiles SET approval_status = 'approved' WHERE id IN (v_s1, v_s2);

  INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
  VALUES (v_subj, 'M45 OS ' || left(v_subj::text, 6), 'm45-os-' || left(v_subj::text, 6), 'OS', true);

  INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
  VALUES (v_topic, v_subj, 'Paging Topic ' || left(v_topic::text, 6), 'paging-' || left(v_topic::text, 6), true);

  INSERT INTO public.kb_documents (
    id, subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    indexing_status, active_chunk_version
  )
  VALUES (
    v_doc, v_subj, v_topic, 'Silberschatz OS 10th Ed', 'os10.pdf',
    'documents/' || v_doc::text || '/source.pdf', 'application/pdf', 4096,
    'uploaded', 'completed', 'indexed', 1
  );

  INSERT INTO public.kb_document_chunks (
    id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
    raw_text, character_count, token_count, page_spans, content_hash, is_active
  )
  VALUES (
    v_chunk, v_doc, 1, 0, 42, 43,
    'Paging avoids external fragmentation by mapping virtual pages to physical frames.',
    length('Paging avoids external fragmentation by mapping virtual pages to physical frames.'),
    15, '[{"page_number": 42, "start_char": 0, "end_char": 80}]'::jsonb,
    'sha256_paging_hash_01', true
  );

  INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status, message_count, next_sequence_number)
  VALUES (v_conv, v_s1, v_subj, 'Paging vs Segmentation', 'active', 2, 3);

  INSERT INTO public.chat_messages (id, conversation_id, sequence_number, role, content, status, idempotency_key)
  VALUES
    (v_mu, v_conv, 1, 'user', 'What is paging?', 'completed', 'idem-user-1'),
    (v_ma, v_conv, 2, 'assistant', 'Paging eliminates external fragmentation [S1].', 'completed', 'idem-user-1');

  INSERT INTO public.chat_message_sources (
    id, message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
    document_title_snapshot, page_number_start, page_number_end,
    excerpt, content_hash, chunk_version, retrieval_rank, relevance_score,
    included_in_context, referenced_in_answer
  )
  VALUES (
    v_src, v_ma, 'S1', v_chunk, v_doc, v_chunk, v_doc,
    'Silberschatz OS 10th Ed', 42, 43,
    'Paging avoids external fragmentation by mapping virtual pages to physical frames.',
    'sha256_paging_hash_01', 1, 1, 0.0163, true, false
  );

  INSERT INTO public.chat_conversation_summaries (
    conversation_id, summary_text, compacted_through_sequence, summary_version, token_count, compaction_status
  )
  VALUES (
    v_conv, 'Student discussed virtual memory concepts.', 0, 1, 8, 'completed'
  );

  INSERT INTO m4_5_vars VALUES (v_s1, v_s2, v_subj, v_topic, v_doc, v_chunk, v_conv, v_mu, v_ma, v_src);
END $$;

-- ------------------------------------------------------------------------------
-- 4. Constraint Integrity Tests (as superuser)
-- ------------------------------------------------------------------------------
-- Duplicate (message_id, citation_key) rejected
PREPARE dup_citation_key AS
  INSERT INTO public.chat_message_sources (
    message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
    document_title_snapshot, page_number_start, page_number_end,
    excerpt, content_hash, chunk_version, retrieval_rank, relevance_score
  )
  SELECT msg_asst_id, 'S1', chunk_id, doc_id, chunk_id, doc_id,
    'Dup', 1, 1, 'Text', 'hash', 1, 1, 0.5 FROM m4_5_vars;
SELECT throws_ok('dup_citation_key', '23505', NULL, 'Duplicate citation_key on the same message must fail');

-- Invalid citation_key format ([S0], BAD1) rejected
PREPARE bad_citation_key AS
  INSERT INTO public.chat_message_sources (
    message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
    document_title_snapshot, page_number_start, page_number_end,
    excerpt, content_hash, chunk_version, retrieval_rank, relevance_score
  )
  SELECT msg_asst_id, 'S0', chunk_id, doc_id, chunk_id, doc_id,
    'Bad', 1, 1, 'Text', 'hash', 1, 1, 0.5 FROM m4_5_vars;
SELECT throws_ok('bad_citation_key', '23514', NULL, 'Zero citation_key S0 must be rejected by check constraint');

-- Invalid page range (start > end) rejected
PREPARE bad_page_range AS
  INSERT INTO public.chat_message_sources (
    message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
    document_title_snapshot, page_number_start, page_number_end,
    excerpt, content_hash, chunk_version, retrieval_rank, relevance_score
  )
  SELECT msg_asst_id, 'S2', chunk_id, doc_id, chunk_id, doc_id,
    'BadPages', 10, 5, 'Text', 'hash', 1, 1, 0.5 FROM m4_5_vars;
SELECT throws_ok('bad_page_range', '23514', NULL, 'Invalid page range (start > end) must be rejected');

-- Duplicate user idempotency_key in same conversation rejected
PREPARE dup_user_idem AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status, idempotency_key)
  SELECT conv_id, 99, 'user', 'Duplicate prompt', 'completed', 'idem-user-1' FROM m4_5_vars;
SELECT throws_ok('dup_user_idem', '23505', NULL, 'Duplicate user idempotency_key in same conversation must be rejected');

-- Snapshot immutability trigger rejects modifying excerpt or content_hash
PREPARE tamper_excerpt AS
  UPDATE public.chat_message_sources
  SET excerpt = 'Tampered forged content'
  WHERE id = (SELECT source_id FROM m4_5_vars);
SELECT throws_ok('tamper_excerpt', '22000', NULL, 'Immutable excerpt update must be rejected by trigger');

PREPARE tamper_title AS
  UPDATE public.chat_message_sources
  SET document_title_snapshot = 'Forged Book'
  WHERE id = (SELECT source_id FROM m4_5_vars);
SELECT throws_ok('tamper_title', '22000', NULL, 'Immutable document_title_snapshot update must be rejected');

-- Allowed state progression: referenced_in_answer = true MUST succeed
PREPARE mark_referenced AS
  UPDATE public.chat_message_sources
  SET referenced_in_answer = true
  WHERE id = (SELECT source_id FROM m4_5_vars);
SELECT lives_ok('mark_referenced', 'Updating referenced_in_answer flag on existing snapshot must succeed');

-- Invalid summary status & negative watermark rejected
PREPARE bad_summary_status AS
  UPDATE public.chat_conversation_summaries
  SET compaction_status = 'invalid_state'
  WHERE conversation_id = (SELECT conv_id FROM m4_5_vars);
SELECT throws_ok('bad_summary_status', '23514', NULL, 'Invalid compaction_status must be rejected');

PREPARE neg_summary_watermark AS
  UPDATE public.chat_conversation_summaries
  SET compacted_through_sequence = -1
  WHERE conversation_id = (SELECT conv_id FROM m4_5_vars);
SELECT throws_ok('neg_summary_watermark', '23514', NULL, 'Negative compaction sequence must be rejected');

-- ------------------------------------------------------------------------------
-- 5. RLS Ownership & Moderation Visibility Tests
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT s1_id::text FROM m4_5_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;
SET LOCAL ROLE authenticated;

-- Student 1 sees own source when document, topic, and subject are active
SELECT results_eq(
  'SELECT citation_key FROM public.chat_message_sources WHERE message_id = (SELECT msg_asst_id FROM m4_5_vars)',
  $$VALUES ('S1')$$,
  'Owner Student 1 sees active source snapshot through RLS'
);

-- Student 1 sees own summary
SELECT results_eq(
  'SELECT summary_version FROM public.chat_conversation_summaries WHERE conversation_id = (SELECT conv_id FROM m4_5_vars)',
  $$VALUES (1)$$,
  'Owner Student 1 sees own conversation summary through RLS'
);

-- Student 1 CANNOT directly mutate source snapshot or summary watermark (42501 permission denied)
PREPARE s1_forged_source_insert AS
  INSERT INTO public.chat_message_sources (
    message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id,
    document_title_snapshot, page_number_start, page_number_end,
    excerpt, content_hash, chunk_version, retrieval_rank, relevance_score
  )
  SELECT msg_asst_id, 'S9', chunk_id, doc_id, chunk_id, doc_id,
    'Forged', 1, 1, 'Text', 'hash', 1, 1, 0.5 FROM m4_5_vars;
SELECT throws_ok('s1_forged_source_insert', '42501', NULL, 'Student 1 cannot directly insert source snapshots');

PREPARE s1_advance_watermark AS
  UPDATE public.chat_conversation_summaries SET compacted_through_sequence = 999;
SELECT throws_ok('s1_advance_watermark', '42501', NULL, 'Student 1 cannot directly advance summary compaction watermark');

-- Switch to Student 2 (non-owner)
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT s2_id::text FROM m4_5_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;

SELECT is_empty(
  'SELECT * FROM public.chat_message_sources WHERE message_id = (SELECT msg_asst_id FROM m4_5_vars)',
  'Student 2 MUST NOT see Student 1 source snapshots'
);
SELECT is_empty(
  'SELECT * FROM public.chat_conversation_summaries WHERE conversation_id = (SELECT conv_id FROM m4_5_vars)',
  'Student 2 MUST NOT see Student 1 summaries'
);

-- Switch back to superuser and test archival / deletion fencing on RLS & snapshot durability
RESET ROLE;
UPDATE public.kb_documents SET is_archived = true WHERE id = (SELECT doc_id FROM m4_5_vars);

DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT s1_id::text FROM m4_5_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;
SET LOCAL ROLE authenticated;

SELECT is_empty(
  'SELECT * FROM public.chat_message_sources WHERE message_id = (SELECT msg_asst_id FROM m4_5_vars)',
  'Archiving source document MUST immediately hide raw snapshot row from authenticated PostgREST SELECT'
);

-- Switch to superuser: hard delete document -> snapshot row survives with document_id = NULL and immutable source_document_id!
RESET ROLE;
UPDATE public.kb_documents SET is_archived = false WHERE id = (SELECT doc_id FROM m4_5_vars);
DELETE FROM public.kb_documents WHERE id = (SELECT doc_id FROM m4_5_vars);

SELECT results_eq(
  'SELECT (document_id IS NULL AND source_document_id = (SELECT doc_id FROM m4_5_vars)) FROM public.chat_message_sources WHERE id = (SELECT source_id FROM m4_5_vars)',
  $$VALUES (true)$$,
  'Deleting document preserves snapshot row cleanly with document_id NULL and immutable source_document_id intact'
);

-- Deleting conversation cascades cleanly to sources and summaries
DELETE FROM public.chat_conversations WHERE id = (SELECT conv_id FROM m4_5_vars);

SELECT is_empty(
  'SELECT * FROM public.chat_message_sources WHERE id = (SELECT source_id FROM m4_5_vars)',
  'Deleting conversation cascades to chat_message_sources'
);
SELECT is_empty(
  'SELECT * FROM public.chat_conversation_summaries WHERE conversation_id = (SELECT conv_id FROM m4_5_vars)',
  'Deleting conversation cascades to chat_conversation_summaries'
);

SELECT * FROM finish();
ROLLBACK;
