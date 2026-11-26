-- ==============================================================================
-- RagBait Phase 4 — M4.4: Hybrid Retrieval & Subject Scoping
-- Migration: 20260304000000_m4_4_hybrid_retrieval.sql
-- Description:
--   1. Adds GIN index for multilingual full-text search ('simple' config).
--   2. Implements secure SECURITY DEFINER function public.match_subject_chunks().
--   3. Configures strict search_path, execution permissions, and visibility rules.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Multilingual Lexical GIN Index (Simple Configuration)
-- ------------------------------------------------------------------------------
-- The 'simple' configuration preserves exact Unicode tokens for Bengali, Hindi,
-- technical acronyms, and formulas without English-specific stemming distortions.
CREATE INDEX IF NOT EXISTS idx_kb_chunks_fts_simple 
  ON public.kb_document_chunks 
  USING gin (to_tsvector('simple'::regconfig, raw_text));

-- ------------------------------------------------------------------------------
-- 2. Hybrid Retrieval Function with Reciprocal Rank Fusion (RRF)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.match_subject_chunks(
  p_subject_id uuid,
  p_query_embedding extensions.vector,
  p_query_text text,
  p_model_fingerprint text DEFAULT 'e5_small_int8_761b726d_f80102d3',
  p_match_count integer DEFAULT 5,
  p_candidate_limit integer DEFAULT 30,
  p_rrf_k integer DEFAULT 60,
  p_vector_weight double precision DEFAULT 0.60,
  p_fts_weight double precision DEFAULT 0.40
)
RETURNS TABLE (
  chunk_id uuid,
  document_id uuid,
  subject_id uuid,
  topic_id uuid,
  document_title text,
  raw_text text,
  page_number_start integer,
  page_number_end integer,
  page_spans jsonb,
  content_hash text,
  chunk_version integer,
  model_fingerprint text,
  vector_rank integer,
  fts_rank integer,
  vector_distance double precision,
  fts_score double precision,
  rrf_score double precision,
  final_rank integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
  v_q_en tsquery;
  v_q_simple tsquery;
  v_has_lexemes boolean;
  v_limit integer;
  v_cand_limit integer;
  v_rrf_k integer;
BEGIN
  -- Strict Parameter Bounds & Sanitization
  IF p_subject_id IS NULL OR p_query_embedding IS NULL THEN
    RETURN;
  END IF;

  -- Verify active subject existence
  IF NOT EXISTS (
    SELECT 1 FROM public.kb_subjects 
    WHERE id = p_subject_id AND is_active = true
  ) THEN
    RETURN;
  END IF;

  v_limit := GREATEST(1, LEAST(COALESCE(p_match_count, 5), 50));
  v_cand_limit := GREATEST(v_limit, LEAST(COALESCE(p_candidate_limit, 30), 100));
  v_rrf_k := GREATEST(1, COALESCE(p_rrf_k, 60));

  -- Parse full-text search queries safely
  IF p_query_text IS NOT NULL AND trim(p_query_text) != '' THEN
    v_q_en := plainto_tsquery('english'::regconfig, p_query_text);
    v_q_simple := plainto_tsquery('simple'::regconfig, p_query_text);
    v_has_lexemes := (numnode(v_q_en) > 0 OR numnode(v_q_simple) > 0);
  ELSE
    v_has_lexemes := false;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      c.id AS vc_chunk_id,
      c.document_id AS vc_document_id,
      d.subject_id AS vc_subject_id,
      d.topic_id AS vc_topic_id,
      d.title AS vc_document_title,
      c.raw_text AS vc_raw_text,
      c.page_number_start AS vc_page_number_start,
      c.page_number_end AS vc_page_number_end,
      c.page_spans AS vc_page_spans,
      c.content_hash AS vc_content_hash,
      c.chunking_version AS vc_chunk_version,
      e.model_fingerprint AS vc_model_fingerprint,
      (e.embedding <=> p_query_embedding)::double precision AS vc_vector_distance,
      ROW_NUMBER() OVER (ORDER BY e.embedding <=> p_query_embedding ASC, c.id ASC)::integer AS vc_vector_rank
    FROM public.kb_chunk_embeddings e
    JOIN public.kb_document_chunks c ON c.id = e.chunk_id
    JOIN public.kb_documents d ON d.id = c.document_id
    JOIN public.kb_subjects s ON s.id = d.subject_id
    LEFT JOIN public.kb_topics t ON t.id = d.topic_id
    WHERE d.subject_id = p_subject_id
      AND s.is_active = true
      AND (d.topic_id IS NULL OR t.is_active = true)
      AND d.is_archived = false
      AND d.upload_status = 'uploaded'
      AND d.extraction_status = 'completed'
      AND d.indexing_status = 'indexed'
      AND d.embedding_status = 'embedded'
      AND d.active_embedding_fingerprint = p_model_fingerprint
      AND c.chunking_version = d.active_chunk_version
      AND c.is_active = true
      AND e.chunk_version = d.active_chunk_version
      AND e.model_fingerprint = p_model_fingerprint
      AND e.source_content_hash = c.content_hash
    ORDER BY vc_vector_distance ASC, c.id ASC
    LIMIT v_cand_limit
  ),
  fts_candidates AS (
    SELECT
      c.id AS fc_chunk_id,
      c.document_id AS fc_document_id,
      d.subject_id AS fc_subject_id,
      d.topic_id AS fc_topic_id,
      d.title AS fc_document_title,
      c.raw_text AS fc_raw_text,
      c.page_number_start AS fc_page_number_start,
      c.page_number_end AS fc_page_number_end,
      c.page_spans AS fc_page_spans,
      c.content_hash AS fc_content_hash,
      c.chunking_version AS fc_chunk_version,
      e.model_fingerprint AS fc_model_fingerprint,
      (
        COALESCE(ts_rank(to_tsvector('english'::regconfig, c.raw_text), v_q_en), 0.0) +
        COALESCE(ts_rank(to_tsvector('simple'::regconfig, c.raw_text), v_q_simple), 0.0)
      )::double precision AS fc_fts_score,
      ROW_NUMBER() OVER (
        ORDER BY (
          COALESCE(ts_rank(to_tsvector('english'::regconfig, c.raw_text), v_q_en), 0.0) +
          COALESCE(ts_rank(to_tsvector('simple'::regconfig, c.raw_text), v_q_simple), 0.0)
        ) DESC,
        c.id ASC
      )::integer AS fc_fts_rank
    FROM public.kb_document_chunks c
    JOIN public.kb_documents d ON d.id = c.document_id
    JOIN public.kb_chunk_embeddings e ON e.chunk_id = c.id
    JOIN public.kb_subjects s ON s.id = d.subject_id
    LEFT JOIN public.kb_topics t ON t.id = d.topic_id
    WHERE d.subject_id = p_subject_id
      AND s.is_active = true
      AND (d.topic_id IS NULL OR t.is_active = true)
      AND d.is_archived = false
      AND d.upload_status = 'uploaded'
      AND d.extraction_status = 'completed'
      AND d.indexing_status = 'indexed'
      AND d.embedding_status = 'embedded'
      AND d.active_embedding_fingerprint = p_model_fingerprint
      AND c.chunking_version = d.active_chunk_version
      AND c.is_active = true
      AND e.chunk_version = d.active_chunk_version
      AND e.model_fingerprint = p_model_fingerprint
      AND e.source_content_hash = c.content_hash
      AND v_has_lexemes
      AND (
        (numnode(v_q_en) > 0 AND to_tsvector('english'::regconfig, c.raw_text) @@ v_q_en)
        OR
        (numnode(v_q_simple) > 0 AND to_tsvector('simple'::regconfig, c.raw_text) @@ v_q_simple)
      )
    ORDER BY fc_fts_score DESC, c.id ASC
    LIMIT v_cand_limit
  ),
  combined AS (
    SELECT
      COALESCE(v.vc_chunk_id, f.fc_chunk_id) AS comb_chunk_id,
      COALESCE(v.vc_document_id, f.fc_document_id) AS comb_document_id,
      COALESCE(v.vc_subject_id, f.fc_subject_id) AS comb_subject_id,
      COALESCE(v.vc_topic_id, f.fc_topic_id) AS comb_topic_id,
      COALESCE(v.vc_document_title, f.fc_document_title) AS comb_document_title,
      COALESCE(v.vc_raw_text, f.fc_raw_text) AS comb_raw_text,
      COALESCE(v.vc_page_number_start, f.fc_page_number_start) AS comb_page_number_start,
      COALESCE(v.vc_page_number_end, f.fc_page_number_end) AS comb_page_number_end,
      COALESCE(v.vc_page_spans, f.fc_page_spans) AS comb_page_spans,
      COALESCE(v.vc_content_hash, f.fc_content_hash) AS comb_content_hash,
      COALESCE(v.vc_chunk_version, f.fc_chunk_version) AS comb_chunk_version,
      COALESCE(v.vc_model_fingerprint, f.fc_model_fingerprint) AS comb_model_fingerprint,
      v.vc_vector_rank AS comb_vector_rank,
      f.fc_fts_rank AS comb_fts_rank,
      v.vc_vector_distance AS comb_vector_distance,
      f.fc_fts_score AS comb_fts_score,
      (
        COALESCE(p_vector_weight / (v_rrf_k + v.vc_vector_rank), 0.0::double precision) +
        COALESCE(p_fts_weight / (v_rrf_k + f.fc_fts_rank), 0.0::double precision)
      )::double precision AS comb_rrf_score
    FROM vector_candidates v
    FULL OUTER JOIN fts_candidates f ON f.fc_chunk_id = v.vc_chunk_id
  )
  SELECT
    c.comb_chunk_id AS chunk_id,
    c.comb_document_id AS document_id,
    c.comb_subject_id AS subject_id,
    c.comb_topic_id AS topic_id,
    c.comb_document_title AS document_title,
    c.comb_raw_text AS raw_text,
    c.comb_page_number_start AS page_number_start,
    c.comb_page_number_end AS page_number_end,
    c.comb_page_spans AS page_spans,
    c.comb_content_hash AS content_hash,
    c.comb_chunk_version AS chunk_version,
    c.comb_model_fingerprint AS model_fingerprint,
    c.comb_vector_rank AS vector_rank,
    c.comb_fts_rank AS fts_rank,
    c.comb_vector_distance AS vector_distance,
    c.comb_fts_score AS fts_score,
    c.comb_rrf_score AS rrf_score,
    ROW_NUMBER() OVER (
      ORDER BY
        c.comb_rrf_score DESC,
        COALESCE(c.comb_vector_rank, 999999) ASC,
        COALESCE(c.comb_fts_rank, 999999) ASC,
        c.comb_chunk_id ASC
    )::integer AS final_rank
  FROM combined c
  ORDER BY
    c.comb_rrf_score DESC,
    COALESCE(c.comb_vector_rank, 999999) ASC,
    COALESCE(c.comb_fts_rank, 999999) ASC,
    c.comb_chunk_id ASC
  LIMIT v_limit;
END;
$$;

-- ------------------------------------------------------------------------------
-- 3. Execution Grants & Row-Level Security Fencing
-- ------------------------------------------------------------------------------
-- Direct function execution is strictly revoked from PUBLIC, anonymous and student roles.
REVOKE ALL ON FUNCTION public.match_subject_chunks(
  uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision
) FROM PUBLIC, anon, authenticated;

-- Trusted backend service and administrator connections execute the function.
GRANT EXECUTE ON FUNCTION public.match_subject_chunks(
  uuid, extensions.vector, text, text, integer, integer, integer, double precision, double precision
) TO service_role, postgres;
