-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.3: pgTAP Database, Schema & Vector Tests
-- Test: m4_3_embedding_test.sql
-- ==============================================================================

BEGIN;
SELECT plan(18);

-- ------------------------------------------------------------------------------
-- 1. Extension Verification
-- ------------------------------------------------------------------------------
SELECT has_extension('vector', 'pgvector extension must be installed');

SELECT is(
  (SELECT extversion FROM pg_extension WHERE extname = 'vector'),
  '0.8.2',
  'pgvector extension version must be 0.8.2'
);

SELECT is(
  (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'),
  'extensions',
  'pgvector extension must be installed in extensions schema'
);

-- ------------------------------------------------------------------------------
-- 2. Table & Column Existence
-- ------------------------------------------------------------------------------
SELECT has_table('public', 'kb_embedding_jobs', 'public.kb_embedding_jobs must exist');
SELECT has_table('public', 'kb_chunk_embeddings', 'public.kb_chunk_embeddings must exist');

SELECT has_column('public', 'kb_documents', 'embedding_status', 'public.kb_documents.embedding_status must exist');
SELECT has_column('public', 'kb_documents', 'active_embedding_fingerprint', 'public.kb_documents.active_embedding_fingerprint must exist');

-- ------------------------------------------------------------------------------
-- 3. Row Level Security Enabled
-- ------------------------------------------------------------------------------
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_embedding_jobs' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_embedding_jobs'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_chunk_embeddings' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_chunk_embeddings'
);

-- ------------------------------------------------------------------------------
-- 4. Anonymous and Authenticated Role Access Blocked
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('anon', 'public.kb_embedding_jobs', 'SELECT'),
  false,
  'anon must NOT have SELECT on kb_embedding_jobs'
);
SELECT is(
  has_table_privilege('anon', 'public.kb_chunk_embeddings', 'SELECT'),
  false,
  'anon must NOT have SELECT on kb_chunk_embeddings'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_chunk_embeddings', 'SELECT'),
  false,
  'authenticated students must NOT have direct SELECT on raw kb_chunk_embeddings'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_chunk_embeddings', 'INSERT'),
  false,
  'authenticated must NOT have INSERT on kb_chunk_embeddings'
);

-- ------------------------------------------------------------------------------
-- 5. Constraints and Business Rules Enforcement
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE m4_3_test_fixtures (
  subject_id uuid,
  topic_id uuid,
  document_id uuid,
  chunk_id uuid
);

WITH ins_s AS (
  INSERT INTO public.kb_subjects (name, slug, description)
  VALUES ('M4_3 Embedding Subject', 'm4-3-embedding-subject', 'Testing M4.3')
  RETURNING id
),
ins_t AS (
  INSERT INTO public.kb_topics (subject_id, name, slug)
  SELECT ins_s.id, 'M4_3 Topic', 'm4-3-topic'
  FROM ins_s
  RETURNING id, subject_id
),
ins_d AS (
  INSERT INTO public.kb_documents (
    subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status, indexing_status
  )
  SELECT
    ins_t.subject_id, ins_t.id, 'Test Document', 'test.pdf',
    'documents/00000000-0000-0000-0000-000000000002/source.pdf',
    'application/pdf', 1024, 'uploaded', 'completed', 'indexed'
  FROM ins_t
  RETURNING id, subject_id, topic_id
),
ins_c AS (
  INSERT INTO public.kb_document_chunks (
    document_id, chunk_index, page_number_start, page_number_end,
    raw_text, character_count, token_count, content_hash, is_active
  )
  SELECT
    ins_d.id, 0, 1, 1,
    'Test chunk raw text for vector embedding test.',
    length('Test chunk raw text for vector embedding test.'),
    10,
    'mock_content_hash_m4_3',
    true
  FROM ins_d
  RETURNING id, document_id
)
INSERT INTO m4_3_test_fixtures (subject_id, topic_id, document_id, chunk_id)
SELECT d.subject_id, d.topic_id, d.id, c.id
FROM ins_d d JOIN ins_c c ON c.document_id = d.id;

-- 5.1 Insert valid 384-dim vector succeeds
SELECT lives_ok(
  format(
    'INSERT INTO public.kb_chunk_embeddings (chunk_id, document_id, model_id, model_fingerprint, dimensions, embedding, source_content_hash) ' ||
    'VALUES (%L, %L, %L, %L, 384, %L::extensions.vector, %L)',
    (SELECT chunk_id FROM m4_3_test_fixtures),
    (SELECT document_id FROM m4_3_test_fixtures),
    'intfloat/multilingual-e5-small',
    'e5_small_int8_761b726d_f80102d3',
    '[' || array_to_string(array_fill(0.051031, ARRAY[384]), ',') || ']',
    'mock_content_hash_m4_3'
  ),
  'Valid 384-dimensional vector insertion must succeed'
);

-- 5.2 Duplicate embedding on same chunk and model fingerprint fails
SELECT throws_ok(
  format(
    'INSERT INTO public.kb_chunk_embeddings (chunk_id, document_id, model_id, model_fingerprint, dimensions, embedding, source_content_hash) ' ||
    'VALUES (%L, %L, %L, %L, 384, %L::extensions.vector, %L)',
    (SELECT chunk_id FROM m4_3_test_fixtures),
    (SELECT document_id FROM m4_3_test_fixtures),
    'intfloat/multilingual-e5-small',
    'e5_small_int8_761b726d_f80102d3',
    '[' || array_to_string(array_fill(0.051031, ARRAY[384]), ',') || ']',
    'mock_content_hash_m4_3'
  ),
  '23505', -- unique_violation
  NULL,
  'Duplicate embedding for same chunk and model_fingerprint must violate uq_chunk_model_embedding'
);

-- 5.3 Dimensions check constraint
SELECT throws_ok(
  format(
    'INSERT INTO public.kb_chunk_embeddings (chunk_id, document_id, model_id, model_fingerprint, dimensions, embedding, source_content_hash) ' ||
    'VALUES (%L, %L, %L, %L, 512, %L::extensions.vector, %L)',
    (SELECT chunk_id FROM m4_3_test_fixtures),
    (SELECT document_id FROM m4_3_test_fixtures),
    'intfloat/multilingual-e5-small',
    'other_fingerprint',
    '[' || array_to_string(array_fill(0.051031, ARRAY[384]), ',') || ']',
    'mock_content_hash_m4_3'
  ),
  '23514', -- check_violation
  NULL,
  'Embedding row with dimensions != 384 must violate ck_embedding_dimensions'
);

-- 5.4 Active embedding job uniqueness
INSERT INTO public.kb_embedding_jobs (document_id, model_fingerprint, status)
VALUES ((SELECT document_id FROM m4_3_test_fixtures), 'e5_small_int8_761b726d_f80102d3', 'queued');

SELECT throws_ok(
  format(
    'INSERT INTO public.kb_embedding_jobs (document_id, model_fingerprint, status) VALUES (%L, %L, %L)',
    (SELECT document_id FROM m4_3_test_fixtures),
    'e5_small_int8_761b726d_f80102d3',
    'processing'
  ),
  '23505', -- unique_violation
  NULL,
  'Second active embedding job on same document and model must violate uq_kb_active_embedding_job'
);

-- 5.5 Document embedding_status check constraint
SELECT throws_ok(
  format(
    'UPDATE public.kb_documents SET embedding_status = %L WHERE id = %L',
    'invalid_embedding_status',
    (SELECT document_id FROM m4_3_test_fixtures)
  ),
  '23514',
  NULL,
  'Invalid embedding_status on kb_documents must violate check constraint'
);

ROLLBACK;
