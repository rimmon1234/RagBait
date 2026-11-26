import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeBaseCataloguePage from "@/app/(protected)/knowledge-base/page";
import SubjectDetailPage from "@/app/(protected)/knowledge-base/[subjectSlug]/page";
import TopicDocumentsPage from "@/app/(protected)/knowledge-base/[subjectSlug]/[topicSlug]/page";
import * as api from "@/lib/api";
import type {
  DocumentListItem,
  SubjectRead,
  TopicRead,
} from "@/lib/knowledge-base/knowledge-base-types";
import type { UserProfile } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
  ApiError: class extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
  }),
  useParams: () => ({
    subjectSlug: "dbms",
    topicSlug: "sql-queries",
  }),
}));

const mockSubjects: SubjectRead[] = [
  {
    id: "sub-1",
    name: "Database Management Systems",
    slug: "dbms",
    description: "Relational models, SQL, indexing, and transactions",
    icon_name: "database",
    display_order: 1,
    is_active: true,
    topic_count: 4,
    document_count: 10,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
  {
    id: "sub-2",
    name: "Operating Systems",
    slug: "os",
    description: "Kernel, processes, threads, concurrency, and virtual memory",
    icon_name: "cpu",
    display_order: 2,
    is_active: true,
    topic_count: 6,
    document_count: 15,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

const mockTopics: TopicRead[] = [
  {
    id: "top-1",
    subject_id: "sub-1",
    name: "SQL Queries & Aggregations",
    slug: "sql-queries",
    description: "SELECT, JOINs, GROUP BY, subqueries",
    display_order: 1,
    is_active: true,
    document_count: 5,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

const mockDocuments: DocumentListItem[] = [
  {
    id: "doc-1",
    subject_id: "sub-1",
    topic_id: "top-1",
    uploader_id: "user-1",
    uploader_display_name: "Alice",
    title: "SQL Joins and Subqueries Cheatsheet",
    original_filename: "sql_cheatsheet.pdf",
    mime_type: "application/pdf",
    file_size_bytes: 1048576,
    upload_status: "uploaded",
    extraction_status: "completed",
    total_pages: 5,
    processed_pages: 5,
    pages_with_text: 5,
    created_at: "2026-02-01T12:00:00Z",
    updated_at: "2026-02-01T12:00:00Z",
  },
  {
    id: "doc-2",
    subject_id: "sub-1",
    topic_id: "top-1",
    uploader_id: "user-2",
    uploader_display_name: "Bob",
    title: "Handwritten Class Notes Unit 2",
    original_filename: "handwritten.pdf",
    mime_type: "application/pdf",
    file_size_bytes: 5242880,
    upload_status: "uploaded",
    extraction_status: "completed_no_text",
    total_pages: 12,
    processed_pages: 12,
    pages_with_text: 0,
    created_at: "2026-02-02T12:00:00Z",
    updated_at: "2026-02-02T12:00:00Z",
  },
];

const mockProfile: UserProfile = {
  id: "user-1",
  email: "alice@univ.edu",
  display_name: "Alice",
  role: "user",
  approval_status: "approved",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("Knowledge Base Pages Integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders catalogue page with subjects and filters by search query", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      throw new Error(`Unhandled url: ${url}`);
    });

    render(<KnowledgeBaseCataloguePage />);

    expect(screen.getByText("Knowledge Base")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Database Management Systems")).toBeInTheDocument();
      expect(screen.getByText("Operating Systems")).toBeInTheDocument();
    });

    // Filter by search
    const searchInput = screen.getByPlaceholderText(/Search subjects/i);
    fireEvent.change(searchInput, { target: { value: "Operating" } });

    expect(screen.getByText("Operating Systems")).toBeInTheDocument();
    expect(screen.queryByText("Database Management Systems")).not.toBeInTheDocument();
  });

  it("renders subject detail page with breadcrumbs and topics", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/kb/subjects/sub-1/topics")) {
        return mockTopics;
      }
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      throw new Error(`Unhandled url: ${url}`);
    });

    render(<SubjectDetailPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Database Management Systems" })).toBeInTheDocument();
      expect(screen.getByText("SQL Queries & Aggregations")).toBeInTheDocument();
      expect(screen.getByText(/5 docs/i)).toBeInTheDocument();
    });
  });

  it("renders topic documents page with document list, status badges, and preview drawer", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me")) {
        return mockProfile;
      }
      if (url.includes("/api/v1/kb/subjects/sub-1/topics")) {
        return mockTopics;
      }
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      if (url.includes("/api/v1/kb/topics/top-1/documents")) {
        return {
          items: mockDocuments,
          total: 2,
          limit: 20,
          offset: 0,
        };
      }
      if (url.includes("/api/v1/kb/documents/doc-1/pages")) {
        return {
          items: [
            {
              id: "page-1",
              document_id: "doc-1",
              page_number: 1,
              raw_text: "INNER JOIN returns matching rows across both relations.",
              character_count: 55,
              word_count: 8,
              has_text: true,
              created_at: "2026-02-01T12:00:00Z",
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
        };
      }
      throw new Error(`Unhandled url: ${url}`);
    });

    render(<TopicDocumentsPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "SQL Queries & Aggregations" })).toBeInTheDocument();
      expect(screen.getByText("SQL Joins and Subqueries Cheatsheet")).toBeInTheDocument();
      expect(screen.getByText("Handwritten Class Notes Unit 2")).toBeInTheDocument();
    });

    // Check status badges
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Scanned note")).toBeInTheDocument();

    // Verify previewing the scanned note (doc-2, newest date)
    const previewButtons = screen.getAllByRole("button", { name: /Preview/i });
    fireEvent.click(previewButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/Scanned \/ Non-extractable Document/i)).toBeInTheDocument();
    });

    // Close preview drawer
    const closeBtn = screen.getByRole("button", { name: /Close preview/i });
    fireEvent.click(closeBtn);

    // Verify previewing the text document (doc-1)
    fireEvent.click(previewButtons[1]);

    await waitFor(() => {
      expect(screen.getByText(/INNER JOIN returns matching rows across both relations/i)).toBeInTheDocument();
    });
  });

  it("hides Upload Material and Manage Subjects buttons for ordinary students and shows them for admins", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me")) {
        return mockProfile; // role: "user"
      }
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      throw new Error(`Unhandled url: ${url}`);
    });

    const { unmount } = render(<KnowledgeBaseCataloguePage />);
    await waitFor(() => {
      expect(screen.getByText("Database Management Systems")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /Upload Material/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Manage \/ Add Subjects/i })).not.toBeInTheDocument();
    unmount();

    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me")) {
        return { ...mockProfile, role: "admin" };
      }
      if (url.includes("/api/v1/kb/subjects")) {
        return mockSubjects;
      }
      throw new Error(`Unhandled url: ${url}`);
    });

    render(<KnowledgeBaseCataloguePage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Upload Material/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Manage \/ Add Subjects/i })).toBeInTheDocument();
    });
  });
});
