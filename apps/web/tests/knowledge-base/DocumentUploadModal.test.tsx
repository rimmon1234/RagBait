import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";
import * as api from "@/lib/api";
import * as uploadClient from "@/lib/knowledge-base/upload-client";
import type { SubjectRead, TopicRead } from "@/lib/knowledge-base/knowledge-base-types";

vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
  ApiError: class extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

vi.mock("@/lib/knowledge-base/upload-client", () => ({
  computeFileSha256: vi.fn().mockResolvedValue("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
  uploadFileToStorage: vi.fn().mockImplementation((url, file, mime, onProgress) => {
    if (onProgress) onProgress(100);
    return Promise.resolve();
  }),
}));

const mockSubjects: SubjectRead[] = [
  {
    id: "sub-1",
    name: "Database Systems",
    slug: "dbms",
    description: "Database theory",
    icon_name: "database",
    display_order: 1,
    is_active: true,
    topic_count: 2,
    document_count: 5,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

const mockTopics: TopicRead[] = [
  {
    id: "top-1",
    subject_id: "sub-1",
    name: "Indexing & B+ Trees",
    slug: "indexing",
    description: "Search trees and hashes",
    display_order: 1,
    is_active: true,
    document_count: 2,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

describe("DocumentUploadModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("orchestrates upload intent, storage put, and backend confirmation", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string, options?: any) => {
      if (url.includes("/api/v1/kb/subjects/sub-1/topics")) {
        return mockTopics;
      }
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      if (url.includes("/api/v1/kb/documents/upload-intent")) {
        return {
          document_id: "doc-999",
          storage_path: "materials/sub-1/top-1/doc-999.pdf",
          signed_upload_url: "http://storage.local/upload/signed-token",
          token: "signed-token",
          upload_expires_at: "2026-02-01T15:00:00Z",
          max_file_size_bytes: 52428800,
          supported_methods: ["PUT"],
        };
      }
      if (url.includes("/api/v1/kb/documents/doc-999/confirm-upload")) {
        return {
          document: {
            id: "doc-999",
            subject_id: "sub-1",
            topic_id: "top-1",
            title: "B+ Trees Deep Dive",
            original_filename: "b_trees.pdf",
            mime_type: "application/pdf",
            file_size_bytes: 1024,
            upload_status: "uploaded",
            extraction_status: "pending",
            total_pages: 0,
            processed_pages: 0,
            pages_with_text: 0,
            created_at: "2026-02-01T14:00:00Z",
            updated_at: "2026-02-01T14:00:00Z",
          },
          message: "Document upload verified and queued for extraction.",
        };
      }
      throw new Error(`Unhandled url ${url}`);
    });

    const onSuccess = vi.fn();
    const onClose = vi.fn();

    render(
      <DocumentUploadModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        initialSubjectId="sub-1"
        initialTopicId="top-1"
        availableSubjects={mockSubjects}
      />
    );

    expect(screen.getByRole("heading", { name: "Upload Study Material" })).toBeInTheDocument();

    // Select file
    const sampleFile = new File(["%PDF-1.4 binary content"], "b_trees.pdf", {
      type: "application/pdf",
    });
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [sampleFile] } });

    // Verify title auto-populated
    await waitFor(() => {
      const titleInput = screen.getByLabelText("Document Title") as HTMLInputElement;
      expect(titleInput.value).toBe("B trees");
    });

    // Customise title
    const titleInput = screen.getByLabelText("Document Title");
    fireEvent.change(titleInput, { target: { value: "B+ Trees Deep Dive" } });

    // Submit form
    const submitBtn = screen.getByRole("button", { name: /Upload & Queue/i });
    fireEvent.click(submitBtn);

    // Verify intent call and confirm call
    await waitFor(() => {
      expect(uploadClient.computeFileSha256).toHaveBeenCalled();
      expect(uploadClient.uploadFileToStorage).toHaveBeenCalledWith(
        "http://storage.local/upload/signed-token",
        sampleFile,
        "application/pdf",
        expect.any(Function)
      );
    });

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });
});
