-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.5 Post-Implementation Hardening
-- Migration: 20260306000000_m4_5_hardening.sql
-- Description:
--   1. Audit A1: Strengthen public.chat_message_sources constraints, lineage checks,
--      provenance state transition trigger guards, and narrow transition function.
--   2. Audit A2: Add durable worker lease/claim/backoff columns and recovery index
--      on public.chat_conversation_summaries.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Audit A1 — Source Snapshot Immutability & Provenance Transition Hardening
-- ------------------------------------------------------------------------------
ALTER TABLE public.chat_message_sources
    DROP CONSTRAINT IF EXISTS ck_chat_msg_sources_provenance_order,
    DROP CONSTRAINT IF EXISTS ck_chat_msg_sources_doc_lineage,
    DROP CONSTRAINT IF EXISTS ck_chat_msg_sources_chunk_lineage;

ALTER TABLE public.chat_message_sources
    ADD CONSTRAINT ck_chat_msg_sources_provenance_order CHECK (
        NOT (referenced_in_answer = true AND included_in_context = false)
    ),
    ADD CONSTRAINT ck_chat_msg_sources_doc_lineage CHECK (
        document_id IS NULL OR document_id = source_document_id
    ),
    ADD CONSTRAINT ck_chat_msg_sources_chunk_lineage CHECK (
        chunk_id IS NULL OR chunk_id = source_chunk_id
    );

-- Strengthen immutability trigger:
--   - Blocks mutation of id, created_at, identity, excerpt, content_hash, version, rank,
--     and included_in_context.
--   - Blocks arbitrary reassignment of document_id / chunk_id to any non-null value.
--   - Blocks arbitrary nullification of document_id / chunk_id while parent still exists
--     (permits genuine FK ON DELETE SET NULL cascades when parent row was deleted).
--   - Enforces valid provenance transition: included_in_context = true AND monotonic
--     referenced_in_answer false -> true only.
CREATE OR REPLACE FUNCTION public.enforce_chat_message_source_immutability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.message_id IS DISTINCT FROM OLD.message_id
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

    -- Prevent reassigning or manually stripping live FK references except via real cascade delete
    IF NEW.document_id IS DISTINCT FROM OLD.document_id THEN
        IF NEW.document_id IS NOT NULL THEN
            RAISE EXCEPTION 'Immutable document_id on public.chat_message_sources cannot be reassigned'
                USING ERRCODE = '22000';
        END IF;
        IF OLD.document_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.kb_documents d WHERE d.id = OLD.document_id
        ) THEN
            RAISE EXCEPTION 'Cannot manually clear document_id while source document exists'
                USING ERRCODE = '22000';
        END IF;
    END IF;

    IF NEW.chunk_id IS DISTINCT FROM OLD.chunk_id THEN
        IF NEW.chunk_id IS NOT NULL THEN
            RAISE EXCEPTION 'Immutable chunk_id on public.chat_message_sources cannot be reassigned'
                USING ERRCODE = '22000';
        END IF;
        IF OLD.chunk_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.kb_document_chunks c WHERE c.id = OLD.chunk_id
        ) THEN
            RAISE EXCEPTION 'Cannot manually clear chunk_id while source chunk exists'
                USING ERRCODE = '22000';
        END IF;
    END IF;

    -- Controlled provenance transition: included_in_context -> referenced_in_answer only
    IF NEW.referenced_in_answer IS TRUE AND NEW.included_in_context IS NOT TRUE THEN
        RAISE EXCEPTION 'Invalid provenance transition: source not included in context'
            USING ERRCODE = '22000';
    END IF;

    IF OLD.referenced_in_answer IS TRUE AND NEW.referenced_in_answer IS NOT TRUE THEN
        RAISE EXCEPTION 'Invalid provenance transition: referenced_in_answer cannot revert from true to false'
            USING ERRCODE = '22000';
    END IF;

    RETURN NEW;
END;
$$;

-- Narrow controlled backend provenance transition function
CREATE OR REPLACE FUNCTION public.mark_chat_message_sources_referenced(
    p_message_id uuid,
    p_citation_keys text[]
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_updated integer := 0;
    v_invalid_keys text[];
BEGIN
    IF p_citation_keys IS NULL OR array_length(p_citation_keys, 1) IS NULL THEN
        RETURN 0;
    END IF;

    SELECT array_agg(k)
    INTO v_invalid_keys
    FROM unnest(p_citation_keys) AS k
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.chat_message_sources s
        WHERE s.message_id = p_message_id
          AND s.citation_key = k
          AND s.included_in_context = true
    );

    IF v_invalid_keys IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid provenance transition for excluded or unknown keys: %', v_invalid_keys
            USING ERRCODE = '22000';
    END IF;

    UPDATE public.chat_message_sources
    SET referenced_in_answer = true,
        updated_at = timezone('utc'::text, now())
    WHERE message_id = p_message_id
      AND included_in_context = true
      AND citation_key = ANY(p_citation_keys)
      AND referenced_in_answer = false;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;

REVOKE ALL ON FUNCTION public.mark_chat_message_sources_referenced(uuid, text[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_chat_message_sources_referenced(uuid, text[]) TO service_role, postgres;

-- ------------------------------------------------------------------------------
-- 2. Audit A2 — Durable Summary Compaction Leases & Crash Recovery
-- ------------------------------------------------------------------------------
ALTER TABLE public.chat_conversation_summaries
    ADD COLUMN IF NOT EXISTS worker_id text,
    ADD COLUMN IF NOT EXISTS lease_token uuid,
    ADD COLUMN IF NOT EXISTS locked_until timestamptz,
    ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;

-- Repair any pre-existing dangling running rows safely before adding lease check
UPDATE public.chat_conversation_summaries
SET compaction_status = 'pending'
WHERE compaction_status = 'running'
  AND (lease_token IS NULL OR locked_until IS NULL);

ALTER TABLE public.chat_conversation_summaries
    DROP CONSTRAINT IF EXISTS ck_chat_summary_lock_consistency;

ALTER TABLE public.chat_conversation_summaries
    ADD CONSTRAINT ck_chat_summary_lock_consistency CHECK (
        compaction_status != 'running' OR (lease_token IS NOT NULL AND locked_until IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS idx_chat_conv_summaries_recoverable
    ON public.chat_conversation_summaries (compaction_status, locked_until, next_retry_at, updated_at)
    WHERE compaction_status IN ('pending', 'running', 'failed', 'completed');
