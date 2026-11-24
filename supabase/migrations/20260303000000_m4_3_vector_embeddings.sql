-- ==============================================================================
-- RagBait Phase 4 — M4.3: Embedding Infrastructure & Vector Storage
-- Migration: 20260303000000_m4_3_vector_embeddings.sql
-- Description:
--   1. Activates pgvector 0.8.2 in extensions schema.
--   2. Adds embedding_status and active_embedding_fingerprint to public.kb_documents.
--   3. Creates public.kb_embedding_jobs background queue table.
--   4. Creates public.kb_chunk_embeddings vector storage table with HNSW indexing.
--   5. Configures strict Row Level Security, indexes, and permission revocations.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Activate Vector Extension in extensions schema
-- ------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

-- ------------------------------------------------------------------------------
-- 2. Extend public.kb_documents with Embedding Lifecycle Fields
-- ------------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_schema = 'public' 
      AND table_name = 'kb_documents' 
      AND column_name = 'embedding_status'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD COLUMN embedding_status text NOT NULL DEFAULT 'unembedded';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_schema = 'public' 
      AND table_name = 'kb_documents' 
      AND column_name = 'active_embedding_fingerprint'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD COLUMN active_embedding_fingerprint text DEFAULT NULL;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_kb_documents_embedding_status'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD CONSTRAINT ck_kb_documents_embedding_status
      CHECK (embedding_status IN ('unembedded', 'embedding', 'embedded', 'failed', 'not_applicable'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kb_documents_embedding_status
  ON public.kb_documents(embedding_status);

-- ------------------------------------------------------------------------------
-- 3. Durable Background Embedding Queue Table
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.kb_embedding_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  chunk_version integer NOT NULL DEFAULT 1,
  model_fingerprint text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  worker_id text,
  lease_token uuid,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  scheduled_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  locked_until timestamptz,
  heartbeat_at timestamptz,
  total_chunks integer NOT NULL DEFAULT 0,
  embedded_chunks integer NOT NULL DEFAULT 0,
  error_code text,
  error_message text,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT ck_kb_embedding_jobs_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
  CONSTRAINT ck_kb_embedding_jobs_attempts CHECK (attempts >= 0 AND attempts <= max_attempts),
  CONSTRAINT ck_kb_embedding_jobs_max_attempts CHECK (max_attempts > 0),
  CONSTRAINT ck_kb_embedding_jobs_chunk_version CHECK (chunk_version >= 1)
);

-- At most one active embedding job per document and model fingerprint
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_active_embedding_job 
  ON public.kb_embedding_jobs(document_id, model_fingerprint)
  WHERE status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS idx_kb_embedding_jobs_poll 
  ON public.kb_embedding_jobs(status, scheduled_at)
  WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_kb_embedding_jobs_locked 
  ON public.kb_embedding_jobs(status, locked_until)
  WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS idx_kb_embedding_jobs_doc_id 
  ON public.kb_embedding_jobs(document_id);

CREATE OR REPLACE TRIGGER tr_kb_embedding_jobs_updated_at
BEFORE UPDATE ON public.kb_embedding_jobs
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 4. Document Chunk Embeddings Table (384-dim Dense Vector Storage)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.kb_chunk_embeddings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id uuid NOT NULL REFERENCES public.kb_document_chunks(id) ON DELETE CASCADE,
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  chunk_version integer NOT NULL DEFAULT 1,
  model_id text NOT NULL DEFAULT 'intfloat/multilingual-e5-small',
  model_fingerprint text NOT NULL,
  dimensions integer NOT NULL DEFAULT 384,
  embedding extensions.vector(384) NOT NULL,
  source_content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_chunk_model_embedding UNIQUE (chunk_id, model_fingerprint),
  CONSTRAINT ck_embedding_dimensions CHECK (dimensions = 384)
);

CREATE INDEX IF NOT EXISTS idx_kb_embeddings_chunk_id 
  ON public.kb_chunk_embeddings(chunk_id);

CREATE INDEX IF NOT EXISTS idx_kb_embeddings_doc_version 
  ON public.kb_chunk_embeddings(document_id, chunk_version);

CREATE INDEX IF NOT EXISTS idx_kb_embeddings_fingerprint 
  ON public.kb_chunk_embeddings(model_fingerprint);

-- HNSW Vector Index for Cosine Distance
CREATE INDEX IF NOT EXISTS idx_kb_embeddings_hnsw 
  ON public.kb_chunk_embeddings 
  USING hnsw (embedding extensions.vector_cosine_ops) 
  WITH (m = 16, ef_construction = 64);

CREATE OR REPLACE TRIGGER tr_kb_chunk_embeddings_updated_at
BEFORE UPDATE ON public.kb_chunk_embeddings
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 5. Row Level Security & Governance Policies
-- ------------------------------------------------------------------------------
ALTER TABLE public.kb_embedding_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_chunk_embeddings ENABLE ROW LEVEL SECURITY;

-- Administrators have full management access
CREATE POLICY kb_embedding_jobs_admin_all ON public.kb_embedding_jobs FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

CREATE POLICY kb_chunk_embeddings_admin_all ON public.kb_chunk_embeddings FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- Strict privilege boundary: No direct student or anonymous access to raw embeddings
REVOKE ALL ON public.kb_embedding_jobs FROM anon, authenticated;
REVOKE ALL ON public.kb_chunk_embeddings FROM anon, authenticated;
