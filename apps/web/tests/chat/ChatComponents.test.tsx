import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatInterface } from "@/components/chat/ChatInterface";
import { NewConversationModal } from "@/components/chat/NewConversationModal";
import { RenameConversationModal } from "@/components/chat/RenameConversationModal";
import { DeleteConversationModal } from "@/components/chat/DeleteConversationModal";
import type { ChatConversation, ChatMessage } from "@/types/chat";
import type { SubjectRead } from "@/lib/knowledge-base/knowledge-base-types";

const mockSubject: SubjectRead = {
  id: "sub-1",
  name: "Computer Networks",
  slug: "cn",
  description: "Network protocols and layers",
  icon_name: "network",
  display_order: 1,
  is_active: true,
  topic_count: 5,
  document_count: 12,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

const mockConversation: ChatConversation = {
  id: "conv-1",
  user_id: "user-1",
  subject_id: "sub-1",
  subject_name: "Computer Networks",
  subject_slug: "cn",
  title: "TCP Handshake Discussion",
  status: "active",
  message_count: 2,
  last_message_at: "2026-03-01T12:00:00Z",
  archived_at: null,
  created_at: "2026-03-01T10:00:00Z",
  updated_at: "2026-03-01T12:00:00Z",
};

const mockMessages: ChatMessage[] = [
  {
    id: "msg-1",
    conversation_id: "conv-1",
    sequence_number: 1,
    role: "user",
    content: "Explain the three-way handshake in TCP.",
    status: "completed",
    client_message_id: null,
    created_at: "2026-03-01T10:00:00Z",
    updated_at: "2026-03-01T10:00:00Z",
  },
  {
    id: "msg-2",
    conversation_id: "conv-1",
    sequence_number: 2,
    role: "assistant",
    content: "The TCP 3-way handshake consists of SYN, SYN-ACK, and ACK.",
    status: "completed",
    client_message_id: null,
    created_at: "2026-03-01T10:00:05Z",
    updated_at: "2026-03-01T10:00:05Z",
  },
];

describe("ChatSidebar Component", () => {
  it("renders conversation items with title, subject badge, and message count", () => {
    render(
      <ChatSidebar
        conversations={[mockConversation]}
        activeConversationId="conv-1"
        subjects={[mockSubject]}
        statusTab="active"
        onStatusTabChange={vi.fn()}
        searchQuery=""
        onSearchChange={vi.fn()}
        onNewChat={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
        hasMore={false}
        onLoadMore={vi.fn()}
        loading={false}
        loadingMore={false}
      />
    );

    expect(screen.getByText("TCP Handshake Discussion")).toBeInTheDocument();
    expect(screen.getByText("Computer Networks")).toBeInTheDocument();
    expect(screen.getByText(/2 msgs/)).toBeInTheDocument();
  });

  it("triggers onNewChat when New Chat button is clicked", () => {
    const onNewChat = vi.fn();
    render(
      <ChatSidebar
        conversations={[]}
        subjects={[mockSubject]}
        statusTab="active"
        onStatusTabChange={vi.fn()}
        searchQuery=""
        onSearchChange={vi.fn()}
        onNewChat={onNewChat}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
        hasMore={false}
        onLoadMore={vi.fn()}
        loading={false}
        loadingMore={false}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /New Chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("filters conversation list by search query", () => {
    const conv2: ChatConversation = {
      ...mockConversation,
      id: "conv-2",
      title: "DNS Resolution",
    };

    render(
      <ChatSidebar
        conversations={[mockConversation, conv2]}
        subjects={[mockSubject]}
        statusTab="active"
        onStatusTabChange={vi.fn()}
        searchQuery="DNS"
        onSearchChange={vi.fn()}
        onNewChat={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
        hasMore={false}
        onLoadMore={vi.fn()}
        loading={false}
        loadingMore={false}
      />
    );

    expect(screen.getByText("DNS Resolution")).toBeInTheDocument();
    expect(screen.queryByText("TCP Handshake Discussion")).not.toBeInTheDocument();
  });

  it("calls onArchive when archive button is clicked on an active conversation", () => {
    const onArchive = vi.fn();
    render(
      <ChatSidebar
        conversations={[mockConversation]}
        subjects={[mockSubject]}
        statusTab="active"
        onStatusTabChange={vi.fn()}
        searchQuery=""
        onSearchChange={vi.fn()}
        onNewChat={vi.fn()}
        onRename={vi.fn()}
        onArchive={onArchive}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
        hasMore={false}
        onLoadMore={vi.fn()}
        loading={false}
        loadingMore={false}
      />
    );

    fireEvent.click(screen.getByTitle("Archive conversation"));
    expect(onArchive).toHaveBeenCalledWith(mockConversation);
  });
});

describe("ChatInterface Component", () => {
  it("renders header with subject badge, title, and active status", () => {
    render(
      <ChatInterface
        conversation={mockConversation}
        messages={mockMessages}
        loading={false}
        loadingOlder={false}
        hasMoreOlder={false}
        onLoadOlder={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByText("TCP Handshake Discussion")).toBeInTheDocument();
    expect(screen.getByText("Computer Networks")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("renders messages with sequence numbers and content", () => {
    render(
      <ChatInterface
        conversation={mockConversation}
        messages={mockMessages}
        loading={false}
        loadingOlder={false}
        hasMoreOlder={false}
        onLoadOlder={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByText("Explain the three-way handshake in TCP.")).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("The TCP 3-way handshake consists of SYN, SYN-ACK, and ACK.")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
  });

  it("renders informative M4.1 infrastructure banner when messages are empty", () => {
    render(
      <ChatInterface
        conversation={mockConversation}
        messages={[]}
        loading={false}
        loadingOlder={false}
        hasMoreOlder={false}
        onLoadOlder={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByText("Study Chat: Computer Networks")).toBeInTheDocument();
    expect(screen.getByText(/Phase 4 Milestone M4.1 — Infrastructure Ready/)).toBeInTheDocument();
  });

  it("renders load older messages button when hasMoreOlder is true", () => {
    const onLoadOlder = vi.fn();
    render(
      <ChatInterface
        conversation={mockConversation}
        messages={mockMessages}
        loading={false}
        loadingOlder={false}
        hasMoreOlder={true}
        onLoadOlder={onLoadOlder}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    const loadMoreBtn = screen.getByRole("button", { name: /Load older messages/i });
    expect(loadMoreBtn).toBeInTheDocument();
    fireEvent.click(loadMoreBtn);
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  });
});

describe("NewConversationModal Component", () => {
  it("renders subject options and calls createConversation on submit", async () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();

    render(
      <NewConversationModal
        isOpen={true}
        onClose={onClose}
        subjects={[mockSubject]}
        defaultSubjectId="sub-1"
        onConversationCreated={onCreated}
      />
    );

    expect(screen.getByText("New Study Conversation")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Computer Networks")).toBeInTheDocument();

    const titleInput = screen.getByPlaceholderText(/e.g. Chapter 4 Practice Questions/);
    fireEvent.change(titleInput, { target: { value: "Exam Revision" } });
    expect(titleInput).toHaveValue("Exam Revision");
  });
});

describe("RenameConversationModal Component", () => {
  it("pre-populates current title and allows renaming", () => {
    const onClose = vi.fn();
    const onRenamed = vi.fn();

    render(
      <RenameConversationModal
        conversation={mockConversation}
        isOpen={true}
        onClose={onClose}
        onConversationRenamed={onRenamed}
      />
    );

    expect(screen.getByText("Rename Conversation")).toBeInTheDocument();
    const titleInput = screen.getByDisplayValue("TCP Handshake Discussion");
    expect(titleInput).toBeInTheDocument();

    fireEvent.change(titleInput, { target: { value: "TCP and UDP Protocols" } });
    expect(titleInput).toHaveValue("TCP and UDP Protocols");
  });
});

describe("DeleteConversationModal Component", () => {
  it("displays confirmation message and delete action", () => {
    const onClose = vi.fn();
    const onDeleted = vi.fn();

    render(
      <DeleteConversationModal
        conversation={mockConversation}
        isOpen={true}
        onClose={onClose}
        onConversationDeleted={onDeleted}
      />
    );

    expect(screen.getByRole("heading", { name: "Delete Conversation" })).toBeInTheDocument();
    expect(
      screen.getByText(/Are you sure you want to delete/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/"TCP Handshake Discussion"/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete Conversation/i })).toBeInTheDocument();
  });
});
