-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.1 Security Hardening Remediation
--
-- Corrections:
--   1. Revoke direct SELECT on public.kb_extraction_jobs from authenticated.
--      Job status and execution metadata must be mediated through trusted
--      FastAPI endpoints to avoid exposing lease tokens, worker IDs, and raw errors.
--   2. Restrict owner-specific document visibility in kb_documents_select
--      strictly to ('pending_upload', 'upload_failed').
--      Prevents students from observing documents in 'deleting' or 'deleted' states.
--   3. Synchronize kb_document_pages_select with the hardened document visibility.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Extraction Job Privilege Hardening
-- ------------------------------------------------------------------------------
-- Revoke all table-level privileges from authenticated, anon, and PUBLIC
REVOKE ALL ON public.kb_extraction_jobs FROM PUBLIC, anon, authenticated;

-- Ensure service_role and postgres maintain operational and worker privileges
GRANT ALL ON public.kb_extraction_jobs TO service_role, postgres;

-- Drop legacy client policies on kb_extraction_jobs
DROP POLICY IF EXISTS kb_extraction_jobs_select ON public.kb_extraction_jobs;
DROP POLICY IF EXISTS kb_extraction_jobs_admin_all ON public.kb_extraction_jobs;

-- ------------------------------------------------------------------------------
-- 2. Owner Document Visibility Hardening
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
  )
  OR app_private.is_admin(auth.uid())
);

-- ------------------------------------------------------------------------------
-- 3. Document Pages Visibility Synchronization
-- ------------------------------------------------------------------------------
DROP POLICY IF EXISTS kb_document_pages_select ON public.kb_document_pages;

CREATE POLICY kb_document_pages_select ON public.kb_document_pages FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.kb_documents d
    WHERE d.id = kb_document_pages.document_id
      AND (
        (
          app_private.is_approved(auth.uid()) 
          AND d.is_archived = false 
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
