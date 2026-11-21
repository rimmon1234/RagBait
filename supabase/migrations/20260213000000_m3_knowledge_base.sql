-- ==============================================================================
-- RagBait Phase 3 — Milestone M3.1: Academic Knowledge Base & Document Schema
--
-- Entities:
--   1. public.kb_subjects           - Subject taxonomy
--   2. public.kb_topics             - Topic taxonomy with subject-composite integrity
--   3. public.kb_documents          - Uploaded study documents & lifecycle metadata
--   4. public.kb_document_pages     - Physical page-level text extraction provenance
--   5. public.kb_extraction_jobs    - Durable background extraction job queue
--
-- Security:
--   - Non-recursive authorization helpers (app_private.is_approved, app_private.is_admin)
--   - Strict table grants: writes restricted to backend service_role and admin
--   - Explicit Row-Level Security (RLS) on all 5 tables
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 0. Authorization Helper: is_approved (SECURITY DEFINER in app_private)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app_private.is_approved(user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = user_id
      AND approval_status = 'approved'
  );
$$;

REVOKE ALL ON FUNCTION app_private.is_approved(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION app_private.is_approved(uuid) TO authenticated, service_role, postgres;

-- ------------------------------------------------------------------------------
-- 1. Subject Taxonomy Table
-- ------------------------------------------------------------------------------
CREATE TABLE public.kb_subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  slug text NOT NULL,
  description text,
  icon_name text NOT NULL DEFAULT 'BookOpen',
  display_order int NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_subjects_name UNIQUE (name),
  CONSTRAINT uq_kb_subjects_slug UNIQUE (slug),
  CONSTRAINT ck_kb_subjects_name_not_empty CHECK (char_length(trim(name)) > 0),
  CONSTRAINT ck_kb_subjects_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  CONSTRAINT ck_kb_subjects_display_order CHECK (display_order >= 0),
  CONSTRAINT ck_kb_subjects_icon_name CHECK (
    icon_name IN (
      'BookOpen', 'Code', 'Cpu', 'Database', 'Globe', 
      'Layers', 'Server', 'Terminal', 'Brain', 'Compass', 
      'FileText', 'Bookmark'
    )
  )
);

CREATE INDEX idx_kb_subjects_active_order 
  ON public.kb_subjects(is_active, display_order);

CREATE TRIGGER tr_kb_subjects_updated_at
BEFORE UPDATE ON public.kb_subjects
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 2. Topic Taxonomy Table (Composite Key on subject_id, id)
-- ------------------------------------------------------------------------------
CREATE TABLE public.kb_topics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id uuid NOT NULL REFERENCES public.kb_subjects(id) ON DELETE CASCADE,
  name text NOT NULL,
  slug text NOT NULL,
  description text,
  display_order int NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_topics_subject_slug UNIQUE (subject_id, slug),
  CONSTRAINT uq_kb_topics_subject_id UNIQUE (subject_id, id),
  CONSTRAINT ck_kb_topics_name_not_empty CHECK (char_length(trim(name)) > 0),
  CONSTRAINT ck_kb_topics_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  CONSTRAINT ck_kb_topics_display_order CHECK (display_order >= 0)
);

CREATE INDEX idx_kb_topics_subject_active_order 
  ON public.kb_topics(subject_id, is_active, display_order);

CREATE TRIGGER tr_kb_topics_updated_at
BEFORE UPDATE ON public.kb_topics
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 3. Document Lifecycle Enums & Metadata Table
-- ------------------------------------------------------------------------------
CREATE TYPE public.kb_upload_status AS ENUM (
  'pending_upload',
  'uploaded',
  'upload_failed',
  'deleting',
  'deleted'
);

CREATE TYPE public.kb_extraction_status AS ENUM (
  'pending',
  'queued',
  'processing',
  'completed',
  'completed_no_text',
  'failed'
);

CREATE TABLE public.kb_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id uuid NOT NULL,
  topic_id uuid NOT NULL,
  uploader_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  uploader_email_snapshot text,
  title text NOT NULL,
  original_filename text NOT NULL,
  storage_path text NOT NULL,
  mime_type text NOT NULL,
  file_size_bytes bigint NOT NULL,
  sha256_checksum text,
  upload_status public.kb_upload_status NOT NULL DEFAULT 'pending_upload',
  extraction_status public.kb_extraction_status NOT NULL DEFAULT 'pending',
  total_pages int NOT NULL DEFAULT 0,
  processed_pages int NOT NULL DEFAULT 0,
  pages_with_text int NOT NULL DEFAULT 0,
  extraction_version int NOT NULL DEFAULT 1,
  error_code text,
  error_message text,
  is_archived boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_documents_storage_path UNIQUE (storage_path),
  CONSTRAINT fk_kb_documents_subject_topic FOREIGN KEY (subject_id, topic_id)
    REFERENCES public.kb_topics(subject_id, id) ON DELETE RESTRICT,
  CONSTRAINT ck_kb_documents_title_not_empty CHECK (char_length(trim(title)) > 0),
  CONSTRAINT ck_kb_documents_original_filename_not_empty CHECK (char_length(trim(original_filename)) > 0),
  CONSTRAINT ck_kb_documents_file_size CHECK (file_size_bytes > 0 AND file_size_bytes <= 52428800),
  CONSTRAINT ck_kb_documents_mime_type CHECK (mime_type IN ('application/pdf', 'text/plain')),
  CONSTRAINT ck_kb_documents_storage_path_format CHECK (storage_path ~ '^documents/[0-9a-fA-F-]{36}/source\.(pdf|txt)$'),
  CONSTRAINT ck_kb_documents_pages_non_negative CHECK (
    total_pages >= 0 AND
    processed_pages >= 0 AND
    pages_with_text >= 0 AND
    pages_with_text <= processed_pages AND
    (processed_pages <= total_pages OR total_pages = 0)
  ),
  CONSTRAINT ck_kb_documents_extraction_version CHECK (extraction_version >= 1)
);

CREATE INDEX idx_kb_documents_topic_active_created 
  ON public.kb_documents(topic_id, is_archived, created_at DESC);

CREATE INDEX idx_kb_documents_uploader 
  ON public.kb_documents(uploader_id);

CREATE INDEX idx_kb_documents_subject 
  ON public.kb_documents(subject_id);

CREATE TRIGGER tr_kb_documents_updated_at
BEFORE UPDATE ON public.kb_documents
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 4. Extracted Document Pages Table (Page Provenance)
-- ------------------------------------------------------------------------------
CREATE TABLE public.kb_document_pages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  page_number int NOT NULL,
  raw_text text NOT NULL DEFAULT '',
  character_count int NOT NULL DEFAULT 0,
  word_count int NOT NULL DEFAULT 0,
  has_text boolean NOT NULL DEFAULT false,
  extraction_version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_document_page_number UNIQUE (document_id, page_number),
  CONSTRAINT ck_kb_document_pages_page_number CHECK (page_number > 0),
  CONSTRAINT ck_kb_document_pages_char_count CHECK (character_count = char_length(raw_text)),
  CONSTRAINT ck_kb_document_pages_word_count CHECK (word_count >= 0),
  CONSTRAINT ck_kb_document_pages_has_text CHECK (has_text = (char_length(trim(raw_text)) > 0)),
  CONSTRAINT ck_kb_document_pages_version CHECK (extraction_version >= 1)
);

CREATE INDEX idx_kb_document_pages_doc_page 
  ON public.kb_document_pages(document_id, page_number);

-- ------------------------------------------------------------------------------
-- 5. Extraction Jobs Table (Fencing Tokens & Crash Recovery)
-- ------------------------------------------------------------------------------
CREATE TYPE public.kb_job_status AS ENUM (
  'queued',
  'processing',
  'completed',
  'failed'
);

CREATE TABLE public.kb_extraction_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  status public.kb_job_status NOT NULL DEFAULT 'queued',
  worker_id text,
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 3,
  scheduled_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  locked_until timestamptz,
  lease_token uuid,
  error_code text,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT ck_kb_extraction_jobs_attempts CHECK (attempts >= 0 AND attempts <= max_attempts),
  CONSTRAINT ck_kb_extraction_jobs_max_attempts CHECK (max_attempts > 0)
);

CREATE UNIQUE INDEX uq_kb_active_extraction_job 
  ON public.kb_extraction_jobs(document_id)
  WHERE status IN ('queued', 'processing');

CREATE INDEX idx_kb_extraction_jobs_poll 
  ON public.kb_extraction_jobs(status, scheduled_at)
  WHERE status = 'queued';

CREATE INDEX idx_kb_extraction_jobs_locked 
  ON public.kb_extraction_jobs(status, locked_until)
  WHERE status = 'processing';

CREATE TRIGGER tr_kb_extraction_jobs_updated_at
BEFORE UPDATE ON public.kb_extraction_jobs
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ------------------------------------------------------------------------------
-- 6. Table Grants (authenticated vs service_role / backend)
-- ------------------------------------------------------------------------------
-- Revoke all permissions from PUBLIC, anon, and authenticated
REVOKE ALL ON public.kb_subjects FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.kb_topics FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.kb_documents FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.kb_document_pages FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.kb_extraction_jobs FROM PUBLIC, anon, authenticated;

-- Authenticated users have SELECT only; all mutations go through secured API / service_role
GRANT SELECT ON public.kb_subjects TO authenticated;
GRANT SELECT ON public.kb_topics TO authenticated;
GRANT SELECT ON public.kb_documents TO authenticated;
GRANT SELECT ON public.kb_document_pages TO authenticated;
GRANT SELECT ON public.kb_extraction_jobs TO authenticated;

-- Service role & postgres have full administrative and worker privileges
GRANT ALL ON public.kb_subjects TO service_role, postgres;
GRANT ALL ON public.kb_topics TO service_role, postgres;
GRANT ALL ON public.kb_documents TO service_role, postgres;
GRANT ALL ON public.kb_document_pages TO service_role, postgres;
GRANT ALL ON public.kb_extraction_jobs TO service_role, postgres;

-- ------------------------------------------------------------------------------
-- 7. Row Level Security Policies
-- ------------------------------------------------------------------------------
ALTER TABLE public.kb_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_document_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_extraction_jobs ENABLE ROW LEVEL SECURITY;

-- 7.1 kb_subjects Policies
-- Approved students view active subjects; Admins view all
CREATE POLICY kb_subjects_select ON public.kb_subjects FOR SELECT
USING (
  (is_active = true AND app_private.is_approved(auth.uid()))
  OR app_private.is_admin(auth.uid())
);

-- Admin only for mutations
CREATE POLICY kb_subjects_admin_all ON public.kb_subjects FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 7.2 kb_topics Policies
-- Approved students view active topics under active subjects; Admins view all
CREATE POLICY kb_topics_select ON public.kb_topics FOR SELECT
USING (
  (
    is_active = true 
    AND app_private.is_approved(auth.uid()) 
    AND EXISTS (
      SELECT 1 FROM public.kb_subjects s 
      WHERE s.id = kb_topics.subject_id AND s.is_active = true
    )
  )
  OR app_private.is_admin(auth.uid())
);

-- Admin only for mutations
CREATE POLICY kb_topics_admin_all ON public.kb_topics FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 7.3 kb_documents Policies
-- Approved students view uploaded, non-archived documents OR their own pending uploads (non-archived); Admins view all
CREATE POLICY kb_documents_select ON public.kb_documents FOR SELECT
USING (
  (
    app_private.is_approved(auth.uid())
    AND is_archived = false
    AND (
      upload_status = 'uploaded'
      OR uploader_id = auth.uid()
    )
  )
  OR app_private.is_admin(auth.uid())
);

-- Admin only for direct SQL mutations
CREATE POLICY kb_documents_admin_all ON public.kb_documents FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 7.4 kb_document_pages Policies
-- Pages visible if parent document is accessible to caller
CREATE POLICY kb_document_pages_select ON public.kb_document_pages FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.kb_documents d
    WHERE d.id = kb_document_pages.document_id
      AND (
        (
          app_private.is_approved(auth.uid()) 
          AND d.is_archived = false 
          AND (d.upload_status = 'uploaded' OR d.uploader_id = auth.uid())
        )
        OR app_private.is_admin(auth.uid())
      )
  )
);

-- Admin only for direct SQL mutations
CREATE POLICY kb_document_pages_admin_all ON public.kb_document_pages FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));

-- 7.5 kb_extraction_jobs Policies
-- Uploaders can view the status of jobs for their own documents; Admins view all
CREATE POLICY kb_extraction_jobs_select ON public.kb_extraction_jobs FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.kb_documents d
    WHERE d.id = kb_extraction_jobs.document_id
      AND (
        (app_private.is_approved(auth.uid()) AND d.uploader_id = auth.uid())
        OR app_private.is_admin(auth.uid())
      )
  )
);

-- Admin only for direct SQL mutations
CREATE POLICY kb_extraction_jobs_admin_all ON public.kb_extraction_jobs FOR ALL
USING (app_private.is_admin(auth.uid()))
WITH CHECK (app_private.is_admin(auth.uid()));
