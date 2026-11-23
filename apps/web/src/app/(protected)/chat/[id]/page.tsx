"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertCircle, Loader2 } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import {
  archiveConversation,
  deleteConversation,
  getConversation,
  getConversationMessages,
  listConversations,
  restoreConversation,
} from "@/lib/chat-client";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatInterface } from "@/components/chat/ChatInterface";
import { NewConversationModal } from "@/components/chat/NewConversationModal";
import { RenameConversationModal } from "@/components/chat/RenameConversationModal";
import { DeleteConversationModal } from "@/components/chat/DeleteConversationModal";
import type { ChatConversation, ChatMessage } from "@/types/chat";

export default function ChatDetailPage() {
  const router = useRouter();
  const routeParams = useParams<{ id: string }>();
  const conversationId = routeParams?.id;

  const { subjects } = useKnowledgeSubjects();

  // Sidebar state
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | undefined>(undefined);
  const [statusTab, setStatusTab] = useState<"active" | "archived">("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarLoading, setSidebarLoading] = useState(true);
  const [sidebarLoadingMore, setSidebarLoadingMore] = useState(false);
  const [sidebarCursor, setSidebarCursor] = useState<string | null>(null);
  const [sidebarHasMore, setSidebarHasMore] = useState(false);

  // Active conversation & messages state
  const [conversation, setConversation] = useState<ChatConversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConv, setLoadingConv] = useState(true);
  const [errorConv, setErrorConv] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [hasMoreOlder, setHasMoreOlder] = useState(false);

  // Modal states
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [editingConv, setEditingConv] = useState<ChatConversation | null>(null);
  const [deletingConv, setDeletingConv] = useState<ChatConversation | null>(null);

  // Load conversation and initial messages
  const loadActiveConversation = useCallback(async () => {
    if (!conversationId) return;

    try {
      setLoadingConv(true);
      setErrorConv(null);
      const [conv, msgResp] = await Promise.all([
        getConversation(conversationId),
        getConversationMessages(conversationId, { limit: 50 }),
      ]);
      setConversation(conv);
      setMessages(msgResp.items);
      setOlderCursor(msgResp.next_cursor);
      setHasMoreOlder(msgResp.has_more);
    } catch (err) {
      console.error("Failed to load conversation details:", err);
      setErrorConv(err instanceof Error ? err.message : "Conversation not found or inaccessible");
    } finally {
      setLoadingConv(false);
    }
  }, [conversationId]);

  useEffect(() => {
    loadActiveConversation();
  }, [loadActiveConversation]);

  // Load conversations for sidebar
  const loadSidebarConversations = useCallback(
    async (reset = true) => {
      try {
        if (reset) {
          setSidebarLoading(true);
        } else {
          setSidebarLoadingMore(true);
        }

        const res = await listConversations({
          subject_id: selectedSubjectId,
          status: statusTab,
          cursor: reset ? undefined : sidebarCursor || undefined,
          search: searchQuery.trim() || undefined,
          limit: 20,
        });

        if (reset) {
          setConversations(res.items);
        } else {
          setConversations((prev) => [...prev, ...res.items]);
        }
        setSidebarCursor(res.next_cursor);
        setSidebarHasMore(res.has_more);
      } catch (err) {
        console.error("Failed to load conversations for sidebar:", err);
      } finally {
        setSidebarLoading(false);
        setSidebarLoadingMore(false);
      }
    },
    [selectedSubjectId, statusTab, searchQuery, sidebarCursor]
  );

  useEffect(() => {
    loadSidebarConversations(true);
  }, [selectedSubjectId, statusTab, searchQuery]);

  // Load older messages
  const handleLoadOlderMessages = async () => {
    if (!conversationId || !olderCursor || loadingOlder) return;

    try {
      setLoadingOlder(true);
      const res = await getConversationMessages(conversationId, {
        cursor: olderCursor,
        direction: "before",
        limit: 50,
      });

      // Older messages should be prepended
      setMessages((prev) => [...res.items, ...prev]);
      setOlderCursor(res.next_cursor);
      setHasMoreOlder(res.has_more);
    } catch (err) {
      console.error("Failed to load older messages:", err);
    } finally {
      setLoadingOlder(false);
    }
  };

  const handleConversationCreated = (conv: ChatConversation) => {
    setConversations((prev) => [conv, ...prev]);
    router.push(`/chat/${conv.id}`);
  };

  const handleConversationRenamed = (updated: ChatConversation) => {
    if (conversation?.id === updated.id) {
      setConversation(updated);
    }
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  const handleArchive = async (target: ChatConversation) => {
    try {
      const updated = await archiveConversation(target.id);
      if (conversation?.id === updated.id) {
        setConversation(updated);
      }
      setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      console.error("Failed to archive conversation:", err);
    }
  };

  const handleRestore = async (target: ChatConversation) => {
    try {
      const updated = await restoreConversation(target.id);
      if (conversation?.id === updated.id) {
        setConversation(updated);
      }
      setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      console.error("Failed to restore conversation:", err);
    }
  };

  const handleConversationDeleted = (deletedId: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== deletedId));
    if (conversation?.id === deletedId) {
      router.push("/chat");
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-white">
      {/* Sidebar */}
      <ChatSidebar
        conversations={conversations}
        activeConversationId={conversationId}
        subjects={subjects}
        selectedSubjectId={selectedSubjectId}
        onSubjectFilterChange={setSelectedSubjectId}
        statusTab={statusTab}
        onStatusTabChange={setStatusTab}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onNewChat={() => setIsNewModalOpen(true)}
        onRename={setEditingConv}
        onArchive={handleArchive}
        onRestore={handleRestore}
        onDelete={setDeletingConv}
        hasMore={sidebarHasMore}
        onLoadMore={() => loadSidebarConversations(false)}
        loading={sidebarLoading}
        loadingMore={sidebarLoadingMore}
      />

      {/* Main Chat Interface */}
      <main className="flex flex-1 flex-col overflow-hidden">
        {loadingConv ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          </div>
        ) : errorConv || !conversation ? (
          <div className="flex h-full flex-col items-center justify-center p-8 text-center">
            <AlertCircle className="h-10 w-10 text-red-500" />
            <h2 className="mt-3 text-lg font-bold text-gray-900">Conversation Not Found</h2>
            <p className="mt-1 max-w-md text-sm text-gray-600">
              {errorConv || "This conversation does not exist or you do not have permission to view it."}
            </p>
            <button
              type="button"
              onClick={() => router.push("/chat")}
              className="mt-4 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700"
            >
              Back to Study Chats
            </button>
          </div>
        ) : (
          <ChatInterface
            conversation={conversation}
            messages={messages}
            loading={loadingConv}
            loadingOlder={loadingOlder}
            hasMoreOlder={hasMoreOlder}
            onLoadOlder={handleLoadOlderMessages}
            onRename={() => setEditingConv(conversation)}
            onArchive={() => handleArchive(conversation)}
            onRestore={() => handleRestore(conversation)}
            onDelete={() => setDeletingConv(conversation)}
          />
        )}
      </main>

      {/* Modals */}
      <NewConversationModal
        isOpen={isNewModalOpen}
        onClose={() => setIsNewModalOpen(false)}
        subjects={subjects}
        defaultSubjectId={conversation?.subject_id}
        onConversationCreated={handleConversationCreated}
      />

      <RenameConversationModal
        isOpen={Boolean(editingConv)}
        onClose={() => setEditingConv(null)}
        conversation={editingConv}
        onConversationRenamed={handleConversationRenamed}
      />

      <DeleteConversationModal
        isOpen={Boolean(deletingConv)}
        onClose={() => setDeletingConv(null)}
        conversation={deletingConv}
        onConversationDeleted={handleConversationDeleted}
      />
    </div>
  );
}
