-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.1: pgTAP Database & Security Tests
-- Test: m4_1_chat_persistence_test.sql
-- ==============================================================================

BEGIN;
SELECT plan(28);

-- ------------------------------------------------------------------------------
-- 1. Table Existence
-- ------------------------------------------------------------------------------
SELECT has_table('public', 'chat_conversations', 'public.chat_conversations must exist');
SELECT has_table('public', 'chat_messages', 'public.chat_messages must exist');

-- ------------------------------------------------------------------------------
-- 2. Row Level Security Enabled
-- ------------------------------------------------------------------------------
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'chat_conversations' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on chat_conversations'
);
SELECT is(
  (SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'chat_messages' AND n.nspname = 'public'),
  true,
  'RLS must be enabled on chat_messages'
);

-- ------------------------------------------------------------------------------
-- 3. Anonymous Role Access Blocked
-- ------------------------------------------------------------------------------
SELECT is(
  has_table_privilege('anon', 'public.chat_conversations', 'SELECT'),
  false,
  'anon must NOT have SELECT on chat_conversations'
);
SELECT is(
  has_table_privilege('anon', 'public.chat_messages', 'SELECT'),
  false,
  'anon must NOT have SELECT on chat_messages'
);
SELECT is(
  has_table_privilege('anon', 'public.chat_conversations', 'INSERT'),
  false,
  'anon must NOT have INSERT on chat_conversations'
);
SELECT is(
  has_table_privilege('anon', 'public.chat_messages', 'INSERT'),
  false,
  'anon must NOT have INSERT on chat_messages'
);

-- ------------------------------------------------------------------------------
-- 4. Test Fixtures Setup (as superuser / postgres)
-- ------------------------------------------------------------------------------
CREATE TEMPORARY TABLE chat_test_vars (
  student_1_id uuid,
  student_2_id uuid,
  subject_id uuid,
  conv_s1_id uuid,
  msg_s1_id uuid
);
GRANT SELECT ON chat_test_vars TO authenticated;

DO $$
DECLARE
  v_s1 uuid;
  v_s2 uuid;
  v_subj uuid := gen_random_uuid();
  v_conv uuid := gen_random_uuid();
  v_msg uuid := gen_random_uuid();
BEGIN
  -- Insert or fetch student 1
  SELECT id INTO v_s1 FROM auth.users WHERE email = 'm41_student1@test.internal';
  IF v_s1 IS NULL THEN
    v_s1 := gen_random_uuid();
    INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
    VALUES (v_s1, 'm41_student1@test.internal', crypt('password123', gen_salt('bf')), now(), '{"provider":"email","providers":["email"]}', '{"display_name":"M41 Student 1"}');
    
    INSERT INTO public.profiles (id, email, display_name, role, approval_status)
    VALUES (v_s1, 'm41_student1@test.internal', 'M41 Student 1', 'user', 'approved')
    ON CONFLICT (id) DO UPDATE SET approval_status = 'approved';
  END IF;

  -- Insert or fetch student 2
  SELECT id INTO v_s2 FROM auth.users WHERE email = 'm41_student2@test.internal';
  IF v_s2 IS NULL THEN
    v_s2 := gen_random_uuid();
    INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
    VALUES (v_s2, 'm41_student2@test.internal', crypt('password123', gen_salt('bf')), now(), '{"provider":"email","providers":["email"]}', '{"display_name":"M41 Student 2"}');
    
    INSERT INTO public.profiles (id, email, display_name, role, approval_status)
    VALUES (v_s2, 'm41_student2@test.internal', 'M41 Student 2', 'user', 'approved')
    ON CONFLICT (id) DO UPDATE SET approval_status = 'approved';
  END IF;

  -- Create active subject
  INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
  VALUES (v_subj, 'M4.1 Operating Systems', 'm41-os', 'OS study materials', true);

  -- Create initial conversation owned by Student 1
  INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status, message_count, next_sequence_number)
  VALUES (v_conv, v_s1, v_subj, 'CPU Scheduling Thread', 'active', 1, 2);

  -- Create initial message for Student 1
  INSERT INTO public.chat_messages (id, conversation_id, sequence_number, role, content, status)
  VALUES (v_msg, v_conv, 1, 'user', 'What is Round Robin?', 'completed');

  INSERT INTO chat_test_vars VALUES (v_s1, v_s2, v_subj, v_conv, v_msg);
END $$;

-- ------------------------------------------------------------------------------
-- 5. Student 1 (Authenticated Owner) Operations
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_1_id::text FROM chat_test_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;
SET LOCAL ROLE authenticated;

-- Student 1 should see their own conversation
SELECT results_eq(
  'SELECT title FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars)',
  $$VALUES ('CPU Scheduling Thread')$$,
  'Student 1 should be able to view their own conversation'
);

-- Student 1 can insert a new conversation for themselves
PREPARE insert_own_conv AS
  INSERT INTO public.chat_conversations (user_id, subject_id, title)
  VALUES ((SELECT student_1_id FROM chat_test_vars), (SELECT subject_id FROM chat_test_vars), 'Memory Management');
SELECT lives_ok('insert_own_conv', 'Student 1 can insert conversation owned by themselves');

-- Student 1 CANNOT insert a conversation with Student 2 user_id (RLS WITH CHECK failure)
PREPARE forge_conv_user AS
  INSERT INTO public.chat_conversations (user_id, subject_id, title)
  VALUES ((SELECT student_2_id FROM chat_test_vars), (SELECT subject_id FROM chat_test_vars), 'Forged Conversation');
SELECT throws_ok(
  'forge_conv_user',
  '42501',
  NULL,
  'Student 1 must NOT be allowed to insert a conversation owned by Student 2'
);

-- Student 1 can view messages in their conversation
SELECT results_eq(
  'SELECT content FROM public.chat_messages WHERE conversation_id = (SELECT conv_s1_id FROM chat_test_vars)',
  $$VALUES ('What is Round Robin?')$$,
  'Student 1 can view messages in their own conversation'
);

-- Student 1 can insert a message with next sequence number
PREPARE insert_own_msg AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content)
  VALUES ((SELECT conv_s1_id FROM chat_test_vars), 2, 'assistant', 'Round Robin is a preemptive scheduling algorithm.');
SELECT lives_ok('insert_own_msg', 'Student 1 can insert a message into their own active conversation');

-- Duplicate sequence number in same conversation MUST fail (Unique constraint)
PREPARE insert_dup_seq AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content)
  VALUES ((SELECT conv_s1_id FROM chat_test_vars), 1, 'user', 'Duplicate sequence attempt');
SELECT throws_ok(
  'insert_dup_seq',
  '23505',
  NULL,
  'Duplicate sequence number in the same conversation must be rejected by unique constraint'
);

-- Sequence number <= 0 MUST fail (Check constraint)
PREPARE insert_zero_seq AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content)
  VALUES ((SELECT conv_s1_id FROM chat_test_vars), 0, 'user', 'Zero sequence attempt');
SELECT throws_ok(
  'insert_zero_seq',
  '23514',
  NULL,
  'Sequence number <= 0 must be rejected by check constraint'
);

-- Empty title MUST fail (Check constraint)
PREPARE insert_empty_title AS
  INSERT INTO public.chat_conversations (user_id, subject_id, title)
  VALUES ((SELECT student_1_id FROM chat_test_vars), (SELECT subject_id FROM chat_test_vars), '   ');
SELECT throws_ok(
  'insert_empty_title',
  '23514',
  NULL,
  'Whitespace/empty conversation title must be rejected by check constraint'
);

-- ------------------------------------------------------------------------------
-- 6. Student 2 (Non-Owner Cross-User Isolation) Operations
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_2_id::text FROM chat_test_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;

-- Student 2 MUST NOT see Student 1's conversations
SELECT is_empty(
  'SELECT * FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars)',
  'Student 2 must NOT see Student 1 conversations'
);

-- Student 2 MUST NOT see Student 1's messages
SELECT is_empty(
  'SELECT * FROM public.chat_messages WHERE conversation_id = (SELECT conv_s1_id FROM chat_test_vars)',
  'Student 2 must NOT see Student 1 messages'
);

-- Student 2 CANNOT update Student 1's conversation (0 rows affected)
DO $$
DECLARE
  v_rows integer;
BEGIN
  UPDATE public.chat_conversations SET title = 'Hacked Title' 
  WHERE id = (SELECT conv_s1_id FROM chat_test_vars);
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows != 0 THEN
    RAISE EXCEPTION 'Student 2 updated Student 1 conversation!';
  END IF;
END $$;
SELECT pass('Student 2 UPDATE on Student 1 conversation affects 0 rows');

-- Student 2 CANNOT insert messages into Student 1's conversation
PREPARE s2_insert_s1_msg AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content)
  VALUES ((SELECT conv_s1_id FROM chat_test_vars), 99, 'user', 'Injected message from S2');
SELECT throws_ok(
  's2_insert_s1_msg',
  '42501',
  NULL,
  'Student 2 must NOT be allowed to insert messages into Student 1 conversation'
);

-- Student 2 CANNOT delete Student 1's conversation
DO $$
DECLARE
  v_rows integer;
BEGIN
  DELETE FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars);
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows != 0 THEN
    RAISE EXCEPTION 'Student 2 deleted Student 1 conversation!';
  END IF;
END $$;
SELECT pass('Student 2 DELETE on Student 1 conversation affects 0 rows');

-- ------------------------------------------------------------------------------
-- 7. Soft Delete and Archive Lifecycle Verification
-- ------------------------------------------------------------------------------
-- Switch back to Student 1
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_1_id::text FROM chat_test_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
END $$;

-- Student 1 archives conversation
DO $$
BEGIN
  UPDATE public.chat_conversations 
  SET status = 'archived', archived_at = now() 
  WHERE id = (SELECT conv_s1_id FROM chat_test_vars);
END $$;

SELECT results_eq(
  'SELECT status FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars)',
  $$VALUES ('archived'::public.chat_conversation_status)$$,
  'Student 1 conversation status should be archived'
);

-- Messages are still readable when conversation is archived
SELECT is(
  (SELECT count(*)::integer FROM public.chat_messages WHERE conversation_id = (SELECT conv_s1_id FROM chat_test_vars)),
  2,
  'Messages in archived conversation remain readable to owner'
);

-- But new messages CANNOT be inserted into archived conversation (RLS status = active check)
PREPARE insert_archived_msg AS
  INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content)
  VALUES ((SELECT conv_s1_id FROM chat_test_vars), 3, 'user', 'Message into archived conv');
SELECT throws_ok(
  'insert_archived_msg',
  '42501',
  NULL,
  'Inserting messages into an archived conversation must be rejected by RLS'
);

-- Restore conversation
DO $$
BEGIN
  UPDATE public.chat_conversations 
  SET status = 'active', archived_at = NULL 
  WHERE id = (SELECT conv_s1_id FROM chat_test_vars);
END $$;

SELECT results_eq(
  'SELECT status FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars)',
  $$VALUES ('active'::public.chat_conversation_status)$$,
  'Restored conversation status should be active'
);

-- Soft delete conversation: when status = 'deleted', conversation is hidden from SELECT
-- Delete conversation: Student 1 deletes their own conversation
DO $$
BEGIN
  PERFORM set_config('request.jwt.claim.sub', (SELECT student_1_id::text FROM chat_test_vars), true);
  PERFORM set_config('request.jwt.claim.role', 'authenticated', true);
  DELETE FROM public.chat_conversations 
  WHERE id = (SELECT conv_s1_id FROM chat_test_vars);
END $$;

SELECT is_empty(
  'SELECT * FROM public.chat_conversations WHERE id = (SELECT conv_s1_id FROM chat_test_vars)',
  'Deleted conversation must no longer exist'
);

SELECT is_empty(
  'SELECT * FROM public.chat_messages WHERE conversation_id = (SELECT conv_s1_id FROM chat_test_vars)',
  'Messages of deleted conversation must be cascade deleted'
);

-- ------------------------------------------------------------------------------
-- 8. Subject Foreign Key ON DELETE RESTRICT
-- ------------------------------------------------------------------------------
-- Switch to postgres role to test FK constraint
RESET ROLE;
PREPARE delete_subject_with_conv AS
  DELETE FROM public.kb_subjects WHERE id = (SELECT subject_id FROM chat_test_vars);
SELECT throws_ok(
  'delete_subject_with_conv',
  '23503',
  NULL,
  'Deleting subject referenced by existing conversation must be restricted (ON DELETE RESTRICT)'
);

SELECT * FROM finish();
ROLLBACK;
