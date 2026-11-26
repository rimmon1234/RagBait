BEGIN;
SELECT plan(8);

SELECT has_table('public', 'chat_message_web_sources', 'chat_message_web_sources table exists');

SELECT has_function(
    'public',
    'mark_chat_message_web_sources_referenced',
    ARRAY['uuid', 'text[]'],
    'mark_chat_message_web_sources_referenced helper exists'
);

SELECT ok(
    NOT has_table_privilege('anon', 'public.chat_message_web_sources', 'SELECT'),
    'anon cannot SELECT chat_message_web_sources'
);
SELECT ok(
    NOT has_table_privilege('authenticated', 'public.chat_message_web_sources', 'INSERT'),
    'authenticated cannot INSERT chat_message_web_sources directly'
);
SELECT ok(
    NOT has_function_privilege('authenticated', 'public.mark_chat_message_web_sources_referenced(uuid, text[])', 'EXECUTE'),
    'authenticated cannot execute SECURITY DEFINER web provenance mutator directly'
);

-- Setup ephemeral fixtures for immutability trigger verification
DO $$
DECLARE
    v_user_id uuid := gen_random_uuid();
    v_subj_id uuid := gen_random_uuid();
    v_conv_id uuid := gen_random_uuid();
    v_msg_id uuid := gen_random_uuid();
    v_web_id uuid;
    v_err text;
BEGIN
    INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
    VALUES (v_user_id, '00000000-0000-0000-0000-000000000000', 'pgtap_m49_' || left(v_user_id::text, 6) || '@test.edu', 'authenticated', 'authenticated', '{}'::jsonb);
    UPDATE public.profiles SET approval_status = 'approved' WHERE id = v_user_id;
    INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
    VALUES (v_subj_id, 'M49 Subject ' || left(v_subj_id::text, 6), 'm49-subj-' || left(v_subj_id::text, 8), 'M49', true);
    INSERT INTO public.chat_conversations (id, user_id, subject_id, title)
    VALUES (v_conv_id, v_user_id, v_subj_id, 'M49 Test Conv');
    INSERT INTO public.chat_messages (id, conversation_id, sequence_number, role, content, status)
    VALUES (v_msg_id, v_conv_id, 1, 'assistant', 'Answer [W1].', 'completed');

    INSERT INTO public.chat_message_web_sources (
        message_id, citation_key, title, url, domain, excerpt, content_hash, snippet_only
    )
    VALUES (
        v_msg_id, 'W1', 'Python Docs', 'https://docs.python.org/3.15/', 'docs.python.org',
        'Python 3.15 preview snippet', 'sha-test-1', true
    )
    RETURNING id INTO v_web_id;

    -- 1. Mutating immutable URL/excerpt raises SQLSTATE 22000
    BEGIN
        UPDATE public.chat_message_web_sources SET url = 'https://evil.example/' WHERE id = v_web_id;
        RAISE EXCEPTION 'Expected immutability violation';
    EXCEPTION WHEN data_exception THEN
        -- Expected
    END;

    -- 2. Controlled false -> true via SECURITY DEFINER succeeds
    PERFORM public.mark_chat_message_web_sources_referenced(v_msg_id, ARRAY['W1']);

    -- 3. Reverting referenced_in_answer true -> false fails
    BEGIN
        UPDATE public.chat_message_web_sources SET referenced_in_answer = false WHERE id = v_web_id;
        RAISE EXCEPTION 'Expected provenance revert rejection';
    EXCEPTION WHEN data_exception THEN
        -- Expected
    END;
END $$;

SELECT ok(
    EXISTS (
        SELECT 1 FROM public.chat_message_web_sources WHERE domain = 'docs.python.org' AND referenced_in_answer = true
    ),
    'Controlled referenced_in_answer false -> true transition persisted'
);

SELECT throws_ok(
    $$ SELECT public.mark_chat_message_web_sources_referenced(gen_random_uuid(), ARRAY['W99']) $$,
    '22000',
    NULL,
    'Unknown web citation key rejected by SECURITY DEFINER provenance mutator'
);

SELECT col_not_null('public', 'chat_message_web_sources', 'content_hash', 'content_hash is NOT NULL');

SELECT * FROM finish();
ROLLBACK;
