import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  confirmUpload,
  createUploadIntent,
  deleteDocument,
  getDocument,
  getDocumentPages,
  getDownloadUrl,
  getSubjects,
  getTopicDocuments,
  getTopics,
} from "@/lib/knowledge-base/knowledge-base-api";
import { computeFileSha256, uploadFileToStorage } from "@/lib/knowledge-base/upload-client";
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

describe("Knowledge Base API Client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches active subjects list", async () => {
    const mockSubjects = [
      {
        id: "sub-1",
        name: "DBMS",
        slug: "dbms",
        description: "Database systems",
        icon_name: "database",
        display_order: 1,
        is_active: true,
        topic_count: 5,
        document_count: 12,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ];
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockSubjects);

    const result = await getSubjects();
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/subjects");
    expect(result).toEqual(mockSubjects);
  });

  it("fetches topics for a subject", async () => {
    const mockTopics = [
      {
        id: "top-1",
        subject_id: "sub-1",
        name: "SQL & Relational Algebra",
        slug: "sql-algebra",
        description: "Queries and operators",
        display_order: 1,
        is_active: true,
        document_count: 4,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ];
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockTopics);

    const result = await getTopics("sub-1");
    expect(api.fetchWithAuth).toHaveBeenCalledWith("/api/v1/kb/subjects/sub-1/topics");
    expect(result).toEqual(mockTopics);
  });

  it("fetches topic documents with pagination", async () => {
    const mockDocs = {
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockDocs);

    const result = await getTopicDocuments("top-1", 20, 0);
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/topics/top-1/documents?limit=20&offset=0"
    );
    expect(result).toEqual(mockDocs);
  });

  it("creates upload intent", async () => {
    const intentResponse = {
      document_id: "doc-123",
      storage_path: "docs/doc-123.pdf",
      signed_upload_url: "http://storage.local/upload",
      token: "tok-abc",
      upload_expires_at: "2026-02-01T01:00:00Z",
      max_file_size_bytes: 52428800,
      supported_methods: ["PUT"],
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(intentResponse);

    const result = await createUploadIntent({
      subject_id: "sub-1",
      topic_id: "top-1",
      title: "Unit 1 Notes",
      original_filename: "unit1.pdf",
      mime_type: "application/pdf",
      file_size_bytes: 1024,
    });

    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/documents/upload-intent",
      expect.objectContaining({
        method: "POST",
      })
    );
    expect(result).toEqual(intentResponse);
  });

  it("confirms document upload", async () => {
    const confirmResponse = {
      document: {
        id: "doc-123",
        subject_id: "sub-1",
        topic_id: "top-1",
        title: "Unit 1 Notes",
        original_filename: "unit1.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1024,
        upload_status: "uploaded",
        extraction_status: "pending",
        total_pages: 0,
        processed_pages: 0,
        pages_with_text: 0,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
      message: "Document upload verified and queued for extraction.",
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(confirmResponse);

    const result = await confirmUpload("doc-123", {
      sha256_checksum: "abcd1234",
      file_size_bytes: 1024,
    });

    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/documents/doc-123/confirm-upload",
      expect.objectContaining({
        method: "POST",
      })
    );
    expect(result).toEqual(confirmResponse);
  });

  it("fetches signed download URL", async () => {
    const downloadRes = {
      download_url: "https://storage.local/download?sign=xyz",
      expires_in: 300,
      original_filename: "unit1.pdf",
      mime_type: "application/pdf",
    };
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(downloadRes);

    const result = await getDownloadUrl("doc-123");
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/documents/doc-123/download-url"
    );
    expect(result).toEqual(downloadRes);
  });

  it("deletes a document", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({});
    await deleteDocument("doc-123");
    expect(api.fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/kb/documents/doc-123",
      expect.objectContaining({
        method: "DELETE",
      })
    );
  });
});

describe("Storage Upload Client", () => {
  it("computes sha256 checksum of a file", async () => {
    const file = new File(["test document content"], "test.txt", { type: "text/plain" });
    const hash = await computeFileSha256(file);
    expect(typeof hash).toBe("string");
    expect(hash.length).toBe(64); // 64-char hex string
  });

  it("uploads file to storage via XMLHttpRequest and tracks progress", async () => {
    const file = new File(["sample content"], "sample.pdf", { type: "application/pdf" });
    const progressEvents: number[] = [];

    // Mock XMLHttpRequest
    const originalXHR = globalThis.XMLHttpRequest;
    const mockXHR = {
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      send: vi.fn(function (this: any) {
        if (this.upload && this.upload.onprogress) {
          this.upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 });
          this.upload.onprogress({ lengthComputable: true, loaded: 100, total: 100 });
        }
        this.status = 200;
        if (this.onload) this.onload();
      }),
      upload: {
        onprogress: null as any,
      },
      status: 200,
      statusText: "OK",
      onload: null as any,
      onerror: null as any,
    };

    globalThis.XMLHttpRequest = vi.fn(() => mockXHR as any) as any;

    try {
      await uploadFileToStorage(
        "http://storage.local/upload-url",
        file,
        "application/pdf",
        (pct) => progressEvents.push(pct)
      );

      expect(mockXHR.open).toHaveBeenCalledWith("PUT", "http://storage.local/upload-url", true);
      expect(mockXHR.setRequestHeader).toHaveBeenCalledWith("Content-Type", "application/pdf");
      expect(mockXHR.send).toHaveBeenCalledWith(file);
      expect(progressEvents).toEqual([50, 100]);
    } finally {
      globalThis.XMLHttpRequest = originalXHR;
    }
  });

  it("handles storage upload failure with descriptive error", async () => {
    const file = new File(["content"], "fail.pdf", { type: "application/pdf" });

    const originalXHR = globalThis.XMLHttpRequest;
    const mockXHR = {
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      send: vi.fn(function (this: any) {
        this.status = 403;
        this.statusText = "Forbidden";
        if (this.onload) this.onload();
      }),
      upload: {},
      status: 403,
      statusText: "Forbidden",
      onload: null as any,
      onerror: null as any,
    };

    globalThis.XMLHttpRequest = vi.fn(() => mockXHR as any) as any;

    try {
      await expect(
        uploadFileToStorage("http://storage.local/upload-fail", file, "application/pdf")
      ).rejects.toThrow(/Storage upload failed with status 403/);
    } finally {
      globalThis.XMLHttpRequest = originalXHR;
    }
  });
});
