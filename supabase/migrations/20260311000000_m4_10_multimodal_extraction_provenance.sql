-- Migration: M4.10 Multimodal Extraction Quality Metrics, Triage Classification & Provenance
-- Implements mandatory separate quality metrics (`has_native_text`, `has_usable_normalized_text`,
-- `has_ocr_text`, `has_visual_description`, `extraction_quality_status`), visual triage classes,
-- Stage 4/5 provenance/uncertainty metadata on `kb_document_pages`, and `source_kind` on chunks.

ALTER TABLE public.kb_document_pages
  ADD COLUMN IF NOT EXISTS has_native_text boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS has_usable_normalized_text boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS has_ocr_text boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS has_visual_description boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS extraction_quality_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS visual_triage_class text NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS ocr_model text NULL,
  ADD COLUMN IF NOT EXISTS ocr_version text NULL,
  ADD COLUMN IF NOT EXISTS ocr_status text NOT NULL DEFAULT 'not_needed',
  ADD COLUMN IF NOT EXISTS ocr_error text NULL,
  ADD COLUMN IF NOT EXISTS ocr_processed_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS visual_model text NULL,
  ADD COLUMN IF NOT EXISTS visual_version text NULL,
  ADD COLUMN IF NOT EXISTS visual_confidence real NULL,
  ADD COLUMN IF NOT EXISTS visual_status text NOT NULL DEFAULT 'not_needed',
  ADD COLUMN IF NOT EXISTS visual_error text NULL,
  ADD COLUMN IF NOT EXISTS visual_processed_at timestamptz NULL;

-- Backfill accurate has_native_text on existing pages without overwriting raw_text
UPDATE public.kb_document_pages
SET has_native_text = has_text,
    has_usable_normalized_text = (
      normalized_text IS NOT NULL
      AND char_length(btrim(normalized_text)) >= 40
    )
WHERE has_native_text <> has_text
   OR (normalized_text IS NOT NULL AND has_usable_normalized_text = false AND char_length(btrim(normalized_text)) >= 40);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_pages_quality_status_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_quality_status_check
      CHECK (
        extraction_quality_status IN (
          'pending',
          'text_complete',
          'boilerplate_only',
          'needs_enrichment',
          'partially_enriched',
          'enriched',
          'failed_enrichment',
          'empty'
        )
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_pages_visual_triage_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_visual_triage_check
      CHECK (
        visual_triage_class IN (
          'none',
          'scanned_ocr',
          'math_equation_ocr',
          'flowchart_or_diagram_visual',
          'table_recovery',
          'mixed_ocr_and_visual'
        )
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_pages_ocr_status_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_ocr_status_check
      CHECK (ocr_status IN ('not_needed', 'pending', 'completed', 'failed', 'skipped'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_pages_visual_status_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_visual_status_check
      CHECK (visual_status IN ('not_needed', 'pending', 'completed', 'failed', 'skipped'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_pages_visual_confidence_check'
  ) THEN
    ALTER TABLE public.kb_document_pages
      ADD CONSTRAINT kb_document_pages_visual_confidence_check
      CHECK (visual_confidence IS NULL OR (visual_confidence >= 0.0 AND visual_confidence <= 1.0));
  END IF;
END $$;

ALTER TABLE public.kb_document_chunks
  ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'text_layer';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'kb_document_chunks_source_kind_check'
  ) THEN
    ALTER TABLE public.kb_document_chunks
      ADD CONSTRAINT kb_document_chunks_source_kind_check
      CHECK (source_kind IN ('text_layer', 'ocr', 'visual_description', 'multimodal_combined'));
  END IF;
END $$;
