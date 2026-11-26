import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatLandingPage from "@/app/(protected)/chat/page";
import DocumentViewerPage from "@/app/(protected)/knowledge-base/document/[id]/page";
import * as chatClient from "@/lib/chat-client";
import * as kbApi from "@/lib/knowledge-base/knowledge-base-api";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";

const pushMock = vi.fn();
const searchParamsMap = new Map<string, string>();
let mockRouteParams: Record<string, string> = { id: "doc-1" };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => ({
    get: (k: string) => searchParamsMap.get(k) ?? null,
  }),
  useParams: () => mockRouteParams,
}));

vi.mock("@/lib/knowledge-base/useKnowledgeSubjects", () => ({
  useKnowledgeSubjects: vi.fn(),
}));

vi.mock("@/lib/chat-client", async (importOriginal) => {
  const actual = await importOriginal<typeof chatClient>();
  return {
    ...actual,
    getAccessState: vi.fn(),
    getChatEntitlementState: vi.fn(),
    listConversations: vi.fn(),
  };
});

vi.mock("@/lib/knowledge-base/knowledge-base-api", async (importOriginal) => {
  const actual = await importOriginal<typeof kbApi>();
  return {
    ...actual,
    getDocument: vi.fn(),
    getDocumentPages: vi.fn(),
    getDownloadUrl: vi.fn(),
  };
});

describe("Chat Access Control & Product Rules (/chat)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    searchParamsMap.clear();
    mockRouteParams = {};
    vi.mocked(useKnowledgeSubjects).mockReturnValue({
      subjects: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("blocks pending/rejected/suspended accounts from seeing history or starting chats", async () => {
    vi.mocked(chatClient.getAccessState).mockResolvedValueOnce({
      approval_status: "pending",
      effective_state: "pending_approval",
      rejection_reason: "Awaiting admin review.",
    } as never);

    render(<ChatLandingPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Account Pending Approval");
    });
    expect(chatClient.listConversations).not.toHaveBeenCalled();
  });

  it("blocks approved non-contributors from AI Chat history and generation until qualifying credential is active", async () => {
    vi.mocked(chatClient.getAccessState).mockResolvedValueOnce({
      approval_status: "approved",
      effective_state: "contribution_required",
    } as never);
    vi.mocked(chatClient.getChatEntitlementState).mockResolvedValueOnce({
      eligible: false,
      can_chat: false,
      reason_code: "CHAT_CONTRIBUTION_REQUIRED",
      reason: "Connect an active OpenRouter API key to unlock new chat generation.",
    });

    render(<ChatLandingPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /API Contribution Required to Unlock AI Chat/i })
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Manage API Credentials/i })).toHaveAttribute(
        "href",
        "/credentials"
      );
      expect(screen.getByRole("link", { name: /Browse Knowledge Base/i })).toHaveAttribute(
        "href",
        "/knowledge-base"
      );
    });

    expect(chatClient.listConversations).not.toHaveBeenCalled();
    expect(screen.queryByText("Previous Study Session")).not.toBeInTheDocument();
  });
});

describe("Physical Page Jump Route (/knowledge-base/document/[id]?page=142)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRouteParams = { id: "doc-1" };
    searchParamsMap.set("page", "142");
  });

  it("computes page batch offset and jumps DocumentPageViewer directly to exact physical page", async () => {
    vi.mocked(kbApi.getDocument).mockResolvedValueOnce({
      id: "doc-1",
      title: "Computer Architecture Notes",
      original_filename: "arch.pdf",
      extraction_status: "completed",
      page_count: 180,
      topic_title: "Memory Hierarchy",
      subject_name: "Computer Architecture",
      subject_slug: "ca",
      topic_slug: "memory-hierarchy",
    } as never);

    vi.mocked(kbApi.getDocumentPages).mockResolvedValueOnce({
      total: 180,
      limit: 100,
      offset: 100,
      items: [
        {
          id: "pg-141",
          document_id: "doc-1",
          page_number: 141,
          raw_text: "Page 141 text",
          character_count: 13,
          word_count: 3,
          has_text: true,
          created_at: "2026-03-01T00:00:00Z",
        },
        {
          id: "pg-142",
          document_id: "doc-1",
          page_number: 142,
          raw_text: "Physical page 142 exact content on TLB.",
          character_count: 39,
          word_count: 7,
          has_text: true,
          created_at: "2026-03-01T00:00:00Z",
        },
      ],
    });

    render(<DocumentViewerPage />);

    await waitFor(() => {
      expect(kbApi.getDocumentPages).toHaveBeenCalledWith("doc-1", 100, 100);
      expect(
        screen.getByText("Physical page 142 exact content on TLB.")
      ).toBeInTheDocument();
    });
  });
});
