-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.2: Knowledge Base Document Upload Lifecycle
--
-- Additions:
--   1. Add upload_expires_at TIMESTAMPTZ to public.kb_documents
--      to track upload reservation TTLs and enable bounded cleanup of abandoned uploads.
--   2. Add index on (upload_status, upload_expires_at) for efficient sweeping.
-- ==============================================================================

ALTER TABLE public.kb_documents
  ADD COLUMN IF NOT EXISTS upload_expires_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_kb_documents_pending_expiry
  ON public.kb_documents(upload_status, upload_expires_at)
  WHERE upload_status = 'pending_upload';

COMMENT ON COLUMN public.kb_documents.upload_expires_at IS
  'Timestamp when the signed upload reservation expires. Abandoned pending uploads after this time are swept.';
