-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.9: Web Source Snapshots & Adaptive Answer Policy
-- Migration: 20260309000000_m4_9_web_sources_and_policy.sql
-- Description:
--   1. Creates public.chat_message_web_sources for immutable external web
--      search evidence snapshots cited via [W1], [W2], ...
--   2. Enforces immutability trigger guarding URL, domain, title, excerpt,
--      content_hash, snippet_only, and retrieval metadata while permitting
--      controlled monotonic referenced_in_answer (false -> true) updates.
--   3. Adds SECURITY DEFINER function mark_chat_message_web_sources_referenced
--      and revokes direct client table access (strict RLS / service_role only).
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.chat_message_web_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL REFERENCES public.chat_messages(id) ON DELETE CASCADE,
    citation_key text NOT NULL,
    title text NOT NULL,
    url text NOT NULL,
    domain text NOT NULL,
    publisher text,
    published_at text,
    retrieved_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    excerpt text NOT NULL,
    content_hash text NOT NULL,
    snippet_only boolean NOT NULL DEFAULT true,
    retrieval_rank integer NOT NULL DEFAULT 1,
    relevance_score double precision NOT NULL DEFAULT 0.0,
    included_in_context boolean NOT NULL DEFAULT true,
    referenced_in_answer boolean NOT NULL DEFAULT false,
    availability_status text NOT NULL DEFAULT 'available',
    created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_chat_msg_web_sources_key UNIQUE (message_id, citation_key),
    CONSTRAINT ck_chat_msg_web_sources_key CHECK (citation_key ~ '^W[1-9][0-9]*$'),
    CONSTRAINT ck_chat_msg_web_sources_title_nonempty CHECK (char_length(trim(title)) > 0),
    CONSTRAINT ck_chat_msg_web_sources_url_http CHECK (url ~* '^https?://'),
    CONSTRAINT ck_chat_msg_web_sources_domain_nonempty CHECK (char_length(trim(domain)) > 0),
    CONSTRAINT ck_chat_msg_web_sources_excerpt_nonempty CHECK (char_length(excerpt) > 0),
    CONSTRAINT ck_chat_msg_web_sources_hash_nonempty CHECK (char_length(trim(content_hash)) > 0),
    CONSTRAINT ck_chat_msg_web_sources_rank CHECK (retrieval_rank >= 1),
    CONSTRAINT ck_chat_msg_web_sources_provenance CHECK (
        NOT (referenced_in_answer = true AND included_in_context = false)
    ),
    CONSTRAINT ck_chat_msg_web_sources_avail CHECK (
        availability_status IN ('available', 'unavailable', 'revoked')
    )
);

CREATE INDEX IF NOT EXISTS idx_chat_msg_web_sources_msg_id
    ON public.chat_message_web_sources (message_id);

CREATE INDEX IF NOT EXISTS idx_chat_msg_web_sources_msg_ref
    ON public.chat_message_web_sources (message_id, referenced_in_answer);

DROP TRIGGER IF EXISTS tr_chat_message_web_sources_updated_at ON public.chat_message_web_sources;
CREATE TRIGGER tr_chat_message_web_sources_updated_at
BEFORE UPDATE ON public.chat_message_web_sources
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE OR REPLACE FUNCTION public.enforce_chat_message_web_source_immutability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.message_id IS DISTINCT FROM OLD.message_id
       OR NEW.citation_key IS DISTINCT FROM OLD.citation_key
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.url IS DISTINCT FROM OLD.url
       OR NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.publisher IS DISTINCT FROM OLD.publisher
       OR NEW.published_at IS DISTINCT FROM OLD.published_at
       OR NEW.retrieved_at IS DISTINCT FROM OLD.retrieved_at
       OR NEW.excerpt IS DISTINCT FROM OLD.excerpt
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.snippet_only IS DISTINCT FROM OLD.snippet_only
       OR NEW.retrieval_rank IS DISTINCT FROM OLD.retrieval_rank
       OR NEW.relevance_score IS DISTINCT FROM OLD.relevance_score
       OR NEW.included_in_context IS DISTINCT FROM OLD.included_in_context
    THEN
        RAISE EXCEPTION 'Immutable fields on public.chat_message_web_sources cannot be modified'
            USING ERRCODE = '22000';
    END IF;

    IF NEW.referenced_in_answer IS TRUE AND NEW.included_in_context IS NOT TRUE THEN
        RAISE EXCEPTION 'Invalid web provenance transition: source not included in context'
            USING ERRCODE = '22000';
    END IF;

    IF OLD.referenced_in_answer IS TRUE AND NEW.referenced_in_answer IS NOT TRUE THEN
        RAISE EXCEPTION 'Invalid web provenance transition: referenced_in_answer cannot revert from true to false'
            USING ERRCODE = '22000';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tr_chat_message_web_sources_immutable ON public.chat_message_web_sources;
CREATE TRIGGER tr_chat_message_web_sources_immutable
BEFORE UPDATE ON public.chat_message_web_sources
FOR EACH ROW EXECUTE FUNCTION public.enforce_chat_message_web_source_immutability();

CREATE OR REPLACE FUNCTION public.mark_chat_message_web_sources_referenced(
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
        FROM public.chat_message_web_sources w
        WHERE w.message_id = p_message_id
          AND w.citation_key = k
          AND w.included_in_context = true
    );

    IF v_invalid_keys IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid web provenance transition for excluded or unknown keys: %', v_invalid_keys
            USING ERRCODE = '22000';
    END IF;

    UPDATE public.chat_message_web_sources
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

REVOKE ALL ON public.chat_message_web_sources FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.chat_message_web_sources TO authenticated;
GRANT ALL ON public.chat_message_web_sources TO service_role, postgres;
ALTER TABLE public.chat_message_web_sources ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_chat_msg_web_sources_select ON public.chat_message_web_sources;
CREATE POLICY p_chat_msg_web_sources_select ON public.chat_message_web_sources
    FOR SELECT TO authenticated
    USING (
        availability_status = 'available'
        AND EXISTS (
            SELECT 1
            FROM public.chat_messages m
            JOIN public.chat_conversations c ON c.id = m.conversation_id
            WHERE m.id = chat_message_web_sources.message_id
              AND c.user_id = auth.uid()
              AND c.status != 'deleted'
        )
    );

REVOKE ALL ON FUNCTION public.mark_chat_message_web_sources_referenced(uuid, text[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_chat_message_web_sources_referenced(uuid, text[]) TO service_role, postgres;
