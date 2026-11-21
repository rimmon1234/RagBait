/**
 * Knowledge Base API Client.
 * Wraps backend endpoints mounted under /api/v1/kb.
 */

import { fetchWithAuth } from "@/lib/api";
import type {
  ConfirmUploadRequest,
  ConfirmUploadResponse,
  DocumentDetail,
  DocumentListResponse,
  DocumentPagesResponse,
  DownloadUrlResponse,
  SubjectRead,
  TopicRead,
  UploadIntentRequest,
  UploadIntentResponse,
} from "./knowledge-base-types";

/**
 * Retrieve all active academic subjects with topic and document counts.
 */
export async function getSubjects(): Promise<SubjectRead[]> {
  return fetchWithAuth<SubjectRead[]>("/api/v1/kb/subjects");
}

/**
 * Retrieve all active topics for a given subject.
 */
export async function getTopics(subjectId: string): Promise<TopicRead[]> {
  return fetchWithAuth<TopicRead[]>(`/api/v1/kb/subjects/${subjectId}/topics`);
}

/**
 * Retrieve paginated uploaded documents within a topic.
 */
export async function getTopicDocuments(
  topicId: string,
  limit = 20,
  offset = 0
): Promise<DocumentListResponse> {
  return fetchWithAuth<DocumentListResponse>(
    `/api/v1/kb/topics/${topicId}/documents?limit=${limit}&offset=${offset}`
  );
}

/**
 * Retrieve details and extraction status for a single document.
 */
export async function getDocument(documentId: string): Promise<DocumentDetail> {
  return fetchWithAuth<DocumentDetail>(`/api/v1/kb/documents/${documentId}`);
}

/**
 * Retrieve paginated extracted text pages for a document.
 */
export async function getDocumentPages(
  documentId: string,
  limit = 50,
  offset = 0
): Promise<DocumentPagesResponse> {
  return fetchWithAuth<DocumentPagesResponse>(
    `/api/v1/kb/documents/${documentId}/pages?limit=${limit}&offset=${offset}`
  );
}

/**
 * Create a signed upload reservation for a new document.
 */
export async function createUploadIntent(
  data: UploadIntentRequest
): Promise<UploadIntentResponse> {
  return fetchWithAuth<UploadIntentResponse>("/api/v1/kb/documents/upload-intent", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/**
 * Confirm and verify an uploaded document in Supabase Storage and queue extraction.
 */
export async function confirmUpload(
  documentId: string,
  data?: ConfirmUploadRequest
): Promise<ConfirmUploadResponse> {
  return fetchWithAuth<ConfirmUploadResponse>(
    `/api/v1/kb/documents/${documentId}/confirm-upload`,
    {
      method: "POST",
      body: JSON.stringify(data || {}),
    }
  );
}

/**
 * Request a short-lived (5-minute) signed URL for downloading the original document file.
 */
export async function getDownloadUrl(documentId: string): Promise<DownloadUrlResponse> {
  return fetchWithAuth<DownloadUrlResponse>(
    `/api/v1/kb/documents/${documentId}/download-url`
  );
}

/**
 * Soft-delete a document and trigger deletion from storage.
 */
export async function deleteDocument(documentId: string): Promise<void> {
  return fetchWithAuth<void>(`/api/v1/kb/documents/${documentId}`, {
    method: "DELETE",
  });
}
