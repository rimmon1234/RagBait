import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdminOverviewCards } from "@/components/knowledge-base/admin/AdminOverviewCards";
import { AdminConfirmModal } from "@/components/knowledge-base/admin/AdminConfirmModal";
import { SubjectFormModal } from "@/components/knowledge-base/admin/SubjectFormModal";
import { TopicFormModal } from "@/components/knowledge-base/admin/TopicFormModal";
import { DocumentModerationDrawer } from "@/components/knowledge-base/admin/DocumentModerationDrawer";
import { JobDetailDrawer } from "@/components/knowledge-base/admin/JobDetailDrawer";
import type {
  AdminDocumentDetail,
  AdminJobDetail,
  AdminOverviewMetrics,
  AdminSubjectItem,
} from "@/lib/knowledge-base/knowledge-base-admin-types";
import * as kbApi from "@/lib/knowledge-base/knowledge-base-api";

vi.mock("@/lib/knowledge-base/knowledge-base-api", () => ({
  getDownloadUrl: vi.fn(),
  getDocumentPages: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 }),
}));

describe("AdminOverviewCards", () => {
  const mockMetrics: AdminOverviewMetrics = {
    active_subjects_count: 8,
    total_subjects_count: 10,
    active_topics_count: 24,
    total_topics_count: 30,
    total_documents_count: 150,
    archived_documents_count: 12,
    queued_jobs_count: 3,
    processing_jobs_count: 2,
    failed_jobs_count: 4,
    completed_jobs_count: 141,
    recent_documents: [],
    recent_actions: [],
  };

  it("renders all 6 metric cards with accurate counts", () => {
    render(<AdminOverviewCards metrics={mockMetrics} />);

    expect(screen.getByText("Active Subjects")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("10 total subjects")).toBeInTheDocument();

    expect(screen.getByText("Active Topics")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    expect(screen.getByText("30 total topics")).toBeInTheDocument();

    expect(screen.getByText("Uploaded Documents")).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("12 archived")).toBeInTheDocument();

    expect(screen.getByText("Extraction Queue")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("3 queued, 2 running")).toBeInTheDocument();

    expect(screen.getByText("Failed Jobs")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();

    expect(screen.getByText("Completed Extractions")).toBeInTheDocument();
    expect(screen.getByText("141")).toBeInTheDocument();
  });
});

describe("AdminConfirmModal", () => {
  it("does not render when isOpen is false", () => {
    const { container } = render(
      <AdminConfirmModal
        isOpen={false}
        title="Confirm Action"
        description="Are you sure?"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders modal and enforces minimum 3 characters when requireReason is true", async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <AdminConfirmModal
        isOpen={true}
        title="Archive Document"
        description="Please confirm archival."
        requireReason={true}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    );

    expect(screen.getByText("Archive Document")).toBeInTheDocument();
    const confirmBtn = screen.getByRole("button", { name: "Confirm" });

    // Submit with empty reason
    fireEvent.click(confirmBtn);
    expect(
      screen.getByText("A moderation reason of at least 3 characters is required.")
    ).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();

    // Type a valid reason
    const textarea = screen.getByPlaceholderText("Provide a moderation reason...");
    fireEvent.change(textarea, { target: { value: "Violates content policy" } });

    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledWith("Violates content policy");
    });
  });

  it("closes on Escape key press", () => {
    const onClose = vi.fn();
    render(
      <AdminConfirmModal
        isOpen={true}
        title="Cancel Test"
        description="Escape test"
        onConfirm={vi.fn()}
        onClose={onClose}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("SubjectFormModal", () => {
  it("creates a new subject and auto-generates slug", async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();

    render(
      <SubjectFormModal
        isOpen={true}
        onSave={onSave}
        onClose={onClose}
      />
    );

    expect(screen.getByText("Create Academic Subject")).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/Subject Name/i);
    fireEvent.change(nameInput, { target: { value: "Cloud Computing & DevOps" } });

    const slugInput = screen.getByLabelText(/URL Slug/i) as HTMLInputElement;
    expect(slugInput.value).toBe("cloud-computing-devops");

    const submitBtn = screen.getByRole("button", { name: "Create Subject" });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        name: "Cloud Computing & DevOps",
        slug: "cloud-computing-devops",
        description: null,
        icon_name: "BookOpen",
        display_order: 0,
      });
    });
  });

  it("pre-fills existing subject data in edit mode", async () => {
    const mockSubject: AdminSubjectItem = {
      id: "sub-1",
      name: "Existing Subject",
      slug: "existing-slug",
      description: "Existing Description",
      icon_name: "Database",
      display_order: 5,
      is_active: true,
      topic_count: 2,
      document_count: 8,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-02-01T00:00:00Z",
    };

    const onSave = vi.fn();

    render(
      <SubjectFormModal
        isOpen={true}
        subject={mockSubject}
        onSave={onSave}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText("Edit Academic Subject")).toBeInTheDocument();
    expect((screen.getByLabelText(/Subject Name/i) as HTMLInputElement).value).toBe(
      "Existing Subject"
    );
    expect((screen.getByLabelText(/URL Slug/i) as HTMLInputElement).value).toBe("existing-slug");

    const saveBtn = screen.getByRole("button", { name: "Update Subject" });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        name: "Existing Subject",
        slug: "existing-slug",
        description: "Existing Description",
        icon_name: "Database",
        display_order: 5,
      });
    });
  });
});

describe("TopicFormModal", () => {
  it("renders with parent subject context and generates valid topic slug", async () => {
    const onSave = vi.fn();

    render(
      <TopicFormModal
        isOpen={true}
        subjectName="Data Structures & Algorithms"
        onSave={onSave}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(/Data Structures & Algorithms/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Create Topic" })).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/Topic Name/i);
    fireEvent.change(nameInput, { target: { value: "Binary Search Trees" } });

    const slugInput = screen.getByLabelText(/URL Slug/i) as HTMLInputElement;
    expect(slugInput.value).toBe("binary-search-trees");

    const submitBtn = screen.getByRole("button", { name: "Create Topic" });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        name: "Binary Search Trees",
        slug: "binary-search-trees",
        description: null,
        display_order: 0,
      });
    });
  });
});

describe("DocumentModerationDrawer", () => {
  const mockDoc: AdminDocumentDetail = {
    id: "doc-12345",
    subject_id: "sub-1",
    subject_name: "DBMS",
    topic_id: "top-1",
    topic_name: "SQL",
    uploader_id: "user-1",
    uploader_display_name: "Student Alice",
    uploader_email: "alice@example.com",
    title: "SQL Query Optimization Notes",
    original_filename: "sql_notes.pdf",
    mime_type: "application/pdf",
    file_size_bytes: 2097152,
    storage_path: "documents/doc-12345/source.pdf",
    sha256_checksum: "abcdef1234567890",
    error_code: null,
    error_message: null,
    upload_expires_at: null,
    upload_status: "uploaded",
    extraction_status: "completed",
    is_archived: false,
    total_pages: 12,
    processed_pages: 12,
    pages_with_text: 12,
    created_at: "2026-02-01T12:00:00Z",
    updated_at: "2026-02-01T12:00:00Z",
  };

  it("renders document metadata and allows opening original download", async () => {
    const onArchive = vi.fn();
    const onRestore = vi.fn();
    const onDelete = vi.fn();
    const onClose = vi.fn();

    vi.mocked(kbApi.getDownloadUrl).mockResolvedValueOnce({
      download_url: "https://storage.supabase.co/signed-url",
      expires_in: 300,
      original_filename: "sql_notes.pdf",
      mime_type: "application/pdf",
    });
    const windowOpenSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    render(
      <DocumentModerationDrawer
        isOpen={true}
        document={mockDoc}
        onArchive={onArchive}
        onRestore={onRestore}
        onDelete={onDelete}
        onClose={onClose}
      />
    );

    expect(screen.getByText("SQL Query Optimization Notes")).toBeInTheDocument();
    expect(screen.getByText("Student Alice (alice@example.com)")).toBeInTheDocument();
    expect(screen.getByText(/2.0 MiB/)).toBeInTheDocument();

    const downloadBtn = screen.getByRole("button", { name: /Download/i });
    fireEvent.click(downloadBtn);

    await waitFor(() => {
      expect(kbApi.getDownloadUrl).toHaveBeenCalledWith("doc-12345");
      expect(windowOpenSpy).toHaveBeenCalledWith("https://storage.supabase.co/signed-url", "_blank");
    });

    const archiveBtn = screen.getByRole("button", { name: "Archive" });
    fireEvent.click(archiveBtn);
    expect(onArchive).toHaveBeenCalledWith(mockDoc);

    const deleteBtn = screen.getByRole("button", { name: "Delete" });
    fireEvent.click(deleteBtn);
    expect(onDelete).toHaveBeenCalledWith(mockDoc);

    windowOpenSpy.mockRestore();
  });
});

describe("JobDetailDrawer", () => {
  const mockJob: AdminJobDetail = {
    id: "job-9999",
    document_id: "doc-12345",
    document_title: "SQL Query Optimization Notes",
    subject_id: "sub-1",
    subject_name: "DBMS",
    topic_id: "top-1",
    topic_name: "SQL",
    uploader_display_name: "Student Alice",
    status: "failed",
    attempts: 1,
    max_attempts: 3,
    scheduled_at: "2026-02-01T12:00:00Z",
    locked_until: null,
    error_code: "CORRUPTED_PDF",
    error_message: "PDF header corrupted at byte 0",
    retry_count: 1,
    retry_of_job_id: "job-8888",
    is_retryable: true,
    file_size_bytes: 2097152,
    mime_type: "application/pdf",
    document_upload_status: "uploaded",
    document_is_archived: false,
    retry_eligibility_reason: "Eligible for manual retry (attempt 1/2).",
    created_at: "2026-02-01T12:00:00Z",
    updated_at: "2026-02-01T12:01:10Z",
  };

  it("renders sanitized job details and triggers retry callback when retry eligible", () => {
    const onRetry = vi.fn();
    const onClose = vi.fn();

    render(
      <JobDetailDrawer
        isOpen={true}
        job={mockJob}
        onRetry={onRetry}
        onClose={onClose}
      />
    );

    expect(screen.getByText(/Job job-9999/i)).toBeInTheDocument();
    expect(screen.getByText("Document: SQL Query Optimization Notes")).toBeInTheDocument();
    expect(screen.getByText(/CORRUPTED_PDF/)).toBeInTheDocument();
    expect(screen.getByText("PDF header corrupted at byte 0")).toBeInTheDocument();
    expect(screen.getByText("1 (max 2)")).toBeInTheDocument();
    expect(screen.getByText("job-8888")).toBeInTheDocument();

    const retryBtn = screen.getByRole("button", { name: /Retry Extraction/i });
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledWith(mockJob);
  });

  it("disables retry button when job is not retry eligible", () => {
    const nonRetryableJob: AdminJobDetail = {
      ...mockJob,
      is_retryable: false,
      retry_count: 2,
      retry_eligibility_reason: "Maximum retry limit (2) reached.",
    };

    render(
      <JobDetailDrawer
        isOpen={true}
        job={nonRetryableJob}
        onRetry={vi.fn()}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText("Maximum retry limit (2) reached.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry Extraction/i })).toBeNull();
  });
});
