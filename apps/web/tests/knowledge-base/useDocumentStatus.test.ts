import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDocumentStatus } from "@/lib/knowledge-base/useDocumentStatus";
import * as api from "@/lib/knowledge-base/knowledge-base-api";
import type { DocumentDetail, DocumentListItem } from "@/lib/knowledge-base/knowledge-base-types";

vi.mock("@/lib/knowledge-base/knowledge-base-api", () => ({
  getDocument: vi.fn(),
}));

describe("useDocumentStatus Hook", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not poll when all documents are in terminal status", () => {
    const docs: DocumentListItem[] = [
      {
        id: "doc-1",
        subject_id: "s-1",
        topic_id: "t-1",
        uploader_id: "u-1",
        uploader_display_name: "Alice",
        title: "Finished Doc",
        original_filename: "doc.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: "completed",
        total_pages: 5,
        processed_pages: 5,
        pages_with_text: 5,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ];

    const onUpdate = vi.fn();
    renderHook(() => useDocumentStatus(docs, onUpdate));

    act(() => {
      vi.advanceTimersByTime(10000);
    });

    expect(api.getDocument).not.toHaveBeenCalled();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("polls active documents with bounded concurrency and updates statuses", async () => {
    const activeDocs: DocumentListItem[] = [
      {
        id: "doc-active-1",
        subject_id: "s-1",
        topic_id: "t-1",
        uploader_id: "u-1",
        uploader_display_name: "Alice",
        title: "Active Doc 1",
        original_filename: "doc1.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: "processing",
        total_pages: 0,
        processed_pages: 0,
        pages_with_text: 0,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
      {
        id: "doc-active-2",
        subject_id: "s-1",
        topic_id: "t-1",
        uploader_id: "u-1",
        uploader_display_name: "Alice",
        title: "Active Doc 2",
        original_filename: "doc2.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: "queued",
        total_pages: 0,
        processed_pages: 0,
        pages_with_text: 0,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
      {
        id: "doc-active-3",
        subject_id: "s-1",
        topic_id: "t-1",
        uploader_id: "u-1",
        uploader_display_name: "Alice",
        title: "Active Doc 3",
        original_filename: "doc3.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: "pending",
        total_pages: 0,
        processed_pages: 0,
        pages_with_text: 0,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
      {
        id: "doc-active-4",
        subject_id: "s-1",
        topic_id: "t-1",
        uploader_id: "u-1",
        uploader_display_name: "Alice",
        title: "Active Doc 4",
        original_filename: "doc4.pdf",
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: "processing",
        total_pages: 0,
        processed_pages: 0,
        pages_with_text: 0,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      },
    ];

    let currentInFlight = 0;
    let maxInFlight = 0;

    vi.mocked(api.getDocument).mockImplementation(async (id: string) => {
      currentInFlight++;
      maxInFlight = Math.max(maxInFlight, currentInFlight);
      // Simulate minor async latency
      await new Promise((r) => setTimeout(r, 10));
      currentInFlight--;

      return {
        id,
        subject_id: "s-1",
        topic_id: "t-1",
        title: `Doc ${id}`,
        original_filename: `${id}.pdf`,
        mime_type: "application/pdf",
        file_size_bytes: 1000,
        upload_status: "uploaded",
        extraction_status: id === "doc-active-1" ? "completed" : "processing",
        total_pages: 4,
        processed_pages: 4,
        pages_with_text: 4,
        created_at: "2026-02-01T00:00:00Z",
        updated_at: "2026-02-01T00:00:00Z",
      } as DocumentDetail;
    });

    const onUpdate = vi.fn();
    renderHook(() => useDocumentStatus(activeDocs, onUpdate));

    // Fast-forward timer to trigger first poll round and async worker completion
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });

    expect(api.getDocument).toHaveBeenCalledTimes(4);
    // Verified: max concurrency is bounded by MAX_CONCURRENT_POLLS = 3
    expect(maxInFlight).toBeLessThanOrEqual(3);
    expect(onUpdate).toHaveBeenCalledTimes(4);
  });

  it("pauses polling when document tab is hidden", async () => {
    const activeDoc: DocumentListItem = {
      id: "doc-active-1",
      subject_id: "s-1",
      topic_id: "t-1",
      uploader_id: "u-1",
      uploader_display_name: "Alice",
      title: "Active Doc 1",
      original_filename: "doc1.pdf",
      mime_type: "application/pdf",
      file_size_bytes: 1000,
      upload_status: "uploaded",
      extraction_status: "processing",
      total_pages: 0,
      processed_pages: 0,
      pages_with_text: 0,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-02-01T00:00:00Z",
    };

    vi.mocked(api.getDocument).mockResolvedValue({
      ...activeDoc,
      extraction_status: "processing",
    } as DocumentDetail);

    // Mock document.visibilityState
    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      configurable: true,
      writable: true,
    });

    const onUpdate = vi.fn();
    renderHook(() => useDocumentStatus([activeDoc], onUpdate));

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });

    // Hidden tab skips polling
    expect(api.getDocument).not.toHaveBeenCalled();

    // Now restore visibility
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
      writable: true,
    });

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });

    expect(api.getDocument).toHaveBeenCalledWith("doc-active-1");
  });

  it("stops polling on component unmount", async () => {
    const activeDoc: DocumentListItem = {
      id: "doc-active-1",
      subject_id: "s-1",
      topic_id: "t-1",
      uploader_id: "u-1",
      uploader_display_name: "Alice",
      title: "Active Doc 1",
      original_filename: "doc1.pdf",
      mime_type: "application/pdf",
      file_size_bytes: 1000,
      upload_status: "uploaded",
      extraction_status: "processing",
      total_pages: 0,
      processed_pages: 0,
      pages_with_text: 0,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-02-01T00:00:00Z",
    };

    const { unmount } = renderHook(() => useDocumentStatus([activeDoc], vi.fn()));
    unmount();

    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });

    expect(api.getDocument).not.toHaveBeenCalled();
  });
});
