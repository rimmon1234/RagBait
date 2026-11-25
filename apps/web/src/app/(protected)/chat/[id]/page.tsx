"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import {
  archiveConversation,
  deleteConversation,
  getConversation,
  getConversationMessages,
  listConversations,
  restoreConversation,
} from "@/lib/chat-client";
import { useChatAccessState } from "@/lib/chat/useChatAccessState";
import { useChatSession } from "@/lib/chat/useChatSession";
import { ChatAccessGate, ContributionGateBanner } from "@/components/chat/ChatAccessGate";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatInterface } from "@/components/chat/ChatInterface";
import { SourcesDrawer } from "@/components/chat/SourcesDrawer";
import { NewConversationModal } from "@/components/chat/NewConversationModal";
import { RenameConversationModal } from "@/components/chat/RenameConversationModal";
import { DeleteConversationModal } from "@/components/chat/DeleteConversationModal";
import type { ChatConversation, ChatMessage, InferenceExecutionMode } from "@/types/chat";

export default function ChatDetailPage() {
  const router = useRouter();
  const routeParams = useParams<{ id: string }>();
  const conversationId = routeParams?.id || "";

  const access = useChatAccessState();
  const { subjects } = useKnowledgeSubjects();

  // Sidebar state
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | undefined>(undefined);
  const [statusTab, setStatusTab] = useState<"active" | "archived">("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarLoading, setSidebarLoading] = useState(true);
  const [sidebarLoadingMore, setSidebarLoadingMore] = useState(false);
  const [sidebarError, setSidebarError] = useState<string | null>(null);
  const [sidebarCursor, setSidebarCursor] = useState<string | null>(null);
  const [sidebarHasMore, setSidebarHasMore] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  // Active conversation initial snapshot
  const [conversation, setConversation] = useState<ChatConversation | null>(null);
  const [initialMessages, setInitialMessages] = useState<ChatMessage[]>([]);
  const [loadingConv, setLoadingConv] = useState(true);
  const [errorConv, setErrorConv] = useState<string | null>(null);
  const [initialOlderCursor, setInitialOlderCursor] = useState<string | null>(null);
  const [initialHasMoreOlder, setInitialHasMoreOlder] = useState(false);

  // Inference mode & Private Local selected model
  const [inferenceMode, setInferenceMode] = useState<InferenceExecutionMode>("cloud");
  const [selectedLocalModel, setSelectedLocalModel] = useState("");

  // Sources Drawer state
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [sourcesMessageId, setSourcesMessageId] = useState<string | null>(null);
  const [focusedCitationKey, setFocusedCitationKey] = useState<string | null>(null);

  // Modal states
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);
  const [editingConv, setEditingConv] = useState<ChatConversation | null>(null);
  const [deletingConv, setDeletingConv] = useState<ChatConversation | null>(null);

  const loadSidebarConversations = useCallback(
    async (reset = true) => {
      if (!access.canBrowseHistory) return;
      try {
        setSidebarError(null);
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
        if (err instanceof ApiError && err.status === 401) {
          void access.refresh();
          return;
        }
        setSidebarError(err instanceof Error ? err.message : "Failed to load conversations");
      } finally {
        setSidebarLoading(false);
        setSidebarLoadingMore(false);
      }
    },
    [access, selectedSubjectId, statusTab, searchQuery, sidebarCursor]
  );

  const loadActiveConversation = useCallback(async () => {
    if (!conversationId || !access.canBrowseHistory) return;

    try {
      setLoadingConv(true);
      setErrorConv(null);
      setSourcesOpen(false);
      setSourcesMessageId(null);
      setFocusedCitationKey(null);

      const [conv, msgResp] = await Promise.all([
        getConversation(conversationId),
        getConversationMessages(conversationId, { limit: 50 }),
      ]);
      setConversation(conv);
      setInitialMessages(msgResp.items);
      setInitialOlderCursor(msgResp.next_cursor);
      setInitialHasMoreOlder(msgResp.has_more);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        void access.refresh();
        return;
      }
      setErrorConv(err instanceof Error ? err.message : "Conversation not found or inaccessible");
    } finally {
      setLoadingConv(false);
    }
  }, [conversationId, access]);

  useEffect(() => {
    if (access.canBrowseHistory) {
      void loadActiveConversation();
    }
    // Reload active conversation only when route id or browse permission becomes true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, access.canBrowseHistory]);

  useEffect(() => {
    if (access.canBrowseHistory) {
      void loadSidebarConversations(true);
    }
    // Reload sidebar cleanly on filter/tab/search changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.canBrowseHistory, selectedSubjectId, statusTab, searchQuery]);

  const handleConversationUpdated = useCallback(() => {
    void loadSidebarConversations(true);
  }, [loadSidebarConversations]);

  const handleAuthExpired = useCallback(() => {
    void access.refresh();
  }, [access]);

  const session = useChatSession({
    conversationId,
    conversation,
    initialMessages,
    initialOlderCursor,
    initialHasMoreOlder,
    onConversationUpdated: handleConversationUpdated,
    onContributionRequired: access.markContributionRequired,
    onAuthExpired: handleAuthExpired,
  });

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (inferenceMode === "private_local") {
        await session.submitPrivateLocalMessage(content, selectedLocalModel);
      } else {
        await session.submitCloudMessage(content);
      }
    },
    [inferenceMode, selectedLocalModel, session]
  );

  const handleInspectSources = useCallback((messageId: string, citationKey?: string) => {
    setSourcesMessageId(messageId);
    setFocusedCitationKey(citationKey || null);
    setSourcesOpen(true);
  }, []);

  const handleConversationCreated = (conv: ChatConversation) => {
    setConversations((prev) => [conv, ...prev]);
    setMobileSidebarOpen(false);
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
      setSidebarError(err instanceof Error ? err.message : "Failed to archive conversation");
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
      setSidebarError(err instanceof Error ? err.message : "Failed to restore conversation");
    }
  };

  const handleConversationDeleted = (deletedId: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== deletedId));
    if (conversation?.id === deletedId) {
      router.push("/chat");
    }
  };

  const activeSourcesProvisional = sourcesMessageId
    ? session.isProvisionalMessage(sourcesMessageId)
    : false;
  const activeProvisionalSources = sourcesMessageId
    ? session.identifiedSourcesForMessage(sourcesMessageId)
    : undefined;

  return (
    <ChatAccessGate access={access}>
      <div className="flex h-[calc(100vh-4rem)] w-full flex-col overflow-hidden bg-white">
        {!access.canGenerate && (
          <ContributionGateBanner reason={access.entitlement?.reason} />
        )}

        <div className="relative flex flex-1 overflow-hidden">
          {/* Desktop Sidebar */}
          <div className="hidden md:flex md:shrink-0">
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
              onLoadMore={() => void loadSidebarConversations(false)}
              loading={sidebarLoading}
              loadingMore={sidebarLoadingMore}
              error={sidebarError}
              onRetry={() => void loadSidebarConversations(true)}
              canCreateChat={access.canGenerate}
            />
          </div>

          {/* Mobile Slide-Over Sidebar */}
          {mobileSidebarOpen && (
            <div className="fixed inset-0 z-40 flex md:hidden">
              <div
                className="fixed inset-0 bg-black/40"
                aria-hidden="true"
                onClick={() => setMobileSidebarOpen(false)}
              />
              <div className="relative z-10 flex h-full w-80 max-w-[85vw] bg-gray-50 shadow-2xl">
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
                  onNewChat={() => {
                    setMobileSidebarOpen(false);
                    setIsNewModalOpen(true);
                  }}
                  onRename={setEditingConv}
                  onArchive={handleArchive}
                  onRestore={handleRestore}
                  onDelete={setDeletingConv}
                  hasMore={sidebarHasMore}
                  onLoadMore={() => void loadSidebarConversations(false)}
                  loading={sidebarLoading}
                  loadingMore={sidebarLoadingMore}
                  error={sidebarError}
                  onRetry={() => void loadSidebarConversations(true)}
                  canCreateChat={access.canGenerate}
                  onCloseMobile={() => setMobileSidebarOpen(false)}
                />
              </div>
            </div>
          )}

          {/* Main Chat Interface */}
          <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
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
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  <button
                    type="button"
                    onClick={() => void loadActiveConversation()}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                  >
                    <RefreshCw className="h-4 w-4" />
                    <span>Retry</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => router.push("/chat")}
                    className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700"
                  >
                    Back to Study Chats
                  </button>
                </div>
              </div>
            ) : (
              <ChatInterface
                conversation={conversation}
                messages={session.messages}
                loading={loadingConv}
                loadingOlder={session.loadingOlder}
                hasMoreOlder={session.hasMoreOlder}
                onLoadOlder={() => void session.loadOlderMessages()}
                onRename={() => setEditingConv(conversation)}
                onArchive={() => void handleArchive(conversation)}
                onRestore={() => void handleRestore(conversation)}
                onDelete={() => setDeletingConv(conversation)}
                canGenerate={access.canGenerate}
                contributionReason={access.entitlement?.reason}
                phase={session.phase}
                isGenerationActive={session.isGenerationActive}
                isProvisionalMessage={session.isProvisionalMessage}
                wasContentReplaced={session.wasContentReplaced}
                identifiedSourcesForMessage={session.identifiedSourcesForMessage}
                queueInfo={session.queueInfo}
                generationError={session.generationError}
                hasRetryPayload={Boolean(session.retryPayload)}
                draftOverride={session.draftOverride}
                onClearDraftOverride={session.clearDraftOverride}
                inferenceMode={inferenceMode}
                onInferenceModeChange={setInferenceMode}
                selectedLocalModel={selectedLocalModel}
                onLocalModelChange={setSelectedLocalModel}
                onSendMessage={handleSendMessage}
                onCancelGeneration={() => void session.cancelActiveGeneration()}
                onRetryGeneration={() => void session.retryFailedSubmission()}
                onInspectSources={handleInspectSources}
                onOpenSidebar={() => setMobileSidebarOpen(true)}
              />
            )}
          </main>
        </div>

        <SourcesDrawer
          isOpen={sourcesOpen}
          messageId={sourcesMessageId}
          subjectName={conversation?.subject_name}
          focusedCitationKey={focusedCitationKey}
          isProvisional={activeSourcesProvisional}
          provisionalSources={activeProvisionalSources}
          onClose={() => setSourcesOpen(false)}
        />

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
    </ChatAccessGate>
  );
}
