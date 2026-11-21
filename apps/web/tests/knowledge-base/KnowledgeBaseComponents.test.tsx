import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";
import { DocumentPageViewer } from "@/components/knowledge-base/DocumentPageViewer";
import { UploadDropzone } from "@/components/knowledge-base/UploadDropzone";
import { DocumentRow } from "@/components/knowledge-base/DocumentRow";
import type { DocumentListItem, DocumentPageItem } from "@/lib/knowledge-base/knowledge-base-types";
import type { UserProfile } from "@/types";

describe("ExtractionStatusBadge", () => {
  it("renders correct badge for pending upload", () => {
    render(<ExtractionStatusBadge uploadStatus="pending_upload" extractionStatus="pending" />);
    expect(screen.getByText("Upload pending")).toBeInTheDocument();
  });

  it("renders correct badge for upload failed", () => {
    render(<ExtractionStatusBadge uploadStatus="upload_failed" extractionStatus="pending" />);
    expect(screen.getByText("Upload failed")).toBeInTheDocument();
  });

  it("renders awaiting extraction when uploaded and pending", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="pending" />);
    expect(screen.getByText("Awaiting extraction")).toBeInTheDocument();
  });

  it("renders queued badge", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="queued" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("renders processing badge", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="processing" />);
    expect(screen.getByText("Processing")).toBeInTheDocument();
  });

  it("renders ready badge on completed", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="completed" />);
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("renders scanned note badge on completed_no_text", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="completed_no_text" />);
    expect(screen.getByText("Scanned note")).toBeInTheDocument();
  });

  it("renders extraction failed badge on failed", () => {
    render(<ExtractionStatusBadge uploadStatus="uploaded" extractionStatus="failed" />);
    expect(screen.getByText("Extraction failed")).toBeInTheDocument();
  });

  it("renders deleted badge", () => {
    render(<ExtractionStatusBadge uploadStatus="deleted" extractionStatus="deleted" />);
    expect(screen.getByText("Deleted")).toBeInTheDocument();
  });
});

describe("DocumentPageViewer", () => {
  it("renders scanned note warning with deterministic explanation and download recommendation", () => {
    render(
      <DocumentPageViewer
        pages={[]}
        currentPageIndex={0}
        onPageChange={() => {}}
        isScannedNote={true}
      />
    );

    expect(screen.getByText(/Scanned \/ Non-extractable Document/i)).toBeInTheDocument();
    expect(screen.getByText(/deterministic text extraction without OCR/i)).toBeInTheDocument();
    expect(screen.getByText(/download and inspect the original file/i)).toBeInTheDocument();
  });

  it("renders extracted plain text safely without innerHTML", () => {
    const mockPages: DocumentPageItem[] = [
      {
        id: "p-1",
        document_id: "doc-1",
        page_number: 1,
        raw_text: "Relational Algebra: σ_salary>50000(Instructor) ⨝ Department\n<script>alert('xss')</script>",
        character_count: 85,
        word_count: 8,
        has_text: true,
        created_at: "2026-02-01T00:00:00Z",
      },
      {
        id: "p-2",
        document_id: "doc-1",
        page_number: 2,
        raw_text: "Page 2 content",
        character_count: 14,
        word_count: 3,
        has_text: true,
        created_at: "2026-02-01T00:00:00Z",
      },
    ];

    render(
      <DocumentPageViewer
        pages={mockPages}
        currentPageIndex={0}
        onPageChange={() => {}}
        isScannedNote={false}
      />
    );

    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/8 words/i)).toBeInTheDocument();
    expect(screen.getByText(/85 characters/i)).toBeInTheDocument();

    // Verify text renders literally as text content, not interpreted as HTML
    const preElement = screen.getByText(/Relational Algebra:/i);
    expect(preElement.tagName).toBe("PRE");
    expect(preElement.textContent).toContain("<script>alert('xss')</script>");
  });
});

describe("UploadDropzone", () => {
  it("rejects file larger than 50 MiB with warning", () => {
    const onSelect = vi.fn();
    render(<UploadDropzone onFileSelect={onSelect} selectedFile={null} onClear={() => {}} />);

    // Create 51 MiB dummy file
    const largeFile = new File(["a".repeat(100)], "oversized.pdf", { type: "application/pdf" });
    Object.defineProperty(largeFile, "size", { value: 55_000_000 });

    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [largeFile] } });

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByText(/exceeds the maximum allowed limit of 50 MB/i)).toBeInTheDocument();
  });

  it("rejects unsupported file extensions", () => {
    const onSelect = vi.fn();
    render(<UploadDropzone onFileSelect={onSelect} selectedFile={null} onClear={() => {}} />);

    const badFile = new File(["test"], "malicious.exe", { type: "application/x-msdownload" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [badFile] } });

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByText(/Only PDF \(\.pdf\) and plain text \(\.txt\) files are supported/i)).toBeInTheDocument();
  });

  it("accepts valid PDF file", () => {
    const onSelect = vi.fn();
    render(<UploadDropzone onFileSelect={onSelect} selectedFile={null} onClear={() => {}} />);

    const validPdf = new File(["%PDF-1.4..."], "notes.pdf", { type: "application/pdf" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [validPdf] } });

    expect(onSelect).toHaveBeenCalledWith(validPdf);
  });
});

describe("DocumentRow", () => {
  const sampleDoc: DocumentListItem = {
    id: "doc-100",
    subject_id: "sub-1",
    topic_id: "top-1",
    uploader_id: "user-owner",
    uploader_display_name: "Alice Student",
    title: "Operating Systems Virtual Memory Notes",
    original_filename: "virtual_memory.pdf",
    mime_type: "application/pdf",
    file_size_bytes: 2048576,
    upload_status: "uploaded",
    extraction_status: "completed",
    total_pages: 14,
    processed_pages: 14,
    pages_with_text: 14,
    created_at: "2026-02-01T10:00:00Z",
    updated_at: "2026-02-01T10:00:00Z",
  };

  const ownerUser: UserProfile = {
    id: "user-owner",
    email: "alice@univ.edu",
    display_name: "Alice Student",
    role: "user",
    approval_status: "approved",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };

  const otherUser: UserProfile = {
    id: "user-other",
    email: "bob@univ.edu",
    display_name: "Bob Peer",
    role: "user",
    approval_status: "approved",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };

  it("shows preview button when document is completed", () => {
    const onPreview = vi.fn();
    render(
      <table>
        <tbody>
          <DocumentRow
            document={sampleDoc}
            currentUser={otherUser}
            onPreview={onPreview}
            onDownload={vi.fn()}
            onDelete={vi.fn()}
          />
        </tbody>
      </table>
    );

    const previewBtn = screen.getByRole("button", { name: /Preview/i });
    expect(previewBtn).toBeInTheDocument();
    fireEvent.click(previewBtn);
    expect(onPreview).toHaveBeenCalledWith(sampleDoc);
  });

  it("allows owner to see delete button and requires confirmation", async () => {
    const onDelete = vi.fn();
    render(
      <table>
        <tbody>
          <DocumentRow
            document={sampleDoc}
            currentUser={ownerUser}
            onPreview={vi.fn()}
            onDownload={vi.fn()}
            onDelete={onDelete}
          />
        </tbody>
      </table>
    );

    const deleteIconBtn = screen.getByTitle("Delete study document");
    expect(deleteIconBtn).toBeInTheDocument();

    // Click trash button -> shows confirm button
    fireEvent.click(deleteIconBtn);
    const confirmBtn = screen.getByRole("button", { name: /Confirm/i });
    expect(confirmBtn).toBeInTheDocument();

    fireEvent.click(confirmBtn);
    expect(onDelete).toHaveBeenCalledWith(sampleDoc);
  });

  it("hides delete button for non-owner students", () => {
    render(
      <table>
        <tbody>
          <DocumentRow
            document={sampleDoc}
            currentUser={otherUser}
            onPreview={vi.fn()}
            onDownload={vi.fn()}
            onDelete={vi.fn()}
          />
        </tbody>
      </table>
    );

    expect(screen.queryByTitle("Delete study document")).not.toBeInTheDocument();
  });
});
