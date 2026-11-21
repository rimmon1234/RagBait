-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.1 pgTAP Database & Security Tests
-- ==============================================================================

BEGIN;
SELECT plan(37);

-- ------------------------------------------------------------------------------
-- 1. Table Existence (5 tables)
-- ------------------------------------------------------------------------------
SELECT has_table('public', 'kb_subjects', 'public.kb_subjects must exist');
SELECT has_table('public', 'kb_topics', 'public.kb_topics must exist');
SELECT has_table('public', 'kb_documents', 'public.kb_documents must exist');
SELECT has_table('public', 'kb_document_pages', 'public.kb_document_pages must exist');
SELECT has_table('public', 'kb_extraction_jobs', 'public.kb_extraction_jobs must exist');

-- ------------------------------------------------------------------------------
-- 2. Row Level Security Enabled (5 tables)
-- ------------------------------------------------------------------------------
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_subjects' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_subjects'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_topics' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_topics'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_documents' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_documents'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_document_pages' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_document_pages'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'kb_extraction_jobs' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on kb_extraction_jobs'
);

-- ------------------------------------------------------------------------------
-- 3. Table Privileges & Direct Write Protection (authenticated role)
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('authenticated', 'public.kb_subjects', 'INSERT'),
  false,
  'authenticated role must NOT have direct INSERT on kb_subjects'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_topics', 'INSERT'),
  false,
  'authenticated role must NOT have direct INSERT on kb_topics'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_documents', 'INSERT'),
  false,
  'authenticated role must NOT have direct INSERT on kb_documents (backend service only)'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_document_pages', 'INSERT'),
  false,
  'authenticated role must NOT have direct INSERT on kb_document_pages'
);
SELECT is(
  has_table_privilege('authenticated', 'public.kb_extraction_jobs', 'INSERT'),
  false,
  'authenticated role must NOT have direct INSERT on kb_extraction_jobs'
);

-- ------------------------------------------------------------------------------
-- 4. Test Fixtures Setup (as postgres / service_role)
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE test_vars (
  admin_id uuid,
  student_approved_id uuid,
  student_approved_2_id uuid,
  student_unapproved_id uuid,
  subject_active_id uuid,
  subject_inactive_id uuid,
  topic_1_id uuid,
  topic_2_id uuid,
  topic_inactive_id uuid,
  topic_sub2_id uuid,
  doc_uploaded_id uuid,
  doc_pending_id uuid,
  doc_archived_id uuid,
  doc_other_pending_id uuid,
  page_1_id uuid
);
GRANT SELECT ON test_vars TO authenticated;

DO $$
DECLARE
  v_admin uuid;
  v_stud_app uuid;
  v_stud_app2 uuid;
  v_stud_unapp uuid;
  v_sub_act uuid := gen_random_uuid();
  v_sub_inact uuid := gen_random_uuid();
  v_top1 uuid := gen_random_uuid();
  v_top2 uuid := gen_random_uuid();
  v_top_inact uuid := gen_random_uuid();
  v_top_sub2 uuid := gen_random_uuid();
  v_doc_up uuid := gen_random_uuid();
  v_doc_pend uuid := gen_random_uuid();
  v_doc_arch uuid := gen_random_uuid();
  v_doc_other_pend uuid := gen_random_uuid();
  v_page uuid := gen_random_uuid();
BEGIN
  -- Look up existing profiles or create fresh in auth.users
  SELECT id INTO v_admin FROM public.profiles WHERE email = 'rimmon.bhowmick.cse28@heritageit.edu.in';
  SELECT id INTO v_stud_app FROM public.profiles WHERE email = 'student.approved@heritageit.edu.in';
  SELECT id INTO v_stud_app2 FROM public.profiles WHERE email = 'student.nocreds@heritageit.edu.in';
  SELECT id INTO v_stud_unapp FROM public.profiles WHERE email = 'rimmonbhowmick@gmail.com';

  IF v_admin IS NULL THEN
    v_admin := gen_random_uuid();
    INSERT INTO auth.users (id, email) VALUES (v_admin, 'test_admin_pgtap@test.edu');
    UPDATE public.profiles SET role = 'admin', approval_status = 'approved' WHERE id = v_admin;
  END IF;

  IF v_stud_app IS NULL THEN
    v_stud_app := gen_random_uuid();
    INSERT INTO auth.users (id, email) VALUES (v_stud_app, 'test_stud_app_pgtap@test.edu');
    UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = v_stud_app;
  END IF;

  IF v_stud_app2 IS NULL THEN
    v_stud_app2 := gen_random_uuid();
    INSERT INTO auth.users (id, email) VALUES (v_stud_app2, 'test_stud_app2_pgtap@test.edu');
    UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = v_stud_app2;
  END IF;

  IF v_stud_unapp IS NULL THEN
    v_stud_unapp := gen_random_uuid();
    INSERT INTO auth.users (id, email) VALUES (v_stud_unapp, 'test_stud_unapp_pgtap@test.edu');
    UPDATE public.profiles SET role = 'user', approval_status = 'pending' WHERE id = v_stud_unapp;
  END IF;

  -- Insert subjects
  INSERT INTO public.kb_subjects (id, name, slug, description, display_order, is_active, created_by)
  VALUES
    (v_sub_act, 'Database Systems', 'dbms', 'DBMS Notes', 1, true, v_admin),
    (v_sub_inact, 'Archived Physics', 'physics-old', 'Old Notes', 2, false, v_admin);

  -- Insert topics
  INSERT INTO public.kb_topics (id, subject_id, name, slug, display_order, is_active, created_by)
  VALUES
    (v_top1, v_sub_act, 'Relational Model', 'relational-model', 1, true, v_admin),
    (v_top2, v_sub_act, 'Transactions', 'transactions', 2, true, v_admin),
    (v_top_inact, v_sub_act, 'Draft Normalization', 'normalization-draft', 3, false, v_admin),
    (v_top_sub2, v_sub_inact, 'Quantum Basics', 'quantum-basics', 1, true, v_admin);

  -- Insert documents
  INSERT INTO public.kb_documents (
    id, subject_id, topic_id, uploader_id, title, original_filename, 
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    total_pages, processed_pages, pages_with_text, is_archived
  ) VALUES
    (v_doc_up, v_sub_act, v_top1, v_stud_app, 'DBMS Unit 1', 'unit1.pdf',
     format('documents/%s/source.pdf', v_doc_up), 'application/pdf', 1024, 'uploaded', 'completed',
     1, 1, 1, false),
    (v_doc_pend, v_sub_act, v_top1, v_stud_app, 'DBMS Pending', 'pending.pdf',
     format('documents/%s/source.pdf', v_doc_pend), 'application/pdf', 2048, 'pending_upload', 'pending',
     0, 0, 0, false),
    (v_doc_arch, v_sub_act, v_top1, v_stud_app, 'DBMS Archived', 'archived.pdf',
     format('documents/%s/source.pdf', v_doc_arch), 'application/pdf', 3072, 'uploaded', 'completed',
     1, 1, 1, true),
    (v_doc_other_pend, v_sub_act, v_top1, v_stud_app2, 'Other Pending', 'other_pending.pdf',
     format('documents/%s/source.pdf', v_doc_other_pend), 'application/pdf', 4096, 'pending_upload', 'pending',
     0, 0, 0, false);

  -- Insert document page
  INSERT INTO public.kb_document_pages (id, document_id, page_number, raw_text, character_count, word_count, has_text)
  VALUES (v_page, v_doc_up, 1, 'Hello DBMS World', 16, 3, true);

  -- Insert extraction job
  INSERT INTO public.kb_extraction_jobs (document_id, status, attempts)
  VALUES (v_doc_up, 'completed', 1);

  -- Save IDs to temp table
  INSERT INTO test_vars VALUES (
    v_admin, v_stud_app, v_stud_app2, v_stud_unapp,
    v_sub_act, v_sub_inact, v_top1, v_top2, v_top_inact, v_top_sub2,
    v_doc_up, v_doc_pend, v_doc_arch, v_doc_other_pend, v_page
  );
END $$;

-- ------------------------------------------------------------------------------
-- 5. Privileges & Isolation: Anonymous User (No Access)
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('anon', 'public.kb_subjects', 'SELECT'),
  false,
  'Anonymous role must NOT have SELECT on public.kb_subjects'
);
SELECT is(
  has_table_privilege('anon', 'public.kb_documents', 'SELECT'),
  false,
  'Anonymous role must NOT have SELECT on public.kb_documents'
);

-- ------------------------------------------------------------------------------
-- 6. RLS: Unapproved User Isolation
-- ------------------------------------------------------------------------------
SET LOCAL ROLE authenticated;
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_unapproved_id::text FROM test_vars), true);
END $$;

SELECT is_empty(
  'SELECT * FROM public.kb_subjects',
  'Unapproved student should see 0 subjects'
);
SELECT is_empty(
  'SELECT * FROM public.kb_topics',
  'Unapproved student should see 0 topics'
);
SELECT is_empty(
  'SELECT * FROM public.kb_documents',
  'Unapproved student should see 0 documents'
);
SELECT is_empty(
  'SELECT * FROM public.kb_document_pages',
  'Unapproved student should see 0 document pages'
);

-- ------------------------------------------------------------------------------
-- 7. RLS: Approved Student Access
-- ------------------------------------------------------------------------------
SET LOCAL ROLE authenticated;
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_approved_id::text FROM test_vars), true);
END $$;

-- Approved student sees active subject, but NOT inactive subject
SELECT results_eq(
  'SELECT slug FROM public.kb_subjects ORDER BY slug',
  $$VALUES ('dbms')$$,
  'Approved student should see ONLY active subjects'
);

-- Approved student sees active topics under active subject, NOT inactive or under inactive subject
SELECT results_eq(
  'SELECT slug FROM public.kb_topics ORDER BY slug',
  $$VALUES ('relational-model'), ('transactions')$$,
  'Approved student should see ONLY active topics under active subjects'
);

-- Approved student sees uploaded non-archived documents + their own pending upload
-- Document 1 (uploaded, not archived) + Document 2 (own pending_upload)
-- Does NOT see Document 3 (archived) or Document 4 (other user pending)
SELECT results_eq(
  'SELECT title FROM public.kb_documents ORDER BY title',
  $$VALUES ('DBMS Pending'), ('DBMS Unit 1')$$,
  'Approved student should see uploaded non-archived docs + their own pending upload'
);

-- Approved student can read pages of accessible uploaded document
SELECT isnt_empty(
  'SELECT * FROM public.kb_document_pages',
  'Approved student can read pages of accessible uploaded document'
);

-- Approved student can read job status for their own document
SELECT isnt_empty(
  'SELECT * FROM public.kb_extraction_jobs',
  'Approved student can read extraction job status for their own document'
);

-- ------------------------------------------------------------------------------
-- 8. RLS: Administrator Access
-- ------------------------------------------------------------------------------
SET LOCAL ROLE authenticated;
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT admin_id::text FROM test_vars), true);
END $$;

-- Admin sees ALL subjects (including inactive)
SELECT results_eq(
  'SELECT slug FROM public.kb_subjects ORDER BY slug',
  $$VALUES ('dbms'), ('physics-old')$$,
  'Admin should see all subjects including inactive'
);

-- Admin sees ALL topics
SELECT results_eq(
  'SELECT slug FROM public.kb_topics ORDER BY slug',
  $$VALUES ('normalization-draft'), ('quantum-basics'), ('relational-model'), ('transactions')$$,
  'Admin should see all topics including inactive'
);

-- Admin sees ALL documents (uploaded, pending, archived, all users)
SELECT results_eq(
  'SELECT title FROM public.kb_documents ORDER BY title',
  $$VALUES ('DBMS Archived'), ('DBMS Pending'), ('DBMS Unit 1'), ('Other Pending')$$,
  'Admin should see all documents across all statuses'
);

-- ------------------------------------------------------------------------------
-- 9. Database Integrity & Constraints Verification (as superuser)
-- ------------------------------------------------------------------------------
RESET ROLE;

-- 9.1 Composite Subject/Topic Mismatch is rejected
-- Attempt to insert document with Subject 1 but Topic from Subject 2 (topic_sub2_id)
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_documents (
        subject_id, topic_id, title, original_filename, storage_path, mime_type, file_size_bytes
      ) VALUES (
        '%s', '%s', 'Invalid Doc', 'inv.pdf', 'documents/00000000-0000-0000-0000-000000000001/source.pdf', 'application/pdf', 100
      )$$,
    (SELECT subject_active_id FROM test_vars),
    (SELECT topic_sub2_id FROM test_vars)
  ),
  '23503', -- foreign_key_violation
  NULL,
  'Composite FK must reject topic belonging to a different subject'
);

-- 9.2 Duplicate topic slug under same subject is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_topics (subject_id, name, slug) VALUES ('%s', 'Duplicate Slug', 'relational-model')$$,
    (SELECT subject_active_id FROM test_vars)
  ),
  '23505', -- unique_violation
  NULL,
  'Duplicate topic slug under same subject must be rejected'
);

-- 9.3 Duplicate page number for same document is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
      VALUES ('%s', 1, 'Duplicate Page', 14, 2, true)$$,
    (SELECT doc_uploaded_id FROM test_vars)
  ),
  '23505', -- unique_violation
  NULL,
  'Duplicate page number for same document must be rejected'
);

-- 9.4 Invalid MIME type is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_documents (
        subject_id, topic_id, title, original_filename, storage_path, mime_type, file_size_bytes
      ) VALUES (
        '%s', '%s', 'Bad MIME', 'bad.exe', 'documents/00000000-0000-0000-0000-000000000002/source.pdf', 'application/x-msdownload', 100
      )$$,
    (SELECT subject_active_id FROM test_vars),
    (SELECT topic_1_id FROM test_vars)
  ),
  '23514', -- check_violation
  NULL,
  'Invalid MIME type must be rejected by check constraint'
);

-- 9.5 Negative file size is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_documents (
        subject_id, topic_id, title, original_filename, storage_path, mime_type, file_size_bytes
      ) VALUES (
        '%s', '%s', 'Negative Size', 'neg.pdf', 'documents/00000000-0000-0000-0000-000000000003/source.pdf', 'application/pdf', -5
      )$$,
    (SELECT subject_active_id FROM test_vars),
    (SELECT topic_1_id FROM test_vars)
  ),
  '23514', -- check_violation
  NULL,
  'Negative file size must be rejected'
);

-- 9.6 Non-canonical storage path is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_documents (
        subject_id, topic_id, title, original_filename, storage_path, mime_type, file_size_bytes
      ) VALUES (
        '%s', '%s', 'Bad Path', 'path.pdf', 'malicious/../path.pdf', 'application/pdf', 100
      )$$,
    (SELECT subject_active_id FROM test_vars),
    (SELECT topic_1_id FROM test_vars)
  ),
  '23514', -- check_violation
  NULL,
  'Non-canonical storage path format must be rejected'
);

-- 9.7 Negative page number on page record is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
      VALUES ('%s', -1, 'Invalid Page Num', 16, 3, true)$$,
    (SELECT doc_uploaded_id FROM test_vars)
  ),
  '23514', -- check_violation
  NULL,
  'Negative or zero page number must be rejected'
);

-- 9.8 Mismatch between has_text and non-whitespace text is rejected
SELECT throws_ok(
  format(
    $$INSERT INTO public.kb_document_pages (document_id, page_number, raw_text, character_count, word_count, has_text)
      VALUES ('%s', 2, 'Has Text Here', 13, 3, false)$$,
    (SELECT doc_uploaded_id FROM test_vars)
  ),
  '23514', -- check_violation
  NULL,
  'Inconsistent has_text flag must be rejected by check constraint'
);

-- ------------------------------------------------------------------------------
-- 10. Finish pgTAP Test Suite
-- ------------------------------------------------------------------------------
SELECT * FROM finish();
ROLLBACK;
