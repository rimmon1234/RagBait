/**
 * TypeScript types for Knowledge Base Administration & Moderation.
 */

export interface SubjectCreateRequest {
  name: string;
  slug: string;
  description?: string | null;
  icon_name?: string;
  display_order?: number;
}

export interface SubjectUpdateRequest {
  name?: string | null;
  slug?: string | null;
  description?: string | null;
  icon_name?: string | null;
  display_order?: number | null;
}

export interface AdminSubjectItem {
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

export interface TopicCreateRequest {
  name: string;
  slug: string;
  description?: string | null;
  display_order?: number;
}

export interface TopicUpdateRequest {
  name?: string | null;
  slug?: string | null;
  description?: string | null;
  display_order?: number | null;
}

export interface AdminTopicItem {
  id: string;
  subject_id: string;
  subject_name?: string | null;
  subject_slug?: string | null;
  name: string;
  slug: string;
  description: string | null;
  display_order: number;
  is_active: boolean;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface AdminDocumentListItem {
  id: string;
  subject_id: string;
  subject_name: string;
  topic_id: string;
  topic_name: string;
  uploader_id: string | null;
  uploader_display_name: string | null;
  uploader_email: string | null;
  title: string;
  original_filename: string;
  mime_type: string;
  file_size_bytes: number;
  upload_status: string;
  extraction_status: string;
  total_pages: number;
  processed_pages: number;
  pages_with_text: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminDocumentDetail extends AdminDocumentListItem {
  storage_path: string;
  sha256_checksum: string | null;
  error_code: string | null;
  error_message: string | null;
  upload_expires_at: string | null;
}

export interface PaginatedAdminDocumentsResponse {
  items: AdminDocumentListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminJobListItem {
  id: string;
  document_id: string;
  document_title: string;
  subject_id: string;
  subject_name: string;
  topic_id: string;
  topic_name: string;
  uploader_display_name: string | null;
  status: string;
  attempts: number;
  max_attempts: number;
  scheduled_at: string;
  locked_until: string | null;
  error_code: string | null;
  error_message: string | null;
  retry_count: number;
  retry_of_job_id: string | null;
  is_retryable: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminJobDetail extends AdminJobListItem {
  file_size_bytes: number;
  mime_type: string;
  document_upload_status: string;
  document_is_archived: boolean;
  retry_eligibility_reason: string | null;
}

export interface PaginatedAdminJobsResponse {
  items: AdminJobListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobRetryResponse {
  job: AdminJobListItem;
  message: string;
}

export interface AdminAuditItem {
  id: string;
  actor_id: string | null;
  actor_display_name: string | null;
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface PaginatedAdminAuditResponse {
  items: AdminAuditItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminOverviewMetrics {
  active_subjects_count: number;
  total_subjects_count: number;
  active_topics_count: number;
  total_topics_count: number;
  total_documents_count: number;
  archived_documents_count: number;
  queued_jobs_count: number;
  processing_jobs_count: number;
  failed_jobs_count: number;
  completed_jobs_count: number;
  recent_documents: AdminDocumentListItem[];
  recent_actions: AdminAuditItem[];
}
