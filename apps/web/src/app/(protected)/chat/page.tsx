"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, MessageSquare, Plus } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import {
  archiveConversation,
  deleteConversation,
  listConversations,
  restoreConversation,
} from "@/lib/chat-client";
import { useChatAccessState } from "@/lib/chat/useChatAccessState";
import { ChatAccessGate, ContributionGateBanner } from "@/components/chat/ChatAccessGate";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { NewConversationModal } from "@/components/chat/NewConversationModal";
import { RenameConversationModal } from "@/components/chat/RenameConversationModal";
import { DeleteConversationModal } from "@/components/chat/DeleteConversationModal";
import type { ChatConversation } from "@/types/chat";

function ChatLandingContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const subjectIdParam = searchParams.get("subject_id") || undefined;

  const access = useChatAccessState();
  const { subjects } = useKnowledgeSubjects();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | undefined>(subjectIdParam);
  const [statusTab, setStatusTab] = useState<"active" | "archived">("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [editingConv, setEditingConv] = useState<ChatConversation | null>(null);
  const [deletingConv, setDeletingConv] = useState<ChatConversation | null>(null);

  useEffect(() => {
    if (subjectIdParam) {
      setSelectedSubjectId(subjectIdParam);
    }
  }, [subjectIdParam]);

  const loadConversations = useCallback(
    async (reset = true) => {
      if (!access.canBrowseHistory) return;
      try {
        setListError(null);
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
        if (err instanceof ApiError && err.status === 401) {
          void access.refresh();
          return;
        }
        setListError(err instanceof Error ? err.message : "Failed to load conversations");
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [access, selectedSubjectId, statusTab, searchQuery, nextCursor]
  );

  useEffect(() => {
    if (access.canBrowseHistory) {
      void loadConversations(true);
    }
    // Intentionally trigger reset reload when filters or browse permission change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.canBrowseHistory, selectedSubjectId, statusTab, searchQuery]);

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
      setListError(err instanceof Error ? err.message : "Failed to archive conversation");
    }
  };

  const handleRestore = async (conv: ChatConversation) => {
    try {
      const updated = await restoreConversation(conv.id);
      setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to restore conversation");
    }
  };

  const handleConversationDeleted = (deletedId: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== deletedId));
  };

  const currentSubjectName = subjects.find((s) => s.id === selectedSubjectId)?.name;

  return (
    <ChatAccessGate access={access}>
      <div className="flex h-[calc(100vh-4rem)] w-full flex-col overflow-hidden bg-white">
        {!access.canGenerate && (
          <ContributionGateBanner reason={access.entitlement?.reason} />
        )}

        <div className="flex flex-1 overflow-hidden">
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
            onLoadMore={() => void loadConversations(false)}
            loading={loading}
            loadingMore={loadingMore}
            error={listError}
            onRetry={() => void loadConversations(true)}
            canCreateChat={access.canGenerate}
          />

          <main className="hidden flex-1 flex-col items-center justify-center bg-gray-50/50 p-8 text-center md:flex">
            <div className="max-w-md rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 shadow-inner">
                <MessageSquare className="h-7 w-7" />
              </div>

              <h2 className="mt-4 text-xl font-bold text-gray-900">
                {currentSubjectName
                  ? `Study Assistant for ${currentSubjectName}`
                  : "RagBait Study Assistant"}
              </h2>

              <p className="mt-2 text-sm leading-relaxed text-gray-600">
                Select an existing conversation from the sidebar to review grounded citations, or start a new subject-scoped study chat.
              </p>

              <button
                type="button"
                disabled={!access.canGenerate}
                onClick={() => setIsNewModalOpen(true)}
                className="mt-6 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-500"
              >
                <Plus className="h-4 w-4" />
                <span>Start New Conversation</span>
              </button>
            </div>
          </main>
        </div>

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
    </ChatAccessGate>
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
