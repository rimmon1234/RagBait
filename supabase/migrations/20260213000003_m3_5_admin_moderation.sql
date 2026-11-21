-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.5 Admin Knowledge Base Management & Moderation
--
-- 1. Hardens RLS on kb_documents and kb_document_pages:
--    Ensures students cannot view documents or extracted pages under archived
--    or inactive subjects/topics, even if they know the UUID.
-- 2. Adds lineage and manual retry bounding columns to kb_extraction_jobs:
--    - retry_count (max 2 manual retries per lineage)
--    - retry_of_job_id (links to preceding failed job)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Taxonomy-Aware Document & Page RLS Policies
-- ------------------------------------------------------------------------------

DROP POLICY IF EXISTS kb_documents_select ON public.kb_documents;

CREATE POLICY kb_documents_select ON public.kb_documents FOR SELECT
USING (
  (
    app_private.is_approved(auth.uid())
    AND is_archived = false
    AND (
      upload_status = 'uploaded'
      OR (
        uploader_id = auth.uid() 
        AND upload_status IN ('pending_upload', 'upload_failed')
      )
    )
    AND EXISTS (
      SELECT 1 FROM public.kb_topics t
      JOIN public.kb_subjects s ON s.id = t.subject_id
      WHERE t.id = kb_documents.topic_id
        AND t.is_active = true
        AND s.is_active = true
    )
  )
  OR app_private.is_admin(auth.uid())
);

DROP POLICY IF EXISTS kb_document_pages_select ON public.kb_document_pages;

CREATE POLICY kb_document_pages_select ON public.kb_document_pages FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.kb_documents d
    JOIN public.kb_topics t ON t.id = d.topic_id
    JOIN public.kb_subjects s ON s.id = t.subject_id
    WHERE d.id = kb_document_pages.document_id
      AND (
        (
          app_private.is_approved(auth.uid()) 
          AND d.is_archived = false 
          AND t.is_active = true
          AND s.is_active = true
          AND (
            d.upload_status = 'uploaded' 
            OR (
              d.uploader_id = auth.uid() 
              AND d.upload_status IN ('pending_upload', 'upload_failed')
            )
          )
        )
        OR app_private.is_admin(auth.uid())
      )
  )
);

-- ------------------------------------------------------------------------------
-- 2. Lineage & Bounded Retry Tracking for Extraction Jobs
-- ------------------------------------------------------------------------------

ALTER TABLE public.kb_extraction_jobs
  ADD COLUMN IF NOT EXISTS retry_count int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS retry_of_job_id uuid REFERENCES public.kb_extraction_jobs(id) ON DELETE SET NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_kb_extraction_jobs_retry_count'
  ) THEN
    ALTER TABLE public.kb_extraction_jobs
      ADD CONSTRAINT ck_kb_extraction_jobs_retry_count CHECK (retry_count >= 0 AND retry_count <= 2);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kb_extraction_jobs_document_created
  ON public.kb_extraction_jobs(document_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_kb_extraction_jobs_retry_of
  ON public.kb_extraction_jobs(retry_of_job_id)
  WHERE retry_of_job_id IS NOT NULL;
