-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.1 pgTAP Database & Security Tests
-- (Including Security Hardening Remediation)
-- ==============================================================================

BEGIN;
SELECT plan(44);

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
-- 3b. Extraction Jobs Privilege Hardening (SEC-M3.1)
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('authenticated', 'public.kb_extraction_jobs', 'SELECT'),
  false,
  'authenticated role must NOT have direct SELECT on kb_extraction_jobs'
);
SELECT is(
  has_table_privilege('anon', 'public.kb_extraction_jobs', 'SELECT'),
  false,
  'anon role must NOT have direct SELECT on kb_extraction_jobs'
);
SELECT is(
  has_table_privilege('service_role', 'public.kb_extraction_jobs', 'SELECT'),
  true,
  'service_role MUST have SELECT on kb_extraction_jobs'
);
SELECT is(
  has_table_privilege('service_role', 'public.kb_extraction_jobs', 'INSERT'),
  true,
  'service_role MUST have INSERT on kb_extraction_jobs'
);
SELECT is(
  has_table_privilege('service_role', 'public.kb_extraction_jobs', 'UPDATE'),
  true,
  'service_role MUST have UPDATE on kb_extraction_jobs'
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
  doc_s1_uploaded_id uuid,
  doc_s1_pending_id uuid,
  doc_s1_failed_id uuid,
  doc_s1_deleting_id uuid,
  doc_s1_deleted_id uuid,
  doc_s1_archived_id uuid,
  doc_s2_pending_id uuid,
  doc_s2_failed_id uuid,
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
  v_doc_s1_up uuid := gen_random_uuid();
  v_doc_s1_pend uuid := gen_random_uuid();
  v_doc_s1_fail uuid := gen_random_uuid();
  v_doc_s1_delng uuid := gen_random_uuid();
  v_doc_s1_del uuid := gen_random_uuid();
  v_doc_s1_arch uuid := gen_random_uuid();
  v_doc_s2_pend uuid := gen_random_uuid();
  v_doc_s2_fail uuid := gen_random_uuid();
  v_page uuid := gen_random_uuid();
BEGIN
  -- Create isolated dedicated test users for this transaction
  v_admin := gen_random_uuid();
  INSERT INTO auth.users (id, email) VALUES (v_admin, 'test_kb_admin_' || v_admin || '@test.edu');
  UPDATE public.profiles SET role = 'admin', approval_status = 'approved' WHERE id = v_admin;

  v_stud_app := gen_random_uuid();
  INSERT INTO auth.users (id, email) VALUES (v_stud_app, 'test_kb_app1_' || v_stud_app || '@test.edu');
  UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = v_stud_app;

  v_stud_app2 := gen_random_uuid();
  INSERT INTO auth.users (id, email) VALUES (v_stud_app2, 'test_kb_app2_' || v_stud_app2 || '@test.edu');
  UPDATE public.profiles SET role = 'user', approval_status = 'approved' WHERE id = v_stud_app2;

  v_stud_unapp := gen_random_uuid();
  INSERT INTO auth.users (id, email) VALUES (v_stud_unapp, 'test_kb_unapp_' || v_stud_unapp || '@test.edu');
  UPDATE public.profiles SET role = 'user', approval_status = 'pending' WHERE id = v_stud_unapp;

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

  -- Insert documents with comprehensive lifecycle states
  -- Student 1 Documents
  INSERT INTO public.kb_documents (
    id, subject_id, topic_id, uploader_id, title, original_filename, 
    storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
    total_pages, processed_pages, pages_with_text, is_archived
  ) VALUES
    -- 1. Shared uploaded document
    (v_doc_s1_up, v_sub_act, v_top1, v_stud_app, 'DBMS Shared Uploaded (S1)', 'unit1.pdf',
     format('documents/%s/source.pdf', v_doc_s1_up), 'application/pdf', 1024, 'uploaded', 'completed',
     1, 1, 1, false),
    -- 2. Pending upload (uploader should see)
    (v_doc_s1_pend, v_sub_act, v_top1, v_stud_app, 'DBMS Pending (S1)', 'pending.pdf',
     format('documents/%s/source.pdf', v_doc_s1_pend), 'application/pdf', 2048, 'pending_upload', 'pending',
     0, 0, 0, false),
    -- 3. Failed upload (uploader should see)
    (v_doc_s1_fail, v_sub_act, v_top1, v_stud_app, 'DBMS Failed (S1)', 'failed.pdf',
     format('documents/%s/source.pdf', v_doc_s1_fail), 'application/pdf', 2048, 'upload_failed', 'failed',
     0, 0, 0, false),
    -- 4. Deleting document (uploader should NOT see via ordinary policy)
    (v_doc_s1_delng, v_sub_act, v_top1, v_stud_app, 'DBMS Deleting (S1)', 'deleting.pdf',
     format('documents/%s/source.pdf', v_doc_s1_delng), 'application/pdf', 2048, 'deleting', 'pending',
     0, 0, 0, false),
    -- 5. Deleted document (uploader should NOT see via ordinary policy)
    (v_doc_s1_del, v_sub_act, v_top1, v_stud_app, 'DBMS Deleted (S1)', 'deleted.pdf',
     format('documents/%s/source.pdf', v_doc_s1_del), 'application/pdf', 2048, 'deleted', 'pending',
     0, 0, 0, false),
    -- 6. Archived document (uploader should NOT see via ordinary policy)
    (v_doc_s1_arch, v_sub_act, v_top1, v_stud_app, 'DBMS Archived (S1)', 'archived.pdf',
     format('documents/%s/source.pdf', v_doc_s1_arch), 'application/pdf', 3072, 'uploaded', 'completed',
     1, 1, 1, true),
    -- Student 2 Documents
    -- 7. Student 2 pending upload (Student 1 should NOT see)
    (v_doc_s2_pend, v_sub_act, v_top1, v_stud_app2, 'DBMS Pending (S2)', 's2_pend.pdf',
     format('documents/%s/source.pdf', v_doc_s2_pend), 'application/pdf', 4096, 'pending_upload', 'pending',
     0, 0, 0, false),
    -- 8. Student 2 failed upload (Student 1 should NOT see)
    (v_doc_s2_fail, v_sub_act, v_top1, v_stud_app2, 'DBMS Failed (S2)', 's2_fail.pdf',
     format('documents/%s/source.pdf', v_doc_s2_fail), 'application/pdf', 4096, 'upload_failed', 'failed',
     0, 0, 0, false);

  -- Insert document page for uploaded document
  INSERT INTO public.kb_document_pages (id, document_id, page_number, raw_text, character_count, word_count, has_text)
  VALUES (v_page, v_doc_s1_up, 1, 'Hello DBMS World', 16, 3, true);

  -- Insert extraction job
  INSERT INTO public.kb_extraction_jobs (document_id, status, attempts, worker_id, lease_token)
  VALUES (v_doc_s1_up, 'completed', 1, 'worker-node-alpha', gen_random_uuid());

  -- Save IDs to temp table
  INSERT INTO test_vars VALUES (
    v_admin, v_stud_app, v_stud_app2, v_stud_unapp,
    v_sub_act, v_sub_inact, v_top1, v_top2, v_top_inact, v_top_sub2,
    v_doc_s1_up, v_doc_s1_pend, v_doc_s1_fail, v_doc_s1_delng, v_doc_s1_del, v_doc_s1_arch,
    v_doc_s2_pend, v_doc_s2_fail, v_page
  );
END $$;

-- ------------------------------------------------------------------------------
-- 5. Privileges & Isolation: Anonymous User (No Table Privileges)
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
SELECT is(
  has_table_privilege('anon', 'public.kb_extraction_jobs', 'SELECT'),
  false,
  'Anonymous role must NOT have SELECT on public.kb_extraction_jobs'
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
-- 7. RLS: Approved Student Access & Owner Lifecycle Visibility
-- ------------------------------------------------------------------------------
SET LOCAL ROLE authenticated;
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_approved_id::text FROM test_vars), true);
END $$;

-- Approved student sees active subject, but NOT inactive subject
SELECT results_eq(
  'SELECT slug FROM public.kb_subjects WHERE id IN (SELECT subject_active_id FROM test_vars UNION ALL SELECT subject_inactive_id FROM test_vars) ORDER BY slug',
  $$VALUES ('dbms')$$,
  'Approved student should see ONLY active subjects'
);

-- Approved student sees active topics under active subject, NOT inactive or under inactive subject
SELECT results_eq(
  'SELECT slug FROM public.kb_topics WHERE subject_id IN (SELECT subject_active_id FROM test_vars UNION ALL SELECT subject_inactive_id FROM test_vars) ORDER BY slug',
  $$VALUES ('relational-model'), ('transactions')$$,
  'Approved student should see ONLY active topics under active subjects'
);

-- Student 1 should see:
--  - DBMS Shared Uploaded (S1) [uploaded, not archived]
--  - DBMS Pending (S1)         [own pending_upload]
--  - DBMS Failed (S1)          [own upload_failed]
-- MUST NOT see:
--  - DBMS Deleting (S1)        [own deleting - excluded by policy]
--  - DBMS Deleted (S1)         [own deleted - excluded by policy]
--  - DBMS Archived (S1)        [is_archived = true]
--  - DBMS Pending (S2)         [other student pending]
--  - DBMS Failed (S2)          [other student failed]
SELECT results_eq(
  'SELECT title FROM public.kb_documents WHERE subject_id = (SELECT subject_active_id FROM test_vars) ORDER BY title',
  $$VALUES ('DBMS Failed (S1)'), ('DBMS Pending (S1)'), ('DBMS Shared Uploaded (S1)')$$,
  'Student 1 should see shared uploaded docs + own pending and failed uploads ONLY'
);

-- Student 2 check:
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_approved_2_id::text FROM test_vars), true);
END $$;

SELECT results_eq(
  'SELECT title FROM public.kb_documents WHERE subject_id = (SELECT subject_active_id FROM test_vars) ORDER BY title',
  $$VALUES ('DBMS Failed (S2)'), ('DBMS Pending (S2)'), ('DBMS Shared Uploaded (S1)')$$,
  'Student 2 should see shared uploaded docs + own pending and failed uploads ONLY'
);

-- Switch back to Student 1
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_approved_id::text FROM test_vars), true);
END $$;

-- Approved student can read pages of accessible uploaded document
SELECT isnt_empty(
  'SELECT * FROM public.kb_document_pages WHERE document_id = (SELECT doc_s1_uploaded_id FROM test_vars)',
  'Approved student can read pages of accessible uploaded document'
);

-- Authenticated user is completely blocked from SELECT on kb_extraction_jobs (SEC-M3.1)
SELECT throws_ok(
  'SELECT * FROM public.kb_extraction_jobs',
  '42501',
  NULL,
  'Authenticated user must be blocked from direct SELECT on kb_extraction_jobs'
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
  'SELECT slug FROM public.kb_subjects WHERE id IN (SELECT subject_active_id FROM test_vars UNION ALL SELECT subject_inactive_id FROM test_vars) ORDER BY slug',
  $$VALUES ('dbms'), ('physics-old')$$,
  'Admin should see all subjects including inactive'
);

-- Admin sees ALL topics
SELECT results_eq(
  'SELECT slug FROM public.kb_topics WHERE subject_id IN (SELECT subject_active_id FROM test_vars UNION ALL SELECT subject_inactive_id FROM test_vars) ORDER BY slug',
  $$VALUES ('normalization-draft'), ('quantum-basics'), ('relational-model'), ('transactions')$$,
  'Admin should see all topics including inactive'
);

-- Admin sees ALL documents (all 8 across all statuses and users)
SELECT results_eq(
  'SELECT title FROM public.kb_documents WHERE subject_id = (SELECT subject_active_id FROM test_vars) ORDER BY title',
  $$VALUES 
    ('DBMS Archived (S1)'), 
    ('DBMS Deleted (S1)'), 
    ('DBMS Deleting (S1)'), 
    ('DBMS Failed (S1)'), 
    ('DBMS Failed (S2)'), 
    ('DBMS Pending (S1)'), 
    ('DBMS Pending (S2)'), 
    ('DBMS Shared Uploaded (S1)')$$,
  'Admin should see all documents across all lifecycle statuses'
);

-- ------------------------------------------------------------------------------
-- 9. Database Integrity & Constraints Verification (as superuser)
-- ------------------------------------------------------------------------------
RESET ROLE;

-- 9.1 Composite Subject/Topic Mismatch is rejected
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
    (SELECT doc_s1_uploaded_id FROM test_vars)
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
    (SELECT doc_s1_uploaded_id FROM test_vars)
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
    (SELECT doc_s1_uploaded_id FROM test_vars)
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
