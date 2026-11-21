import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  archiveAdminDocument,
  archiveAdminSubject,
  archiveAdminTopic,
  createAdminSubject,
  createAdminTopic,
  deleteAdminDocument,
  getAdminAuditLogs,
  getAdminDocument,
  getAdminDocuments,
  getAdminJob,
  getAdminJobs,
  getAdminOverview,
  getAdminSubjects,
  getAdminTopics,
  reorderAdminTopic,
  restoreAdminDocument,
  restoreAdminSubject,
  restoreAdminTopic,
  retryAdminJob,
  updateAdminSubject,
  updateAdminTopic,
} from "@/lib/knowledge-base/knowledge-base-admin-api";
import * as api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
  ApiError: class extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

describe("Admin Knowledge Base API Client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches admin overview metrics", async () => {
    const mockOverview = {
      active_subjects_count: 5,
      total_subjects_count: 6,
      active_topics_count: 15,
      total_topics_count: 18,
      total_documents_count: 42,
      archived_documents_count: 4,
      queued_jobs_count: 2,
      processing_jobs_count: 1,
      failed_jobs_count: 3,
      completed_jobs_count: 36,
      recent_documents: [],
      recent_actions: [],
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockOverview);

    const result = await getAdminOverview();
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/overview");
    expect(result).toEqual(mockOverview);
  });

  it("fetches admin subjects with include_inactive param", async () => {
    const mockSubjects = [
      {
        id: "sub-1",
        name: "DBMS",
        slug: "dbms",
        description: "Databases",
        icon_name: "Database",
        display_order: 1,
        is_active: true,
        topic_count: 4,
        document_count: 10,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ];
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockSubjects);

    const result = await getAdminSubjects(true);
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects?include_inactive=true");
    expect(result).toEqual(mockSubjects);
  });

  it("creates a new subject", async () => {
    const payload = {
      name: "Computer Networks",
      slug: "networks",
      description: "Protocols and architectures",
      icon_name: "Globe",
      display_order: 3,
    };
    const mockCreated = { ...payload, id: "sub-new", is_active: true, topic_count: 0, document_count: 0, created_at: "", updated_at: "" };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockCreated);

    const result = await createAdminSubject(payload);
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    expect(result).toEqual(mockCreated);
  });

  it("updates an existing subject", async () => {
    const patch = { name: "Advanced DBMS", display_order: 2 };
    const mockUpdated = { id: "sub-1", name: "Advanced DBMS", slug: "dbms", icon_name: "Database", display_order: 2, is_active: true, topic_count: 4, document_count: 10, created_at: "", updated_at: "" };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockUpdated);

    const result = await updateAdminSubject("sub-1", patch);
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects/sub-1", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    expect(result).toEqual(mockUpdated);
  });

  it("archives and restores a subject", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "sub-1", is_active: false });
    await archiveAdminSubject("sub-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects/sub-1/archive", {
      method: "POST",
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "sub-1", is_active: true });
    await restoreAdminSubject("sub-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects/sub-1/restore", {
      method: "POST",
    });
  });

  it("fetches admin topics with filter parameters", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce([]);
    await getAdminTopics("sub-1", true);
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/admin/subjects/sub-1/topics?include_inactive=true"
    );
  });

  it("creates, updates, reorders, archives, and restores a topic", async () => {
    const payload = {
      name: "Indexing",
      slug: "indexing",
      description: "B-Trees and hashing",
      display_order: 1,
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ ...payload, id: "top-1", subject_id: "sub-1" });
    await createAdminTopic("sub-1", payload);
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/subjects/sub-1/topics", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "top-1", name: "B-Trees" });
    await updateAdminTopic("top-1", { name: "B-Trees" });
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/topics/top-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "B-Trees" }),
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "top-1", display_order: 5 });
    await reorderAdminTopic("top-1", 5);
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/topics/top-1/order", {
      method: "PATCH",
      body: JSON.stringify({ display_order: 5 }),
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "top-1", is_active: false });
    await archiveAdminTopic("top-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/topics/top-1/archive", {
      method: "POST",
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "top-1", is_active: true });
    await restoreAdminTopic("top-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/topics/top-1/restore", {
      method: "POST",
    });
  });

  it("fetches admin documents with filters and pagination", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ items: [], total: 0, limit: 10, offset: 0 });
    await getAdminDocuments({
      limit: 10,
      offset: 0,
      subject_id: "sub-1",
      extraction_status: "failed",
      is_archived: false,
      search: "exam",
    });
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/admin/documents?limit=10&offset=0&subject_id=sub-1&extraction_status=failed&is_archived=false&search=exam"
    );
  });

  it("fetches single document details", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "doc-1" });
    await getAdminDocument("doc-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/documents/doc-1");
  });

  it("archives, restores, and permanently deletes documents with mandatory reasons", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "doc-1", is_archived: true });
    await archiveAdminDocument("doc-1", "Outdated material");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/documents/doc-1/archive", {
      method: "POST",
      body: JSON.stringify({ reason: "Outdated material" }),
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "doc-1", is_archived: false });
    await restoreAdminDocument("doc-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/documents/doc-1/restore", {
      method: "POST",
      body: JSON.stringify({ reason: null }),
    });

    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(undefined);
    await deleteAdminDocument("doc-1", "Copyright infringement notice");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/documents/doc-1", {
      method: "DELETE",
      body: JSON.stringify({ reason: "Copyright infringement notice" }),
    });
  });

  it("fetches admin jobs with filter query params", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 });
    await getAdminJobs({ limit: 20, offset: 0, status: "failed" });
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/admin/jobs?limit=20&offset=0&status=failed"
    );
  });

  it("fetches single job details and retries a job", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ id: "job-1" });
    await getAdminJob("job-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/jobs/job-1");

    const mockRetryResult = {
      job: { id: "job-new-123", status: "queued" },
      message: "Job queued for retry.",
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockRetryResult);

    const result = await retryAdminJob("job-old-456");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/admin/jobs/job-old-456/retry", {
      method: "POST",
      body: JSON.stringify({ reason: null }),
    });
    expect(result).toEqual(mockRetryResult);
  });

  it("fetches admin audit logs with filters", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({ items: [], total: 0, limit: 15, offset: 0 });
    await getAdminAuditLogs({
      limit: 15,
      offset: 0,
      action: "kb.document.archive",
      actor_id: "admin-1",
    });
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/admin/audit?limit=15&offset=0&action=kb.document.archive&actor_id=admin-1"
    );
  });
});
