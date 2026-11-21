/**
 * Knowledge Base Administration API Client.
 * Wraps administrative endpoints mounted under /api/v1/kb/admin.
 */

import { fetchWithAuth } from "@/lib/api";
import type {
  AdminAuditItem,
  AdminDocumentDetail,
  AdminDocumentListItem,
  AdminJobDetail,
  AdminJobListItem,
  AdminOverviewMetrics,
  AdminSubjectItem,
  AdminTopicItem,
  JobRetryResponse,
  PaginatedAdminAuditResponse,
  PaginatedAdminDocumentsResponse,
  PaginatedAdminJobsResponse,
  SubjectCreateRequest,
  SubjectUpdateRequest,
  TopicCreateRequest,
  TopicUpdateRequest,
} from "./knowledge-base-admin-types";

export async function getAdminOverview(): Promise<AdminOverviewMetrics> {
  return fetchWithAuth<AdminOverviewMetrics>("/api/v1/kb/admin/overview");
}

export async function getAdminSubjects(
  includeInactive = true,
  search?: string
): Promise<AdminSubjectItem[]> {
  const params = new URLSearchParams();
  params.set("include_inactive", String(includeInactive));
  if (search) params.set("search", search);
  return fetchWithAuth<AdminSubjectItem[]>(`/api/v1/kb/admin/subjects?${params.toString()}`);
}

export async function createAdminSubject(
  data: SubjectCreateRequest
): Promise<AdminSubjectItem> {
  return fetchWithAuth<AdminSubjectItem>("/api/v1/kb/admin/subjects", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateAdminSubject(
  id: string,
  data: SubjectUpdateRequest
): Promise<AdminSubjectItem> {
  return fetchWithAuth<AdminSubjectItem>(`/api/v1/kb/admin/subjects/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function archiveAdminSubject(id: string): Promise<AdminSubjectItem> {
  return fetchWithAuth<AdminSubjectItem>(`/api/v1/kb/admin/subjects/${id}/archive`, {
    method: "POST",
  });
}

export async function restoreAdminSubject(id: string): Promise<AdminSubjectItem> {
  return fetchWithAuth<AdminSubjectItem>(`/api/v1/kb/admin/subjects/${id}/restore`, {
    method: "POST",
  });
}

export async function getAdminTopics(
  subjectId: string,
  includeInactive = true
): Promise<AdminTopicItem[]> {
  return fetchWithAuth<AdminTopicItem[]>(
    `/api/v1/kb/admin/subjects/${subjectId}/topics?include_inactive=${includeInactive}`
  );
}

export async function createAdminTopic(
  subjectId: string,
  data: TopicCreateRequest
): Promise<AdminTopicItem> {
  return fetchWithAuth<AdminTopicItem>(`/api/v1/kb/admin/subjects/${subjectId}/topics`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateAdminTopic(
  id: string,
  data: TopicUpdateRequest
): Promise<AdminTopicItem> {
  return fetchWithAuth<AdminTopicItem>(`/api/v1/kb/admin/topics/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function reorderAdminTopic(
  id: string,
  displayOrder: number
): Promise<AdminTopicItem> {
  return fetchWithAuth<AdminTopicItem>(`/api/v1/kb/admin/topics/${id}/order`, {
    method: "PATCH",
    body: JSON.stringify({ display_order: displayOrder }),
  });
}

export async function archiveAdminTopic(id: string): Promise<AdminTopicItem> {
  return fetchWithAuth<AdminTopicItem>(`/api/v1/kb/admin/topics/${id}/archive`, {
    method: "POST",
  });
}

export async function restoreAdminTopic(id: string): Promise<AdminTopicItem> {
  return fetchWithAuth<AdminTopicItem>(`/api/v1/kb/admin/topics/${id}/restore`, {
    method: "POST",
  });
}

export interface DocumentFilterParams {
  limit?: number;
  offset?: number;
  subject_id?: string;
  topic_id?: string;
  uploader_id?: string;
  upload_status?: string;
  extraction_status?: string;
  is_archived?: boolean;
  search?: string;
}

export async function getAdminDocuments(
  filters: DocumentFilterParams = {}
): Promise<PaginatedAdminDocumentsResponse> {
  const params = new URLSearchParams();
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  if (filters.subject_id) params.set("subject_id", filters.subject_id);
  if (filters.topic_id) params.set("topic_id", filters.topic_id);
  if (filters.uploader_id) params.set("uploader_id", filters.uploader_id);
  if (filters.upload_status) params.set("upload_status", filters.upload_status);
  if (filters.extraction_status) params.set("extraction_status", filters.extraction_status);
  if (filters.is_archived !== undefined) params.set("is_archived", String(filters.is_archived));
  if (filters.search) params.set("search", filters.search);

  return fetchWithAuth<PaginatedAdminDocumentsResponse>(
    `/api/v1/kb/admin/documents?${params.toString()}`
  );
}

export async function getAdminDocument(id: string): Promise<AdminDocumentDetail> {
  return fetchWithAuth<AdminDocumentDetail>(`/api/v1/kb/admin/documents/${id}`);
}

export async function archiveAdminDocument(
  id: string,
  reason: string
): Promise<AdminDocumentDetail> {
  return fetchWithAuth<AdminDocumentDetail>(`/api/v1/kb/admin/documents/${id}/archive`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function restoreAdminDocument(
  id: string,
  reason?: string
): Promise<AdminDocumentDetail> {
  return fetchWithAuth<AdminDocumentDetail>(`/api/v1/kb/admin/documents/${id}/restore`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null }),
  });
}

export async function deleteAdminDocument(id: string, reason: string): Promise<void> {
  return fetchWithAuth<void>(`/api/v1/kb/admin/documents/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ reason }),
  });
}

export interface JobFilterParams {
  limit?: number;
  offset?: number;
  status?: string;
  subject_id?: string;
  topic_id?: string;
  search?: string;
}

export async function getAdminJobs(
  filters: JobFilterParams = {}
): Promise<PaginatedAdminJobsResponse> {
  const params = new URLSearchParams();
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  if (filters.status) params.set("status", filters.status);
  if (filters.subject_id) params.set("subject_id", filters.subject_id);
  if (filters.topic_id) params.set("topic_id", filters.topic_id);
  if (filters.search) params.set("search", filters.search);

  return fetchWithAuth<PaginatedAdminJobsResponse>(`/api/v1/kb/admin/jobs?${params.toString()}`);
}

export async function getAdminJob(id: string): Promise<AdminJobDetail> {
  return fetchWithAuth<AdminJobDetail>(`/api/v1/kb/admin/jobs/${id}`);
}

export async function retryAdminJob(
  id: string,
  reason?: string
): Promise<JobRetryResponse> {
  return fetchWithAuth<JobRetryResponse>(`/api/v1/kb/admin/jobs/${id}/retry`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null }),
  });
}

export interface AuditFilterParams {
  limit?: number;
  offset?: number;
  action?: string;
  target_type?: string;
  actor_id?: string;
}

export async function getAdminAuditLogs(
  filters: AuditFilterParams = {}
): Promise<PaginatedAdminAuditResponse> {
  const params = new URLSearchParams();
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  if (filters.action) params.set("action", filters.action);
  if (filters.target_type) params.set("target_type", filters.target_type);
  if (filters.actor_id) params.set("actor_id", filters.actor_id);

  return fetchWithAuth<PaginatedAdminAuditResponse>(
    `/api/v1/kb/admin/audit?${params.toString()}`
  );
}
