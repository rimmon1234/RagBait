-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.2: pgTAP Database, Schema & Security Tests
-- Test: m4_2_indexing_test.sql
-- ==============================================================================

BEGIN;
SELECT plan(18);

-- ------------------------------------------------------------------------------
-- 1. Table & Column Existence
-- ------------------------------------------------------------------------------
SELECT has_table('public', 'kb_indexing_jobs', 'public.kb_indexing_jobs must exist');
SELECT has_table('public', 'kb_document_chunks', 'public.kb_document_chunks must exist');

SELECT has_column('public', 'kb_documents', 'indexing_status', 'public.kb_documents.indexing_status must exist');
SELECT has_column('public', 'kb_documents', 'active_chunk_version', 'public.kb_documents.active_chunk_version must exist');

-- ------------------------------------------------------------------------------
-- 2. Row Level Security Enabled
-- ------------------------------------------------------------------------------
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_indexing_jobs' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_indexing_jobs'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_document_chunks' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_document_chunks'
);

-- ------------------------------------------------------------------------------
-- 3. Anonymous Role Access Blocked
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('anon', 'public.kb_indexing_jobs', 'SELECT'),
  false,
  'anon must NOT have SELECT on kb_indexing_jobs'
);
SELECT is(
  has_table_privilege('anon', 'public.kb_document_chunks', 'SELECT'),
  false,
  'anon must NOT have SELECT on kb_document_chunks'
);
SELECT is(
  has_table_privilege('anon', 'public.kb_document_chunks', 'INSERT'),
  false,
  'anon must NOT have INSERT on kb_document_chunks'
);

-- ------------------------------------------------------------------------------
-- 4. Authenticated Direct Mutations Blocked
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('authenticated', 'public.kb_indexing_jobs', 'INSERT'),
  false,
  'authenticated must NOT have INSERT on kb_indexing_jobs'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_document_chunks', 'INSERT'),
  false,
  'authenticated must NOT have INSERT on kb_document_chunks'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_document_chunks', 'UPDATE'),
  false,
  'authenticated must NOT have UPDATE on kb_document_chunks'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_document_chunks', 'DELETE'),
  false,
  'authenticated must NOT have DELETE on kb_document_chunks'
);

-- ------------------------------------------------------------------------------
-- 5. Check Constraints and Business Rules Enforcement
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE m4_2_test_fixtures (
  subject_id uuid,
  topic_id uuid,
  document_id uuid
);

WITH ins_s AS (
  INSERT INTO public.kb_subjects (name, slug, description)
  VALUES ('M4_2 Indexing Subject', 'm4-2-indexing-subject', 'Testing M4.2')
  RETURNING id
),
ins_t AS (
  INSERT INTO public.kb_topics (subject_id, name, slug)
  SELECT ins_s.id, 'M4_2 Topic', 'm4-2-topic'
  FROM ins_s
  RETURNING id, subject_id
),
ins_d AS (
  INSERT INTO public.kb_documents (
    subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status
  )
  SELECT
    ins_t.subject_id, ins_t.id, 'Test Document', 'test.pdf',
    'documents/00000000-0000-0000-0000-000000000001/source.pdf',
    'application/pdf', 1024, 'uploaded', 'completed'
  FROM ins_t
  RETURNING id, subject_id, topic_id
)
INSERT INTO m4_2_test_fixtures (subject_id, topic_id, document_id)
SELECT subject_id, topic_id, id FROM ins_d;

-- 5.1 Token ceiling check constraint (token_count <= 420)
SELECT throws_ok(
  format(
    'INSERT INTO public.kb_document_chunks (document_id, chunk_index, page_number_start, page_number_end, raw_text, character_count, token_count, content_hash) VALUES (%L, 0, 1, 1, %L, %s, 421, %L)',
    (SELECT document_id FROM m4_2_test_fixtures),
    'This chunk has too many tokens.',
    length('This chunk has too many tokens.'),
    'dummyhash'
  ),
  '23514', -- check_violation
  NULL,
  'Chunk inserting > 420 tokens must violate chk_chunk_token_ceiling'
);

-- 5.2 Valid chunk insertion succeeds
SELECT lives_ok(
  format(
    'INSERT INTO public.kb_document_chunks (document_id, chunk_index, page_number_start, page_number_end, raw_text, character_count, token_count, content_hash) VALUES (%L, 0, 1, 1, %L, %s, 400, %L)',
    (SELECT document_id FROM m4_2_test_fixtures),
    'Valid chunk text under ceiling.',
    length('Valid chunk text under ceiling.'),
    'validhash'
  ),
  'Valid chunk inserting <= 420 tokens must succeed'
);

-- 5.3 Active indexing job uniqueness: at most one active job (queued/processing) per document
INSERT INTO public.kb_indexing_jobs (document_id, status)
VALUES ((SELECT document_id FROM m4_2_test_fixtures), 'queued');

SELECT throws_ok(
  format(
    'INSERT INTO public.kb_indexing_jobs (document_id, status) VALUES (%L, %L)',
    (SELECT document_id FROM m4_2_test_fixtures),
    'processing'
  ),
  '23505', -- unique_violation
  NULL,
  'Second active indexing job on same document must violate uq_kb_active_indexing_job'
);

-- 5.4 Completed/Failed job allows subsequent job
UPDATE public.kb_indexing_jobs
SET status = 'completed'
WHERE document_id = (SELECT document_id FROM m4_2_test_fixtures);

SELECT lives_ok(
  format(
    'INSERT INTO public.kb_indexing_jobs (document_id, status) VALUES (%L, %L)',
    (SELECT document_id FROM m4_2_test_fixtures),
    'queued'
  ),
  'New queued indexing job after completed job must succeed'
);

-- 5.5 Document indexing_status check constraint
SELECT throws_ok(
  format(
    'UPDATE public.kb_documents SET indexing_status = %L WHERE id = %L',
    'invalid_status',
    (SELECT document_id FROM m4_2_test_fixtures)
  ),
  '23514',
  NULL,
  'Invalid indexing_status on kb_documents must violate check constraint'
);

ROLLBACK;
