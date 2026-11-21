-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.5 Admin Moderation & Taxonomy Security Tests
-- ==============================================================================

BEGIN;

SELECT plan(12);

-- 1. Schema Tests for Bounded Retries
SELECT has_column('public', 'kb_extraction_jobs', 'retry_count', 'kb_extraction_jobs has retry_count column');
SELECT has_column('public', 'kb_extraction_jobs', 'retry_of_job_id', 'kb_extraction_jobs has retry_of_job_id column');
SELECT col_is_pk('public', 'kb_extraction_jobs', 'id', 'kb_extraction_jobs id is primary key');

-- 2. Setup Test Data
INSERT INTO auth.users (id, email)
VALUES 
  ('11111111-1111-1111-1111-111111111111'::uuid, 'm35_student@univ.edu'),
  ('22222222-2222-2222-2222-222222222222'::uuid, 'm35_admin@univ.edu')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.profiles (id, email, display_name, role, approval_status)
VALUES 
  ('11111111-1111-1111-1111-111111111111'::uuid, 'm35_student@univ.edu', 'Student', 'user', 'approved'),
  ('22222222-2222-2222-2222-222222222222'::uuid, 'm35_admin@univ.edu', 'Admin', 'admin', 'approved')
ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, approval_status = EXCLUDED.approval_status;

-- Create subject and topic
INSERT INTO public.kb_subjects (id, name, slug, icon_name, is_active)
VALUES ('33333333-3333-3333-3333-333333333333'::uuid, 'Security Testing', 'sec-test', 'BookOpen', true)
ON CONFLICT (id) DO UPDATE SET is_active = true;

INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
VALUES ('44444444-4444-4444-4444-444444444444'::uuid, '33333333-3333-3333-3333-333333333333'::uuid, 'Taxonomy Topic', 'tax-topic', true)
ON CONFLICT (id) DO UPDATE SET is_active = true;

INSERT INTO public.kb_documents (id, subject_id, topic_id, uploader_id, title, original_filename, storage_path, mime_type, file_size_bytes, upload_status, is_archived)
VALUES ('55555555-5555-5555-5555-555555555555'::uuid, '33333333-3333-3333-3333-333333333333'::uuid, '44444444-4444-4444-4444-444444444444'::uuid, '11111111-1111-1111-1111-111111111111'::uuid, 'Test Doc', 'test.pdf', 'documents/55555555-5555-5555-5555-555555555555/source.pdf', 'application/pdf', 1024, 'uploaded', false)
ON CONFLICT (id) DO UPDATE SET upload_status = 'uploaded', is_archived = false;

INSERT INTO public.kb_document_pages (id, document_id, page_number, raw_text, character_count, word_count, has_text)
VALUES ('66666666-6666-6666-6666-666666666666'::uuid, '55555555-5555-5555-5555-555555555555'::uuid, 1, 'Hello World', 11, 2, true)
ON CONFLICT (id) DO NOTHING;

-- 3. Verify Active Taxonomy Visibility for Student
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claims" = '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}';

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_documents WHERE id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[1],
  'Approved student can view document when subject and topic are active'
);

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_document_pages WHERE document_id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[1],
  'Approved student can view pages when subject and topic are active'
);

-- 4. Inactivate Topic and Verify Student is Blocked under RLS
RESET ROLE;
UPDATE public.kb_topics SET is_active = false WHERE id = '44444444-4444-4444-4444-444444444444'::uuid;

SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claims" = '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}';

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_documents WHERE id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[0],
  'Student cannot view document under inactive topic'
);

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_document_pages WHERE document_id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[0],
  'Student cannot view pages under inactive topic'
);

-- 5. Inactivate Subject, Reactivate Topic, and Verify Student is Still Blocked
RESET ROLE;
UPDATE public.kb_subjects SET is_active = false WHERE id = '33333333-3333-3333-3333-333333333333'::uuid;
UPDATE public.kb_topics SET is_active = true WHERE id = '44444444-4444-4444-4444-444444444444'::uuid;

SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claims" = '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}';

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_documents WHERE id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[0],
  'Student cannot view document under inactive subject'
);

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_document_pages WHERE document_id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[0],
  'Student cannot view pages under inactive subject'
);

-- 6. Verify Admin Can Always View Inactive Taxonomy Documents and Pages
RESET ROLE;
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claims" = '{"sub": "22222222-2222-2222-2222-222222222222", "role": "authenticated"}';

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_documents WHERE id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[1],
  'Admin can view document even when taxonomy is inactive'
);

SELECT results_eq(
  'SELECT count(*)::int FROM public.kb_document_pages WHERE document_id = ''55555555-5555-5555-5555-555555555555''::uuid',
  ARRAY[1],
  'Admin can view pages even when taxonomy is inactive'
);

-- 7. Verify Constraint on Retry Count
RESET ROLE;
SELECT throws_ok(
  'INSERT INTO public.kb_extraction_jobs (document_id, status, attempts, retry_count) VALUES (''55555555-5555-5555-5555-555555555555''::uuid, ''failed'', 3, 3)',
  '23514',
  NULL,
  'Cannot insert job with retry_count exceeding 2'
);

SELECT * FROM finish();
ROLLBACK;
