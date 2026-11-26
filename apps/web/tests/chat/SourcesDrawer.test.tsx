import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SourcesDrawer } from "@/components/chat/SourcesDrawer";
import * as chatClient from "@/lib/chat-client";
import type { MessageSourceRead } from "@/types/chat";

vi.mock("@/lib/chat-client", async (importOriginal) => {
  const actual = await importOriginal<typeof chatClient>();
  return {
    ...actual,
    getMessageSources: vi.fn(),
  };
});

const citedSource: MessageSourceRead = {
  id: "src-1",
  citation_key: "S1",
  document_id: "doc-1",
  document_title: "Operating Systems Lecture Notes",
  file_type: "application/pdf",
  page_number_start: 142,
  page_number_end: 142,
  excerpt: "Demand paging loads pages from disk only when referenced by a page fault.",
  relevance_score: 0.94,
  included_in_context: true,
  referenced_in_answer: true,
  is_available: true,
  document_viewer_url: "/knowledge-base/document/doc-1?page=142",
};

const consideredUnCitedSource: MessageSourceRead = {
  id: "src-2",
  citation_key: "S2",
  document_id: "doc-2",
  document_title: "Process Scheduling Guide",
  file_type: "application/pdf",
  page_number_start: 18,
  page_number_end: 19,
  excerpt: "Round-robin scheduling assigns a fixed time quantum per process.",
  relevance_score: 0.72,
  included_in_context: true,
  referenced_in_answer: false,
  is_available: true,
  document_viewer_url: "/knowledge-base/document/doc-2?page=18",
};

const unavailableMaskedSource: MessageSourceRead = {
  id: "src-3",
  citation_key: "S3",
  document_id: null,
  document_title: "Source currently unavailable",
  file_type: null,
  page_number_start: 9,
  page_number_end: 9,
  excerpt: "Source text unavailable because the underlying document is moderated or archived.",
  relevance_score: 0.65,
  included_in_context: true,
  referenced_in_answer: true,
  is_available: false,
  document_viewer_url: null,
};

describe("SourcesDrawer Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads authoritative sources, separates Cited vs All Considered, and links directly to exact physical page", async () => {
    vi.mocked(chatClient.getMessageSources).mockResolvedValueOnce({
      message_id: "msg-1",
      conversation_id: "conv-1",
      sources: [citedSource, consideredUnCitedSource, unavailableMaskedSource],
    });

    render(
      <SourcesDrawer
        isOpen={true}
        messageId="msg-1"
        subjectName="Operating Systems"
        focusedCitationKey="S1"
        onClose={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(chatClient.getMessageSources).toHaveBeenCalledWith("msg-1");
      expect(screen.getByText("Operating Systems Lecture Notes")).toBeInTheDocument();
      expect(
        screen.getByText("Demand paging loads pages from disk only when referenced by a page fault.")
      ).toBeInTheDocument();
    });

    // Cited tab should display S1 & unavailable S3, not un-cited S2
    expect(screen.queryByText("Process Scheduling Guide")).not.toBeInTheDocument();

    // Physical page jump link matches exact backend document_viewer_url
    const openLink = screen.getByRole("link", { name: /Open at Page 142/i });
    expect(openLink).toHaveAttribute("href", "/knowledge-base/document/doc-1?page=142");

    // Masked unavailable source shows safe status and no viewer link
    expect(screen.getByText("Source currently unavailable")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Source text unavailable because the underlying document is moderated or archived."
      )
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(1);

    // Switch to All Considered tab reveals S2
    fireEvent.click(screen.getByRole("tab", { name: /All Considered/i }));
    expect(screen.getByText("Process Scheduling Guide")).toBeInTheDocument();
  });

  it("auto-selects All Considered tab when focused citation key is not in Cited tab", async () => {
    vi.mocked(chatClient.getMessageSources).mockResolvedValueOnce({
      message_id: "msg-1",
      conversation_id: "conv-1",
      sources: [citedSource, consideredUnCitedSource],
    });

    render(
      <SourcesDrawer
        isOpen={true}
        messageId="msg-1"
        focusedCitationKey="S2"
        onClose={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Process Scheduling Guide")).toBeInTheDocument();
    });
  });

  it("shows honest provisional warning during streaming without falsely querying completed /sources", () => {
    render(
      <SourcesDrawer
        isOpen={true}
        messageId="temp-asst"
        isProvisional={true}
        provisionalSources={[
          {
            citation_key: "S1",
            document_id: "doc-1",
            document_title: "Draft Candidate Doc",
            page_number_start: 12,
            page_number_end: 13,
            relevance_score: 0.89,
          },
        ]}
        onClose={vi.fn()}
      />
    );

    expect(chatClient.getMessageSources).not.toHaveBeenCalled();
    expect(screen.getByText("Generation in progress")).toBeInTheDocument();
    expect(
      screen.getByText(/Final cited sources and revalidated excerpts will appear here/i)
    ).toBeInTheDocument();
    expect(screen.getByText("Draft Candidate Doc")).toBeInTheDocument();
  });

  it("clears previous message sources on message switch and handles Escape to close", async () => {
    vi.mocked(chatClient.getMessageSources)
      .mockResolvedValueOnce({
        message_id: "msg-1",
        conversation_id: "conv-1",
        sources: [citedSource],
      })
      .mockRejectedValueOnce(new Error("Network error"));

    const onClose = vi.fn();
    const { rerender } = render(
      <SourcesDrawer isOpen={true} messageId="msg-1" onClose={onClose} />
    );

    await waitFor(() => {
      expect(screen.getByText("Operating Systems Lecture Notes")).toBeInTheDocument();
    });

    rerender(<SourcesDrawer isOpen={true} messageId="msg-2" onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeInTheDocument();
    });
    expect(screen.queryByText("Operating Systems Lecture Notes")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("distinguishes external [Wn] web sources cleanly from KB note sources", async () => {
    const webSource: MessageSourceRead = {
      id: "web-1",
      source_type: "web_source",
      citation_key: "W1",
      document_id: null,
      document_title: "Python 3.15 Release Notes",
      page_number_start: null,
      page_number_end: null,
      excerpt: "Official overview of upcoming Python syntax improvements.",
      relevance_score: 0.91,
      included_in_context: true,
      referenced_in_answer: true,
      is_available: true,
      url: "https://docs.python.org/3.15/whatsnew/3.15.html",
      domain: "docs.python.org",
      publisher: "Python Software Foundation",
      published_at: "2026-10-01",
      retrieved_at: "2026-11-26T04:30:00Z",
      snippet_only: true,
    };

    vi.mocked(chatClient.getMessageSources).mockResolvedValueOnce({
      message_id: "msg-web",
      conversation_id: "conv-1",
      sources: [citedSource, webSource],
    });

    render(<SourcesDrawer isOpen={true} messageId="msg-web" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Python 3.15 Release Notes")).toBeInTheDocument();
    });
    expect(screen.getByText("[W1]")).toBeInTheDocument();
    expect(screen.getByText("docs.python.org")).toBeInTheDocument();
    expect(screen.getByText("Search snippet")).toBeInTheDocument();
    const extLink = screen.getByRole("link", { name: /Open Source Website/i });
    expect(extLink).toHaveAttribute("href", "https://docs.python.org/3.15/whatsnew/3.15.html");
  });
});
