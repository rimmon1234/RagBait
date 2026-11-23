import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  archiveConversation,
  createConversation,
  deleteConversation,
  getConversation,
  getConversationMessages,
  listConversations,
  renameConversation,
  restoreConversation,
} from "@/lib/chat-client";

// Mock fetchWithAuth
vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/api";

const mockFetchWithAuth = vi.mocked(fetchWithAuth);

describe("Chat API Client (Phase 4 M4.1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("createConversation posts to /api/v1/chat/conversations", async () => {
    const mockConv = {
      id: "conv-1",
      user_id: "user-1",
      subject_id: "subj-1",
      subject_name: "Operating Systems",
      subject_slug: "os",
      title: "Processes & Threads",
      status: "active" as const,
      message_count: 0,
      last_message_at: null,
      archived_at: null,
      created_at: "2026-03-01T00:00:00Z",
      updated_at: "2026-03-01T00:00:00Z",
    };
    mockFetchWithAuth.mockResolvedValueOnce(mockConv);

    const result = await createConversation({
      subject_id: "subj-1",
      title: "Processes & Threads",
    });

    expect(mockFetchWithAuth).toHaveBeenCalledWith("/api/v1/chat/conversations", {
      method: "POST",
      body: JSON.stringify({
        subject_id: "subj-1",
        title: "Processes & Threads",
      }),
    });
    expect(result).toEqual(mockConv);
  });

  it("listConversations builds query string with filters and cursor", async () => {
    const mockList = {
      items: [],
      has_more: false,
      next_cursor: null,
    };
    mockFetchWithAuth.mockResolvedValueOnce(mockList);

    const result = await listConversations({
      subject_id: "subj-1",
      status: "active",
      limit: 10,
      cursor: "cursor-abc",
      search: "concurrency",
    });

    expect(mockFetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/conversations?subject_id=subj-1&status=active&limit=10&cursor=cursor-abc&search=concurrency",
      { method: "GET" }
    );
    expect(result).toEqual(mockList);
  });

  it("getConversation calls GET /api/v1/chat/conversations/{id}", async () => {
    const mockConv = { id: "conv-1", title: "Test Chat" };
    mockFetchWithAuth.mockResolvedValueOnce(mockConv);

    const result = await getConversation("conv-1");
    expect(mockFetchWithAuth).toHaveBeenCalledWith("/api/v1/chat/conversations/conv-1", {
      method: "GET",
    });
    expect(result).toEqual(mockConv);
  });

  it("renameConversation calls PATCH /api/v1/chat/conversations/{id}", async () => {
    const mockConv = { id: "conv-1", title: "Renamed Title" };
    mockFetchWithAuth.mockResolvedValueOnce(mockConv);

    const result = await renameConversation("conv-1", { title: "Renamed Title" });
    expect(mockFetchWithAuth).toHaveBeenCalledWith("/api/v1/chat/conversations/conv-1", {
      method: "PATCH",
      body: JSON.stringify({ title: "Renamed Title" }),
    });
    expect(result).toEqual(mockConv);
  });

  it("archiveConversation calls POST /api/v1/chat/conversations/{id}/archive", async () => {
    const mockConv = { id: "conv-1", status: "archived" };
    mockFetchWithAuth.mockResolvedValueOnce(mockConv);

    const result = await archiveConversation("conv-1");
    expect(mockFetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/conversations/conv-1/archive",
      { method: "POST" }
    );
    expect(result).toEqual(mockConv);
  });

  it("restoreConversation calls POST /api/v1/chat/conversations/{id}/restore", async () => {
    const mockConv = { id: "conv-1", status: "active" };
    mockFetchWithAuth.mockResolvedValueOnce(mockConv);

    const result = await restoreConversation("conv-1");
    expect(mockFetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/conversations/conv-1/restore",
      { method: "POST" }
    );
    expect(result).toEqual(mockConv);
  });

  it("deleteConversation calls DELETE /api/v1/chat/conversations/{id}", async () => {
    mockFetchWithAuth.mockResolvedValueOnce(undefined);

    await deleteConversation("conv-1");
    expect(mockFetchWithAuth).toHaveBeenCalledWith("/api/v1/chat/conversations/conv-1", {
      method: "DELETE",
    });
  });

  it("getConversationMessages calls GET with cursor and pagination params", async () => {
    const mockMessages = {
      items: [
        {
          id: "msg-1",
          conversation_id: "conv-1",
          sequence_number: 1,
          role: "user" as const,
          content: "Hello",
          status: "completed" as const,
          client_message_id: null,
          created_at: "2026-03-01T00:00:00Z",
          updated_at: "2026-03-01T00:00:00Z",
        },
      ],
      has_more: true,
      next_cursor: "msg-cursor",
    };
    mockFetchWithAuth.mockResolvedValueOnce(mockMessages);

    const result = await getConversationMessages("conv-1", {
      limit: 20,
      cursor: "cursor-123",
      direction: "before",
    });

    expect(mockFetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/chat/conversations/conv-1/messages?limit=20&cursor=cursor-123&direction=before",
      { method: "GET" }
    );
    expect(result).toEqual(mockMessages);
  });
});
