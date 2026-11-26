-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.4: pgTAP Hybrid Retrieval & Subject Scoping Tests
-- Test: m4_4_retrieval_test.sql
-- ==============================================================================

BEGIN;
SELECT plan(19);

-- ------------------------------------------------------------------------------
-- 1. Multilingual Index Verification
-- ------------------------------------------------------------------------------
SELECT has_index(
  'public',
  'kb_document_chunks',
  'idx_kb_chunks_fts_simple',
  'Multilingual GIN index idx_kb_chunks_fts_simple must exist'
);

-- ------------------------------------------------------------------------------
-- 2. Function Existence and Security Configuration
-- ------------------------------------------------------------------------------
SELECT has_function(
  'public',
  'match_subject_chunks',
  ARRAY['uuid', 'extensions.vector', 'text', 'text', 'integer', 'integer', 'integer', 'double precision', 'double precision'],
  'match_subject_chunks() function must exist with correct parameter signature'
);

SELECT is(
  (
    SELECT prosecdef 
    FROM pg_proc 
    WHERE proname = 'match_subject_chunks' 
      AND pronamespace = 'public'::regnamespace
  ),
  true,
  'match_subject_chunks() must be SECURITY DEFINER'
);

SELECT is(
  (
    SELECT proconfig 
    FROM pg_proc 
    WHERE proname = 'match_subject_chunks' 
      AND pronamespace = 'public'::regnamespace
  ),
  ARRAY['search_path=public, extensions'],
  'match_subject_chunks() must have fixed search_path = public, extensions'
);

-- ------------------------------------------------------------------------------
-- 3. Privilege Revocation & Grant Verification
-- ------------------------------------------------------------------------------
SELECT is(
  has_function_privilege(
    'anon',
    'public.match_subject_chunks(uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision)',
    'EXECUTE'
  ),
  false,
  'anon must NOT have EXECUTE on match_subject_chunks()'
);

SELECT is(
  has_function_privilege(
    'authenticated',
    'public.match_subject_chunks(uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision)',
    'EXECUTE'
  ),
  false,
  'authenticated students must NOT have direct EXECUTE on match_subject_chunks()'
);

SELECT is(
  has_function_privilege(
    'service_role',
    'public.match_subject_chunks(uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision)',
    'EXECUTE'
  ),
  true,
  'service_role trusted backend MUST have EXECUTE on match_subject_chunks()'
);

SELECT is(
  has_function_privilege(
    'postgres',
    'public.match_subject_chunks(uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision)',
    'EXECUTE'
  ),
  true,
  'postgres administrator MUST have EXECUTE on match_subject_chunks()'
);

-- ------------------------------------------------------------------------------
-- 4. Functional Test Setup: Multi-Subject Fixtures
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE m4_4_fixtures (
  subject_1_id uuid,
  subject_2_id uuid,
  subject_inactive_id uuid,
  topic_active_id uuid,
  topic_inactive_id uuid,
  doc_active_id uuid,
  doc_archived_id uuid,
  doc_other_subject_id uuid,
  chunk_en_id uuid,
  chunk_bn_id uuid,
  chunk_hi_id uuid
);

WITH ins_s1 AS (
  INSERT INTO public.kb_subjects (name, slug, description, is_active)
  VALUES ('M4_4 Subject Alpha', 'm4-4-sub-alpha', 'Subject Alpha for M4.4', true)
  RETURNING id
),
ins_s2 AS (
  INSERT INTO public.kb_subjects (name, slug, description, is_active)
  VALUES ('M4_4 Subject Beta', 'm4-4-sub-beta', 'Subject Beta for M4.4', true)
  RETURNING id
),
ins_s3 AS (
  INSERT INTO public.kb_subjects (name, slug, description, is_active)
  VALUES ('M4_4 Inactive Subject', 'm4-4-sub-inactive', 'Inactive Subject', false)
  RETURNING id
),
ins_t_active AS (
  INSERT INTO public.kb_topics (subject_id, name, slug, is_active)
  SELECT ins_s1.id, 'Active Topic Alpha', 'active-topic-alpha', true
  FROM ins_s1
  RETURNING id, subject_id
),
ins_t_inactive AS (
  INSERT INTO public.kb_topics (subject_id, name, slug, is_active)
  SELECT ins_s1.id, 'Inactive Topic Alpha', 'inactive-topic-alpha', false
  FROM ins_s1
  RETURNING id, subject_id
),
ins_t_sub2 AS (
  INSERT INTO public.kb_topics (subject_id, name, slug, is_active)
  SELECT ins_s2.id, 'Topic Beta', 'topic-beta', true
  FROM ins_s2
  RETURNING id, subject_id
),
ins_d_active AS (
  INSERT INTO public.kb_documents (
    subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint
  )
  SELECT
    ins_t_active.subject_id, ins_t_active.id, 'OS Architecture Notes', 'os.pdf',
    'documents/00000000-0000-0000-0000-000000000011/source.pdf', 'application/pdf', 1024,
    'uploaded'::public.kb_upload_status, 'completed'::public.kb_extraction_status,
    'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3'
  FROM ins_t_active
  RETURNING id
),
ins_d_archived AS (
  INSERT INTO public.kb_documents (
    subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint,
    is_archived
  )
  SELECT
    ins_t_active.subject_id, ins_t_active.id, 'Archived OS Notes', 'archived_os.pdf',
    'documents/00000000-0000-0000-0000-000000000012/source.pdf', 'application/pdf', 1024,
    'uploaded'::public.kb_upload_status, 'completed'::public.kb_extraction_status,
    'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3',
    true
  FROM ins_t_active
  RETURNING id
),
ins_d_other_sub AS (
  INSERT INTO public.kb_documents (
    subject_id, topic_id, title, original_filename,
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint
  )
  SELECT
    ins_t_sub2.subject_id, ins_t_sub2.id, 'Biology Cellular Respiration', 'bio.pdf',
    'documents/00000000-0000-0000-0000-000000000013/source.pdf', 'application/pdf', 1024,
    'uploaded'::public.kb_upload_status, 'completed'::public.kb_extraction_status,
    'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3'
  FROM ins_t_sub2
  RETURNING id
),
ins_chunks AS (
  INSERT INTO public.kb_document_chunks (
    document_id, chunking_version, chunk_index, page_number_start, page_number_end,
    raw_text, character_count, token_count, page_spans, content_hash, is_active
  )
  SELECT
    ins_d_active.id, 1, 0, 1, 1,
    'Operating systems coordinate CPU scheduling, memory management, and concurrent processes.',
    length('Operating systems coordinate CPU scheduling, memory management, and concurrent processes.'),
    14, '[{"page_number": 1, "start_char": 0, "end_char": 89}]'::jsonb,
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', true
  FROM ins_d_active
  UNION ALL
  SELECT
    ins_d_active.id, 1, 1, 2, 2,
    'কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে আলোচনা করে।',
    length('কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে আলোচনা করে।'),
    12, '[{"page_number": 2, "start_char": 0, "end_char": 66}]'::jsonb,
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b856', true
  FROM ins_d_active
  UNION ALL
  SELECT
    ins_d_active.id, 1, 2, 3, 3,
    'कंप्यूटर विज्ञान सूचना और संगणना के सैद्धांतिक आधारों का अध्ययन है।',
    length('कंप्यूटर विज्ञान सूचना और संगणना के सैद्धांतिक आधारों का अध्ययन है।'),
    12, '[{"page_number": 3, "start_char": 0, "end_char": 67}]'::jsonb,
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b857', true
  FROM ins_d_active
  RETURNING id, chunk_index
)
INSERT INTO m4_4_fixtures (
  subject_1_id, subject_2_id, subject_inactive_id,
  topic_active_id, topic_inactive_id,
  doc_active_id, doc_archived_id, doc_other_subject_id,
  chunk_en_id, chunk_bn_id, chunk_hi_id
)
SELECT
  (SELECT id FROM ins_s1),
  (SELECT id FROM ins_s2),
  (SELECT id FROM ins_s3),
  (SELECT id FROM ins_t_active),
  (SELECT id FROM ins_t_inactive),
  (SELECT id FROM ins_d_active),
  (SELECT id FROM ins_d_archived),
  (SELECT id FROM ins_d_other_sub),
  (SELECT id FROM ins_chunks WHERE chunk_index = 0),
  (SELECT id FROM ins_chunks WHERE chunk_index = 1),
  (SELECT id FROM ins_chunks WHERE chunk_index = 2);

-- Insert embeddings for active chunks
INSERT INTO public.kb_chunk_embeddings (
  chunk_id, document_id, chunk_version, model_id, model_fingerprint,
  dimensions, embedding, source_content_hash
)
SELECT
  f.chunk_en_id, f.doc_active_id, 1, 'intfloat/multilingual-e5-small', 'e5_small_int8_761b726d_f80102d3',
  384, ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
FROM m4_4_fixtures f;

INSERT INTO public.kb_chunk_embeddings (
  chunk_id, document_id, chunk_version, model_id, model_fingerprint,
  dimensions, embedding, source_content_hash
)
SELECT
  f.chunk_bn_id, f.doc_active_id, 1, 'intfloat/multilingual-e5-small', 'e5_small_int8_761b726d_f80102d3',
  384, ('[' || array_to_string(array_fill(0.050, ARRAY[384]), ',') || ']')::text::extensions.vector,
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b856'
FROM m4_4_fixtures f;

INSERT INTO public.kb_chunk_embeddings (
  chunk_id, document_id, chunk_version, model_id, model_fingerprint,
  dimensions, embedding, source_content_hash
)
SELECT
  f.chunk_hi_id, f.doc_active_id, 1, 'intfloat/multilingual-e5-small', 'e5_small_int8_761b726d_f80102d3',
  384, ('[' || array_to_string(array_fill(0.049, ARRAY[384]), ',') || ']')::text::extensions.vector,
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b857'
FROM m4_4_fixtures f;

-- ------------------------------------------------------------------------------
-- 5. Functional Retrieval Tests
-- ------------------------------------------------------------------------------

-- Test 9: Active Subject retrieval returns results
SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems CPU scheduling',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
  ),
  3,
  'Retrieval on active subject must return all 3 eligible chunks'
);

-- Test 10: Cross-Subject Isolation (Subject 2 has no chunks in doc)
SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_2_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems CPU scheduling',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
  ),
  0,
  'Subject 2 must return 0 results (strict cross-subject isolation)'
);

-- Test 11: Inactive Subject returns zero results
SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_inactive_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
  ),
  0,
  'Inactive subject must immediately return 0 results'
);

-- Test 12: Model Fingerprint mismatch returns zero results
SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems',
      'wrong_fingerprint_xyz',
      5
    )
  ),
  0,
  'Model fingerprint mismatch must fence chunk embeddings'
);

-- Test 13: Top English result has both vector and fts ranks
SELECT is(
  (
    SELECT (vector_rank IS NOT NULL AND fts_rank IS NOT NULL)
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'CPU scheduling',
      'e5_small_int8_761b726d_f80102d3',
      1
    )
    WHERE chunk_id = (SELECT chunk_en_id FROM m4_4_fixtures)
  ),
  true,
  'English chunk matching both vector and keyword search must have both ranks populated'
);

-- Test 14: Bengali Unicode keyword matching works via simple FTS
SELECT is(
  (
    SELECT (fts_rank IS NOT NULL)
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.050, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'কম্পিউটার বিজ্ঞান',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
    WHERE chunk_id = (SELECT chunk_bn_id FROM m4_4_fixtures)
  ),
  true,
  'Bengali chunk must match multilingual FTS and populate fts_rank'
);

-- Test 15: Hindi Unicode keyword matching works via simple FTS
SELECT is(
  (
    SELECT (fts_rank IS NOT NULL)
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.049, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'कंप्यूटर विज्ञान',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
    WHERE chunk_id = (SELECT chunk_hi_id FROM m4_4_fixtures)
  ),
  true,
  'Hindi chunk must match multilingual FTS and populate fts_rank'
);

-- Test 16: Result count bound respected (match_count = 2)
SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems',
      'e5_small_int8_761b726d_f80102d3',
      2
    )
  ),
  2,
  'match_count limit of 2 must bound output to exactly 2 rows'
);

-- Test 17: Final rank ordering is strictly 1-based and contiguous
SELECT is(
  (
    SELECT array_agg(final_rank ORDER BY final_rank)
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems',
      'e5_small_int8_761b726d_f80102d3',
      3
    )
  ),
  ARRAY[1, 2, 3],
  'Final ranks must be contiguous integers [1, 2, 3]'
);

-- Test 18: Physical page provenance is preserved
SELECT is(
  (
    SELECT page_number_start = 1 AND page_number_end = 1
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'CPU scheduling',
      'e5_small_int8_761b726d_f80102d3',
      1
    )
    WHERE chunk_id = (SELECT chunk_en_id FROM m4_4_fixtures)
  ),
  true,
  'Retrieved chunk must retain exact physical page provenance'
);

-- Test 19: Archived document chunks are strictly excluded
UPDATE public.kb_documents
SET is_archived = true
WHERE id = (SELECT doc_active_id FROM m4_4_fixtures);

SELECT is(
  (
    SELECT count(*)::integer
    FROM public.match_subject_chunks(
      (SELECT subject_1_id FROM m4_4_fixtures),
      ('[' || array_to_string(array_fill(0.051, ARRAY[384]), ',') || ']')::text::extensions.vector,
      'Operating systems',
      'e5_small_int8_761b726d_f80102d3',
      5
    )
  ),
  0,
  'Archiving active document must instantly exclude all its chunks from retrieval'
);

ROLLBACK;
