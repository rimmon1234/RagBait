import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminOverviewDashboard from "@/app/admin/knowledge-base/page";
import AdminSubjectsPage from "@/app/admin/knowledge-base/subjects/page";
import DocumentModerationPage from "@/app/admin/knowledge-base/documents/page";
import ExtractionJobsAdminPage from "@/app/admin/knowledge-base/jobs/page";
import KnowledgeBaseAuditPage from "@/app/admin/knowledge-base/audit/page";
import * as adminApi from "@/lib/knowledge-base/knowledge-base-admin-api";
import * as kbApi from "@/lib/knowledge-base/knowledge-base-api";
import type {
  AdminDocumentDetail,
  AdminDocumentListItem,
  AdminJobDetail,
  AdminJobListItem,
  AdminOverviewMetrics,
  AdminSubjectItem,
  AdminTopicItem,
} from "@/lib/knowledge-base/knowledge-base-admin-types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
  }),
  usePathname: () => "/admin/knowledge-base",
}));

vi.mock("@/lib/knowledge-base/knowledge-base-admin-api", () => ({
  getAdminOverview: vi.fn(),
  getAdminSubjects: vi.fn(),
  createAdminSubject: vi.fn(),
  updateAdminSubject: vi.fn(),
  archiveAdminSubject: vi.fn(),
  restoreAdminSubject: vi.fn(),
  getAdminTopics: vi.fn(),
  createAdminTopic: vi.fn(),
  updateAdminTopic: vi.fn(),
  reorderAdminTopic: vi.fn(),
  archiveAdminTopic: vi.fn(),
  restoreAdminTopic: vi.fn(),
  getAdminDocuments: vi.fn(),
  getAdminDocument: vi.fn(),
  archiveAdminDocument: vi.fn(),
  restoreAdminDocument: vi.fn(),
  deleteAdminDocument: vi.fn(),
  getAdminJobs: vi.fn(),
  getAdminJob: vi.fn(),
  retryAdminJob: vi.fn(),
  getAdminAuditLogs: vi.fn(),
}));

vi.mock("@/lib/knowledge-base/knowledge-base-api", () => ({
  getDownloadUrl: vi.fn(),
  getDocumentPages: vi.fn().mockResolvedValue([]),
}));

describe("AdminOverviewDashboard Page", () => {
  const mockMetrics: AdminOverviewMetrics = {
    active_subjects_count: 4,
    total_subjects_count: 5,
    active_topics_count: 12,
    total_topics_count: 15,
    total_documents_count: 40,
    archived_documents_count: 3,
    queued_jobs_count: 1,
    processing_jobs_count: 0,
    failed_jobs_count: 2,
    completed_jobs_count: 37,
    recent_documents: [
      {
        id: "doc-1",
        subject_id: "sub-1",
        subject_name: "DBMS",
        topic_id: "top-1",
        topic_name: "SQL",
        uploader_id: "u-1",
        uploader_display_name: "Bob",
        uploader_email: "bob@example.com",
        title: "Relational Algebra Guide",
        original_filename: "algebra.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1048576,
        upload_status: "uploaded",
        extraction_status: "completed",
        is_archived: false,
        total_pages: 5,
        processed_pages: 5,
        pages_with_text: 5,
        created_at: "2026-02-01T12:00:00Z",
        updated_at: "2026-02-01T12:00:00Z",
      },
    ],
    recent_actions: [
      {
        id: "aud-1",
        actor_id: "adm-1",
        actor_display_name: "Admin User",
        actor_email: "admin@ragbait.internal",
        action: "kb.document.archive",
        target_type: "kb_document",
        target_id: "doc-99",
        payload: { reason: "Test moderation archive" },
        created_at: "2026-02-01T13:00:00Z",
      },
    ],
  };

  it("loads metrics, recent uploads, and recent actions", async () => {
    vi.mocked(adminApi.getAdminOverview).mockResolvedValueOnce(mockMetrics);

    render(<AdminOverviewDashboard />);

    await waitFor(() => {
      expect(screen.getByText("Knowledge Base Administration")).toBeInTheDocument();
      expect(screen.getByText("Relational Algebra Guide")).toBeInTheDocument();
      expect(screen.getByText("kb.document.archive")).toBeInTheDocument();
    });
  });
});

describe("AdminSubjectsPage", () => {
  const mockSubjects: AdminSubjectItem[] = [
    {
      id: "sub-1",
      name: "Database Systems",
      slug: "dbms",
      description: "Relational & NoSQL",
      icon_name: "Database",
      display_order: 1,
      is_active: true,
      topic_count: 2,
      document_count: 10,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-02-01T00:00:00Z",
    },
  ];

  const mockTopics: AdminTopicItem[] = [
    {
      id: "top-1",
      subject_id: "sub-1",
      subject_name: "Database Systems",
      subject_slug: "dbms",
      name: "Normalization",
      slug: "normalization",
      description: "1NF to BCNF",
      display_order: 1,
      is_active: true,
      document_count: 5,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-02-01T00:00:00Z",
    },
  ];

  it("renders subjects list, expands topics, and handles topic display", async () => {
    vi.mocked(adminApi.getAdminSubjects).mockResolvedValueOnce(mockSubjects);
    vi.mocked(adminApi.getAdminTopics).mockResolvedValueOnce(mockTopics);

    render(<AdminSubjectsPage />);

    await waitFor(() => {
      expect(screen.getByText("Database Systems")).toBeInTheDocument();
      expect(screen.getByText("/dbms")).toBeInTheDocument();
    });

    // Expand topics by clicking on subject header
    const subjectHeader = screen.getByText("Database Systems");
    fireEvent.click(subjectHeader);

    await waitFor(() => {
      expect(adminApi.getAdminTopics).toHaveBeenCalledWith("sub-1", true);
      expect(screen.getByText("Normalization")).toBeInTheDocument();
    });
  });
});

describe("AdminDocumentsPage", () => {
  const mockDocs: AdminDocumentListItem[] = [
    {
      id: "doc-1",
      subject_id: "sub-1",
      subject_name: "DBMS",
      topic_id: "top-1",
      topic_name: "SQL",
      uploader_id: "u-1",
      uploader_display_name: "Bob",
      uploader_email: "bob@example.com",
      title: "Relational Algebra Guide",
      original_filename: "algebra.pdf",
      mime_type: "application/pdf",
      file_size_bytes: 1048576,
      upload_status: "uploaded",
      extraction_status: "completed",
      is_archived: false,
      total_pages: 5,
      processed_pages: 5,
      pages_with_text: 5,
      created_at: "2026-02-01T12:00:00Z",
      updated_at: "2026-02-01T12:00:00Z",
    },
  ];

  const mockDetail: AdminDocumentDetail = {
    ...mockDocs[0],
    storage_path: "documents/doc-1/algebra.pdf",
    sha256_checksum: "hash123",
    error_code: null,
    error_message: null,
    upload_expires_at: null,
  };

  it("renders documents moderation table and opens drawer on row inspect click", async () => {
    vi.mocked(adminApi.getAdminDocuments).mockResolvedValueOnce({
      items: mockDocs,
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(adminApi.getAdminSubjects).mockResolvedValueOnce([]);
    vi.mocked(adminApi.getAdminDocument).mockResolvedValueOnce(mockDetail);

    render(<DocumentModerationPage />);

    await waitFor(() => {
      expect(screen.getByText("Relational Algebra Guide")).toBeInTheDocument();
      expect(screen.getByText("Bob")).toBeInTheDocument();
    });

    // Click on Inspect button
    const inspectBtn = screen.getByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(adminApi.getAdminDocument).toHaveBeenCalledWith("doc-1");
      expect(screen.getByText("Relational Algebra Guide")).toBeInTheDocument();
      expect(screen.getByText("algebra.pdf")).toBeInTheDocument();
    });
  });
});

describe("AdminJobsPage", () => {
  const mockJobs: AdminJobListItem[] = [
    {
      id: "job-101",
      document_id: "doc-1",
      document_title: "Relational Algebra Guide",
      subject_id: "sub-1",
      subject_name: "DBMS",
      topic_id: "top-1",
      topic_name: "SQL",
      uploader_display_name: "Alice",
      status: "failed",
      attempts: 1,
      max_attempts: 3,
      scheduled_at: "2026-02-01T12:00:00Z",
      locked_until: null,
      error_code: "TIMEOUT",
      error_message: "Worker lease expired",
      retry_count: 0,
      retry_of_job_id: null,
      is_retryable: true,
      created_at: "2026-02-01T12:00:00Z",
      updated_at: "2026-02-01T12:01:00Z",
    },
  ];

  const mockJobDetail: AdminJobDetail = {
    ...mockJobs[0],
    file_size_bytes: 1048576,
    mime_type: "application/pdf",
    document_upload_status: "uploaded",
    document_is_archived: false,
    retry_eligibility_reason: "Eligible for manual retry.",
  };

  it("renders jobs table, allows opening details, and retrying", async () => {
    vi.mocked(adminApi.getAdminJobs).mockResolvedValueOnce({
      items: mockJobs,
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(adminApi.getAdminJob).mockResolvedValueOnce(mockJobDetail);
    vi.mocked(adminApi.retryAdminJob).mockResolvedValueOnce({
      job: { ...mockJobs[0], id: "job-102", status: "queued", retry_count: 1 },
      message: "Job queued for retry.",
    });

    render(<ExtractionJobsAdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Relational Algebra Guide")).toBeInTheDocument();
      expect(screen.getByText("TIMEOUT")).toBeInTheDocument();
    });

    // Click Inspect to open drawer
    const inspectBtn = screen.getByRole("button", { name: /Inspect/i });
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(adminApi.getAdminJob).toHaveBeenCalledWith("job-101");
      expect(screen.getByText(/Job job-101/i)).toBeInTheDocument();
    });

    // Click Retry Extraction button in drawer (index 1: index 0 is table row)
    const retryButtons = screen.getAllByRole("button", { name: /Retry Extraction/i });
    fireEvent.click(retryButtons[1]);

    // In page, clicking retry opens confirmation dialog
    await waitFor(() => {
      expect(screen.getByText("Retry Extraction Job")).toBeInTheDocument();
    });

    // Modal submit button is the newest (last) button matching "Retry Extraction"
    const confirmButtons = screen.getAllByRole("button", { name: /Retry Extraction/i });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(adminApi.retryAdminJob).toHaveBeenCalledWith("job-101", "");
    });
  });
});

describe("AdminAuditPage", () => {
  const mockAudit = {
    items: [
      {
        id: "aud-1",
        action: "kb.document.delete_permanent",
        actor_id: "adm-1",
        actor_display_name: "Super Admin",
        actor_email: "superadmin@ragbait.internal",
        target_type: "kb_document",
        target_id: "doc-999",
        payload: { reason: "DMCA takedown notice verified", title: "Deleted Document" },
        created_at: "2026-02-01T14:00:00Z",
      },
    ],
    total: 1,
    limit: 25,
    offset: 0,
  };

  it("renders audit log entries with action badges and payload details", async () => {
    vi.mocked(adminApi.getAdminAuditLogs).mockResolvedValueOnce(mockAudit);

    render(<KnowledgeBaseAuditPage />);

    await waitFor(() => {
      expect(screen.getByText("Knowledge Base Audit Activity")).toBeInTheDocument();
      expect(screen.getByText("kb.document.delete_permanent")).toBeInTheDocument();
      expect(screen.getByText("Super Admin")).toBeInTheDocument();
      expect(screen.getByText(/DMCA takedown notice verified/)).toBeInTheDocument();
    });
  });
});
