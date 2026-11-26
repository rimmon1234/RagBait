-- Migration: Stage 2 & Stage 3 PDF Extraction Quality, Traceable Representations, and Sandbox Provenance
-- Preserves `raw_text` verbatim as the native extracted PDF layer while adding
-- separate traceable columns for normalized indexing text, page diagnostics,
-- extraction method/sandbox provenance, and forward slots for OCR / visual descriptions.

ALTER TABLE public.kb_document_pages
  ADD COLUMN IF NOT EXISTS normalized_text text NULL,
  ADD COLUMN IF NOT EXISTS extraction_method text NOT NULL DEFAULT 'pypdf_text',
  ADD COLUMN IF NOT EXISTS normalization_version integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS quality_diagnostics jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS ocr_text text NULL,
  ADD COLUMN IF NOT EXISTS ocr_confidence real NULL,
  ADD COLUMN IF NOT EXISTS visual_description text NULL,
  ADD COLUMN IF NOT EXISTS visual_metadata jsonb NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'kb_document_pages_extraction_method_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_extraction_method_check
      CHECK (extraction_method IN ('pypdf_text', 'pypdf_layout', 'pymupdf_text', 'ocr_fallback', 'hybrid'));
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'kb_document_pages_normalization_version_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_normalization_version_check
      CHECK (normalization_version >= 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'kb_document_pages_ocr_confidence_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_ocr_confidence_check
      CHECK (ocr_confidence IS NULL OR (ocr_confidence >= 0.0 AND ocr_confidence <= 1.0));
  END IF;
END $$;

ALTER TABLE public.kb_documents
  ADD COLUMN IF NOT EXISTS extraction_parser_backend text NOT NULL DEFAULT 'subprocess_isolated',
  ADD COLUMN IF NOT EXISTS extraction_quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'kb_documents_extraction_parser_backend_check'
  ) THEN
    ALTER TABLE public.kb_documents
      ADD CONSTRAINT kb_documents_extraction_parser_backend_check
      CHECK (extraction_parser_backend IN ('container_sandbox', 'subprocess_isolated'));
  END IF;
END $$;

COMMENT ON COLUMN public.kb_document_pages.raw_text IS
  'Verbatim native text extracted from PDF without destructive rewriting.';
COMMENT ON COLUMN public.kb_document_pages.normalized_text IS
  'Equation-safe normalized text representation used for search and chunking.';
COMMENT ON COLUMN public.kb_document_pages.quality_diagnostics IS
  'Per-page Stage 1/2 extraction quality diagnostics and provenance JSON.';
COMMENT ON COLUMN public.kb_document_pages.ocr_text IS
  'Reserved Stage 4 OCR text layer recovered from rendered page images.';
COMMENT ON COLUMN public.kb_document_pages.visual_description IS
  'Reserved Stage 5 AI-generated diagram/equation interpretation.';
