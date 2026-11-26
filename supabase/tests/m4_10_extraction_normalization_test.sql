BEGIN;
SELECT plan(13);

SELECT has_column('public', 'kb_document_pages', 'normalized_text', 'kb_document_pages must have normalized_text');
SELECT has_column('public', 'kb_document_pages', 'extraction_method', 'kb_document_pages must have extraction_method');
SELECT has_column('public', 'kb_document_pages', 'quality_diagnostics', 'kb_document_pages must have quality_diagnostics');
SELECT has_column('public', 'kb_document_pages', 'ocr_text', 'kb_document_pages must have ocr_text');
SELECT has_column('public', 'kb_document_pages', 'visual_description', 'kb_document_pages must have visual_description');
SELECT has_column('public', 'kb_document_pages', 'has_native_text', 'kb_document_pages must track has_native_text');
SELECT has_column('public', 'kb_document_pages', 'has_usable_normalized_text', 'kb_document_pages must track has_usable_normalized_text');
SELECT has_column('public', 'kb_document_pages', 'has_ocr_text', 'kb_document_pages must track has_ocr_text');
SELECT has_column('public', 'kb_document_pages', 'has_visual_description', 'kb_document_pages must track has_visual_description');
SELECT has_column('public', 'kb_document_pages', 'extraction_quality_status', 'kb_document_pages must track extraction_quality_status');
SELECT has_column('public', 'kb_document_pages', 'visual_triage_class', 'kb_document_pages must track visual_triage_class');
SELECT has_column('public', 'kb_document_chunks', 'source_kind', 'kb_document_chunks must track source_kind provenance');
SELECT has_column('public', 'kb_documents', 'extraction_parser_backend', 'kb_documents must have extraction_parser_backend');

SELECT * FROM finish();
ROLLBACK;
