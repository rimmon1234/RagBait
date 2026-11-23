-- ==============================================================================
-- RagBait Phase 4 — M4.2: Page-Aware Chunking & Durable Indexing Lifecycle
-- Migration: 20260302000000_m4_2_document_indexing.sql
-- Description:
--   1. Adds indexing_status and active_chunk_version to public.kb_documents.
--   2. Creates public.kb_indexing_jobs queue table with worker leases and crash recovery.
--   3. Creates public.kb_document_chunks table with physical page provenance and <= 420 token ceiling.
--   4. Configures RLS policies, indexes, and permission grants.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Extend public.kb_documents with Indexing Lifecycle Fields
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_schema = 'public' 
      AND table_name = 'kb_documents' 
      AND column_name = 'indexing_status'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD COLUMN indexing_status text NOT NULL DEFAULT 'unindexed';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_schema = 'public' 
      AND table_name = 'kb_documents' 
      AND column_name = 'active_chunk_version'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD COLUMN active_chunk_version integer DEFAULT NULL;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_kb_documents_indexing_status'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD CONSTRAINT ck_kb_documents_indexing_status
      CHECK (indexing_status IN ('unindexed', 'indexing', 'indexed', 'failed', 'not_indexable'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kb_documents_indexing_status
  ON public.kb_documents(indexing_status);

-- ------------------------------------------------------------------------------
-- 2. Durable Background Indexing Queue Table
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.kb_indexing_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  source_version integer NOT NULL DEFAULT 1,
  chunking_version integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'queued',
  worker_id text,
  lease_token uuid,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  scheduled_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  locked_until timestamptz,
  heartbeat_at timestamptz,
  error_code text,
  error_message text,
  total_chunks integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT ck_kb_indexing_jobs_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
  CONSTRAINT ck_kb_indexing_jobs_attempts CHECK (attempts >= 0 AND attempts <= max_attempts),
  CONSTRAINT ck_kb_indexing_jobs_max_attempts CHECK (max_attempts > 0),
  CONSTRAINT ck_kb_indexing_jobs_versions CHECK (source_version >= 1 AND chunking_version >= 1)
);

-- At most one active indexing job per document
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_active_indexing_job 
  ON public.kb_indexing_jobs(document_id)
  WHERE status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS idx_kb_indexing_jobs_poll 
  ON public.kb_indexing_jobs(status, scheduled_at)
  WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_kb_indexing_jobs_locked 
  ON public.kb_indexing_jobs(status, locked_until)
  WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS idx_kb_indexing_jobs_doc_id 
  ON public.kb_indexing_jobs(document_id);

CREATE OR REPLACE TRIGGER tr_kb_indexing_jobs_updated_at
BEFORE UPDATE ON public.kb_indexing_jobs
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 3. Document Text Chunks Table (Page-Aware Provenance & Token Ceiling)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.kb_document_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  page_number_start integer NOT NULL,
  page_number_end integer NOT NULL,
  raw_text text NOT NULL,
  character_count integer NOT NULL,
  token_count integer NOT NULL,
  content_hash text NOT NULL,
  chunking_version integer NOT NULL DEFAULT 1,
  source_version integer NOT NULL DEFAULT 1,
  indexing_job_id uuid REFERENCES public.kb_indexing_jobs(id) ON DELETE SET NULL,
  is_active boolean NOT NULL DEFAULT false,
  page_spans jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_document_chunks_index UNIQUE (document_id, chunking_version, chunk_index),
  CONSTRAINT ck_kb_chunks_page_numbers CHECK (page_number_start >= 1 AND page_number_end >= page_number_start),
  CONSTRAINT ck_kb_chunks_char_count CHECK (character_count > 0 AND character_count = length(raw_text)),
  CONSTRAINT chk_chunk_token_ceiling CHECK (token_count > 0 AND token_count <= 420),
  CONSTRAINT ck_kb_chunks_versions CHECK (source_version >= 1 AND chunking_version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc_active 
  ON public.kb_document_chunks(document_id, is_active);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_pages 
  ON public.kb_document_chunks(document_id, page_number_start, page_number_end);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_content_hash 
  ON public.kb_document_chunks(content_hash);

-- Native Full-Text Search GIN Index for Hybrid Search
CREATE INDEX IF NOT EXISTS idx_kb_chunks_fts 
  ON public.kb_document_chunks USING gin (to_tsvector('english', raw_text));

-- ------------------------------------------------------------------------------
-- 4. Row Level Security & Governance Policies
-- ------------------------------------------------------------------------------
ALTER TABLE public.kb_indexing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_document_chunks ENABLE ROW LEVEL SECURITY;

-- 4.1 kb_indexing_jobs Policies
-- Only administrators or service role may access indexing jobs
CREATE POLICY kb_indexing_jobs_admin_all ON public.kb_indexing_jobs FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 4.2 kb_document_chunks Policies
-- Approved students view active chunks belonging to accessible, published, non-archived materials
CREATE POLICY kb_document_chunks_select ON public.kb_document_chunks FOR SELECT
USING (
  (
    is_active = true
    AND app_private.is_approved(auth.uid())
    AND EXISTS (
      SELECT 1 FROM public.kb_documents d
      JOIN public.kb_topics t ON t.id = d.topic_id
      JOIN public.kb_subjects s ON s.id = d.subject_id
      WHERE d.id = kb_document_chunks.document_id
        AND d.is_archived = false
        AND d.upload_status = 'uploaded'
        AND t.is_active = true
        AND s.is_active = true
    )
  )
  OR app_private.is_admin(auth.uid())
);

-- Admin only for direct SQL mutations
CREATE POLICY kb_document_chunks_admin_all ON public.kb_document_chunks FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 4.3 Grants & Permissions
REVOKE ALL ON public.kb_indexing_jobs FROM anon, authenticated;
REVOKE ALL ON public.kb_document_chunks FROM anon, authenticated;
GRANT SELECT ON public.kb_document_chunks TO authenticated;
