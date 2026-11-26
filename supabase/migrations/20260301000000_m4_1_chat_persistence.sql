-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.1: Persistent Chat Infrastructure & Conversations
-- Migration: 20260301000000_m4_1_chat_persistence.sql
-- ==============================================================================

-- 1. Conversation Lifecycle Enum
CREATE TYPE public.chat_conversation_status AS ENUM (
    'active',
    'archived',
    'deleted'
);

-- 2. Message Role & Status Enums
CREATE TYPE public.chat_message_role AS ENUM (
    'user',
    'assistant'
);

CREATE TYPE public.chat_message_status AS ENUM (
    'pending',
    'streaming',
    'completed',
    'failed',
    'cancelled'
);

-- 3. Conversations Table
CREATE TABLE public.chat_conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    subject_id uuid NOT NULL REFERENCES public.kb_subjects(id) ON DELETE RESTRICT,
    title text NOT NULL,
    status public.chat_conversation_status NOT NULL DEFAULT 'active',
    message_count integer NOT NULL DEFAULT 0,
    next_sequence_number integer NOT NULL DEFAULT 1,
    last_message_at timestamptz,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT ck_chat_conv_title_not_empty CHECK (char_length(trim(title)) > 0 AND char_length(title) <= 255),
    CONSTRAINT ck_chat_conv_seq_positive CHECK (next_sequence_number >= 1),
    CONSTRAINT ck_chat_conv_msg_count CHECK (message_count >= 0)
);

CREATE INDEX idx_chat_conv_user_status_updated 
    ON public.chat_conversations (user_id, status, updated_at DESC, id DESC);
CREATE INDEX idx_chat_conv_subject_user 
    ON public.chat_conversations (subject_id, user_id);

CREATE TRIGGER tr_chat_conversations_updated_at
BEFORE UPDATE ON public.chat_conversations
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- 4. Messages Table
CREATE TABLE public.chat_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES public.chat_conversations(id) ON DELETE CASCADE,
    sequence_number integer NOT NULL,
    role public.chat_message_role NOT NULL,
    content text NOT NULL DEFAULT '',
    status public.chat_message_status NOT NULL DEFAULT 'completed',
    generation_request_id text,
    idempotency_key text,
    model_id text,
    provider_id text,
    token_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    finish_reason text,
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_chat_messages_conv_seq UNIQUE (conversation_id, sequence_number),
    CONSTRAINT ck_chat_messages_seq_positive CHECK (sequence_number >= 1),
    CONSTRAINT ck_chat_messages_content_length CHECK (char_length(content) <= 32768)
);

CREATE INDEX idx_chat_messages_conv_seq 
    ON public.chat_messages (conversation_id, sequence_number ASC);
CREATE INDEX idx_chat_messages_idempotency 
    ON public.chat_messages (conversation_id, idempotency_key) 
    WHERE idempotency_key IS NOT NULL;

CREATE TRIGGER tr_chat_messages_updated_at
BEFORE UPDATE ON public.chat_messages
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- 5. Table Grants
REVOKE ALL ON public.chat_conversations FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.chat_messages FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.chat_conversations TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chat_messages TO authenticated;

GRANT ALL ON public.chat_conversations TO service_role, postgres;
GRANT ALL ON public.chat_messages TO service_role, postgres;

-- 6. Row-Level Security Policies
ALTER TABLE public.chat_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;

-- 6.1 chat_conversations Policies
-- Authenticated users view only their own non-deleted conversations
CREATE POLICY p_chat_conv_select ON public.chat_conversations
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() AND status != 'deleted');

-- Authenticated users insert conversations owned by themselves
CREATE POLICY p_chat_conv_insert ON public.chat_conversations
    FOR INSERT TO authenticated
    WITH CHECK (user_id = auth.uid());

-- Authenticated users update their own conversations
CREATE POLICY p_chat_conv_update ON public.chat_conversations
    FOR UPDATE TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- Authenticated users delete their own conversations
CREATE POLICY p_chat_conv_delete ON public.chat_conversations
    FOR DELETE TO authenticated
    USING (user_id = auth.uid());

-- 6.2 chat_messages Policies
-- Read messages if caller owns the parent conversation and it is not deleted
CREATE POLICY p_chat_msg_select ON public.chat_messages
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_messages.conversation_id
              AND c.user_id = auth.uid()
              AND c.status != 'deleted'
        )
    );

-- Insert messages into active owned conversations
CREATE POLICY p_chat_msg_insert ON public.chat_messages
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_messages.conversation_id
              AND c.user_id = auth.uid()
              AND c.status = 'active'
        )
    );

-- Update messages in owned conversations
CREATE POLICY p_chat_msg_update ON public.chat_messages
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_messages.conversation_id
              AND c.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_messages.conversation_id
              AND c.user_id = auth.uid()
        )
    );

-- Delete messages in owned conversations
CREATE POLICY p_chat_msg_delete ON public.chat_messages
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_messages.conversation_id
              AND c.user_id = auth.uid()
        )
    );
