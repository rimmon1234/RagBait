/**
 * Knowledge Base TypeScript type definitions.
 * Mirrors backend Pydantic schemas in apps/api/app/modules/knowledge_base/schemas.py.
 */

export interface SubjectRead {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  icon_name: string;
  display_order: number;
  is_active: boolean;
  topic_count: number;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface TopicRead {
  id: string;
  subject_id: string;
  name: string;
  slug: string;
  description: string | null;
  display_order: number;
  is_active: boolean;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentListItem {
  id: string;
  subject_id: string;
  topic_id: string;
  uploader_id: string | null;
  uploader_display_name: string | null;
  title: string;
  original_filename: string;
  mime_type: string;
  file_size_bytes: number;
  upload_status: string;
  extraction_status: string;
  total_pages: number;
  processed_pages: number;
  pages_with_text: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail extends DocumentListItem {
  sha256_checksum?: string | null;
  error_code?: string | null;
  upload_expires_at?: string | null;
}

export interface DocumentListResponse {
  items: DocumentListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentPageItem {
  id: string;
  document_id: string;
  page_number: number;
  raw_text: string;
  character_count: number;
  word_count: number;
  has_text: boolean;
  created_at: string;
}

export interface DocumentPagesResponse {
  items: DocumentPageItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface UploadIntentRequest {
  subject_id: string;
  topic_id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  file_size_bytes: number;
  sha256_checksum?: string | null;
}

export interface UploadIntentResponse {
  document_id: string;
  storage_path: string;
  signed_upload_url: string;
  token: string;
  upload_expires_at: string;
  max_file_size_bytes: number;
  supported_methods: string[];
}

export interface ConfirmUploadRequest {
  sha256_checksum?: string | null;
  file_size_bytes?: number | null;
}

export interface ConfirmUploadResponse {
  document: DocumentDetail;
  message: string;
}

export interface DownloadUrlResponse {
  download_url: string;
  expires_in: number;
  original_filename: string;
  mime_type: string;
}

export type UploadStage =
  | "idle"
  | "hashing"
  | "reserving"
  | "uploading"
  | "confirming"
  | "completed"
  | "error";

export type ExtractionStatus =
  | "pending_upload"
  | "upload_failed"
  | "pending"
  | "queued"
  | "processing"
  | "completed"
  | "completed_no_text"
  | "failed"
  | "deleting"
  | "deleted";
