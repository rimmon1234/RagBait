-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.5: Evidence Snapshots & Conversation Summaries
-- Migration: 20260305000000_m4_5_chat_evidence.sql
-- Description:
--   1. Adds unique partial idempotency index on public.chat_messages.
--   2. Creates public.chat_message_sources with 4-stage provenance flags,
--      immutability protection, citation key constraints, and strict RLS.
--   3. Creates public.chat_conversation_summaries with optimistic concurrency
--      compaction tracking, durable status scheduling, and strict RLS.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Idempotency Hardening on public.chat_messages
-- ------------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_messages_conv_role_idem
    ON public.chat_messages (conversation_id, role, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ------------------------------------------------------------------------------
-- 2. Immutable Evidence Snapshots Table: public.chat_message_sources
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_message_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL REFERENCES public.chat_messages(id) ON DELETE CASCADE,
    citation_key text NOT NULL,
    -- Nullable FKs preserve historical snapshots if source chunks/docs are deleted or re-indexed;
    -- source_chunk_id / source_document_id retain the immutable original UUIDs for audit lineage.
    chunk_id uuid REFERENCES public.kb_document_chunks(id) ON DELETE SET NULL,
    document_id uuid REFERENCES public.kb_documents(id) ON DELETE SET NULL,
    source_chunk_id uuid NOT NULL,
    source_document_id uuid NOT NULL,
    document_title_snapshot text NOT NULL,
    page_number_start integer NOT NULL,
    page_number_end integer NOT NULL,
    page_spans jsonb NOT NULL DEFAULT '[]'::jsonb,
    excerpt text NOT NULL,
    content_hash text NOT NULL,
    chunk_version integer NOT NULL DEFAULT 1,
    retrieval_rank integer NOT NULL,
    relevance_score double precision NOT NULL,
    included_in_context boolean NOT NULL DEFAULT true,
    referenced_in_answer boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_chat_msg_sources_key UNIQUE (message_id, citation_key),
    CONSTRAINT ck_chat_msg_sources_citation_key CHECK (citation_key ~ '^S[1-9][0-9]*$'),
    CONSTRAINT ck_chat_msg_sources_pages CHECK (
        page_number_start >= 1 AND page_number_end >= page_number_start
    ),
    CONSTRAINT ck_chat_msg_sources_rank CHECK (retrieval_rank >= 1),
    CONSTRAINT ck_chat_msg_sources_version CHECK (chunk_version >= 1),
    CONSTRAINT ck_chat_msg_sources_title_nonempty CHECK (
        char_length(trim(document_title_snapshot)) > 0
    ),
    CONSTRAINT ck_chat_msg_sources_excerpt_nonempty CHECK (char_length(excerpt) > 0),
    CONSTRAINT ck_chat_msg_sources_hash_nonempty CHECK (
        char_length(trim(content_hash)) > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_chat_msg_sources_msg_id
    ON public.chat_message_sources (message_id);

CREATE INDEX IF NOT EXISTS idx_chat_msg_sources_doc_id
    ON public.chat_message_sources (document_id)
    WHERE document_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_msg_sources_msg_ref
    ON public.chat_message_sources (message_id, referenced_in_answer);

DROP TRIGGER IF EXISTS tr_chat_message_sources_updated_at ON public.chat_message_sources;
CREATE TRIGGER tr_chat_message_sources_updated_at
BEFORE UPDATE ON public.chat_message_sources
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Immutability guard: once inserted, snapshot identity and content cannot be modified.
-- Only referenced_in_answer, updated_at, and FK nullification (ON DELETE SET NULL) are permitted.
CREATE OR REPLACE FUNCTION public.enforce_chat_message_source_immutability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.message_id IS DISTINCT FROM OLD.message_id
       OR NEW.citation_key IS DISTINCT FROM OLD.citation_key
       OR NEW.source_chunk_id IS DISTINCT FROM OLD.source_chunk_id
       OR NEW.source_document_id IS DISTINCT FROM OLD.source_document_id
       OR NEW.document_title_snapshot IS DISTINCT FROM OLD.document_title_snapshot
       OR NEW.page_number_start IS DISTINCT FROM OLD.page_number_start
       OR NEW.page_number_end IS DISTINCT FROM OLD.page_number_end
       OR NEW.page_spans IS DISTINCT FROM OLD.page_spans
       OR NEW.excerpt IS DISTINCT FROM OLD.excerpt
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.chunk_version IS DISTINCT FROM OLD.chunk_version
       OR NEW.retrieval_rank IS DISTINCT FROM OLD.retrieval_rank
       OR NEW.relevance_score IS DISTINCT FROM OLD.relevance_score
       OR NEW.included_in_context IS DISTINCT FROM OLD.included_in_context
    THEN
        RAISE EXCEPTION 'Immutable fields on public.chat_message_sources cannot be modified'
            USING ERRCODE = '22000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tr_chat_message_sources_immutable ON public.chat_message_sources;
CREATE TRIGGER tr_chat_message_sources_immutable
BEFORE UPDATE ON public.chat_message_sources
FOR EACH ROW EXECUTE FUNCTION public.enforce_chat_message_source_immutability();

-- ------------------------------------------------------------------------------
-- 3. Conversation Summaries Table: public.chat_conversation_summaries
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_conversation_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL UNIQUE REFERENCES public.chat_conversations(id) ON DELETE CASCADE,
    summary_text text NOT NULL DEFAULT '',
    compacted_through_sequence integer NOT NULL DEFAULT 0,
    summary_version integer NOT NULL DEFAULT 0,
    token_count integer NOT NULL DEFAULT 0,
    compaction_status text NOT NULL DEFAULT 'idle',
    pending_target_sequence integer,
    retry_count integer NOT NULL DEFAULT 0,
    model_id text,
    provider_id text,
    last_error text,
    last_compacted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT ck_chat_summary_seq_nonneg CHECK (compacted_through_sequence >= 0),
    CONSTRAINT ck_chat_summary_version_nonneg CHECK (summary_version >= 0),
    CONSTRAINT ck_chat_summary_tokens_nonneg CHECK (token_count >= 0),
    CONSTRAINT ck_chat_summary_status CHECK (
        compaction_status IN ('idle', 'pending', 'running', 'completed', 'failed', 'deferred_local')
    ),
    CONSTRAINT ck_chat_summary_pending_nonneg CHECK (
        pending_target_sequence IS NULL OR pending_target_sequence >= 0
    ),
    CONSTRAINT ck_chat_summary_retry_nonneg CHECK (retry_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_chat_conv_summaries_conv_id
    ON public.chat_conversation_summaries (conversation_id);

CREATE INDEX IF NOT EXISTS idx_chat_conv_summaries_pending
    ON public.chat_conversation_summaries (compaction_status, updated_at)
    WHERE compaction_status IN ('pending', 'failed');

DROP TRIGGER IF EXISTS tr_chat_conversation_summaries_updated_at ON public.chat_conversation_summaries;
CREATE TRIGGER tr_chat_conversation_summaries_updated_at
BEFORE UPDATE ON public.chat_conversation_summaries
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 4. Table Grants & Strict Row-Level Security (RLS)
-- ------------------------------------------------------------------------------
-- Direct writes MUST go through backend service_role privileges; students can never
-- forge source snapshots, alter excerpts, or advance compaction watermarks.
REVOKE ALL ON public.chat_message_sources FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.chat_conversation_summaries FROM PUBLIC, anon, authenticated;

GRANT SELECT ON public.chat_message_sources TO authenticated;
GRANT SELECT ON public.chat_conversation_summaries TO authenticated;

GRANT ALL ON public.chat_message_sources TO service_role, postgres;
GRANT ALL ON public.chat_conversation_summaries TO service_role, postgres;

ALTER TABLE public.chat_message_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_conversation_summaries ENABLE ROW LEVEL SECURITY;

-- 4.1 chat_message_sources SELECT RLS:
-- User must own the parent conversation AND live catalog visibility must hold.
-- Prevents direct PostgREST queries from exfiltrating excerpts of archived/moderated docs.
DROP POLICY IF EXISTS p_chat_msg_sources_select ON public.chat_message_sources;
CREATE POLICY p_chat_msg_sources_select ON public.chat_message_sources
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.chat_messages m
            JOIN public.chat_conversations c ON c.id = m.conversation_id
            JOIN public.kb_documents d ON d.id = chat_message_sources.document_id
            JOIN public.kb_topics t ON t.id = d.topic_id
            JOIN public.kb_subjects s ON s.id = d.subject_id
            WHERE m.id = chat_message_sources.message_id
              AND c.user_id = auth.uid()
              AND c.status != 'deleted'
              AND d.is_archived = false
              AND d.upload_status = 'uploaded'
              AND d.extraction_status IN ('completed', 'completed_no_text')
              AND t.is_active = true
              AND s.is_active = true
        )
    );

-- 4.2 chat_conversation_summaries SELECT RLS:
-- User can only read summaries of their own non-deleted conversations.
DROP POLICY IF EXISTS p_chat_conv_summaries_select ON public.chat_conversation_summaries;
CREATE POLICY p_chat_conv_summaries_select ON public.chat_conversation_summaries
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.chat_conversations c
            WHERE c.id = chat_conversation_summaries.conversation_id
              AND c.user_id = auth.uid()
              AND c.status != 'deleted'
        )
    );
