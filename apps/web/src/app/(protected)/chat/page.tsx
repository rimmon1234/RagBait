"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BookOpen, FolderOpen, Loader2, MessageSquare, Plus, Sparkles } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import {
  archiveConversation,
  deleteConversation,
  listConversations,
  restoreConversation,
} from "@/lib/chat-client";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { NewConversationModal } from "@/components/chat/NewConversationModal";
import { RenameConversationModal } from "@/components/chat/RenameConversationModal";
import { DeleteConversationModal } from "@/components/chat/DeleteConversationModal";
import type { ChatConversation } from "@/types/chat";

function ChatLandingContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const subjectIdParam = searchParams.get("subject_id") || undefined;

  const { subjects, loading: subjectsLoading } = useKnowledgeSubjects();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | undefined>(subjectIdParam);
  const [statusTab, setStatusTab] = useState<"active" | "archived">("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  // Modal states
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [editingConv, setEditingConv] = useState<ChatConversation | null>(null);
  const [deletingConv, setDeletingConv] = useState<ChatConversation | null>(null);

  // Keep selectedSubjectId in sync with searchParams if param changes
  useEffect(() => {
    if (subjectIdParam) {
      setSelectedSubjectId(subjectIdParam);
    }
  }, [subjectIdParam]);

  const loadConversations = useCallback(
    async (reset = true) => {
      try {
        if (reset) {
          setLoading(true);
        } else {
          setLoadingMore(true);
        }

        const res = await listConversations({
          subject_id: selectedSubjectId,
          status: statusTab,
          cursor: reset ? undefined : nextCursor || undefined,
          search: searchQuery.trim() || undefined,
          limit: 20,
        });

        if (reset) {
          setConversations(res.items);
        } else {
          setConversations((prev) => [...prev, ...res.items]);
        }
        setNextCursor(res.next_cursor);
        setHasMore(res.has_more);
      } catch (err) {
        console.error("Failed to load conversations:", err);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [selectedSubjectId, statusTab, searchQuery, nextCursor]
  );

  useEffect(() => {
    loadConversations(true);
  }, [selectedSubjectId, statusTab, searchQuery]);

  const handleConversationCreated = (conv: ChatConversation) => {
    setConversations((prev) => [conv, ...prev]);
    router.push(`/chat/${conv.id}`);
  };

  const handleConversationRenamed = (updated: ChatConversation) => {
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  const handleArchive = async (conv: ChatConversation) => {
    try {
      const updated = await archiveConversation(conv.id);
      setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      console.error("Failed to archive conversation:", err);
    }
  };

  const handleRestore = async (conv: ChatConversation) => {
    try {
      const updated = await restoreConversation(conv.id);
      setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      console.error("Failed to restore conversation:", err);
    }
  };

  const handleConversationDeleted = (deletedId: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== deletedId));
  };

  const currentSubjectName = subjects.find((s) => s.id === selectedSubjectId)?.name;

  return (
    <div className="flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-white">
      {/* Sidebar */}
      <ChatSidebar
        conversations={conversations}
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
        hasMore={hasMore}
        onLoadMore={() => loadConversations(false)}
        loading={loading}
        loadingMore={loadingMore}
      />

      {/* Main Content: Study Chat Welcome Area */}
      <main className="hidden flex-1 flex-col items-center justify-center p-8 text-center md:flex bg-gray-50/50">
        <div className="max-w-md rounded-2xl bg-white p-8 shadow-sm border border-gray-200">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 shadow-inner">
            <MessageSquare className="h-7 w-7" />
          </div>

          <h2 className="mt-4 text-xl font-bold text-gray-900">
            {currentSubjectName
              ? `Study Assistant for ${currentSubjectName}`
              : "RagBait Study Assistant"}
          </h2>

          <p className="mt-2 text-sm text-gray-600 leading-relaxed">
            Select an existing conversation from the sidebar or start a new persistent study session
            bound to an academic subject.
          </p>

          <button
            type="button"
            onClick={() => setIsNewModalOpen(true)}
            className="mt-6 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <Plus className="h-4 w-4" />
            <span>Start New Conversation</span>
          </button>
        </div>
      </main>

      {/* Modals */}
      <NewConversationModal
        isOpen={isNewModalOpen}
        onClose={() => setIsNewModalOpen(false)}
        subjects={subjects}
        defaultSubjectId={selectedSubjectId}
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

export default function ChatLandingPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-[calc(100vh-4rem)] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      }
    >
      <ChatLandingContent />
    </Suspense>
  );
}
