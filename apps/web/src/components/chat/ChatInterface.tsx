"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  ArchiveRestore,
  ArrowDown,
  BookOpen,
  Edit2,
  Loader2,
  Menu,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { QueueWaitInfo, StreamErrorState } from "@/lib/chat/useChatSession";
import type {
  ChatConversation,
  ChatMessage,
  GenerationPhase,
  InferenceExecutionMode,
  StreamSourceSummary,
} from "@/types/chat";

export interface ChatInterfaceProps {
  conversation: ChatConversation;
  messages: ChatMessage[];
  loading: boolean;
  loadingOlder: boolean;
  hasMoreOlder: boolean;
  onLoadOlder: () => void;
  onRename: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onDelete: () => void;
  // M4.7 streaming & grounded citations integration
  canGenerate?: boolean;
  contributionReason?: string;
  phase?: GenerationPhase;
  isGenerationActive?: boolean;
  isProvisionalMessage?: (messageId: string) => boolean;
  wasContentReplaced?: (messageId: string) => boolean;
  identifiedSourcesForMessage?: (messageId: string) => StreamSourceSummary[] | undefined;
  queueInfo?: QueueWaitInfo | null;
  generationError?: StreamErrorState | null;
  hasRetryPayload?: boolean;
  draftOverride?: string | null;
  onClearDraftOverride?: () => void;
  inferenceMode?: InferenceExecutionMode;
  onInferenceModeChange?: (mode: InferenceExecutionMode) => void;
  selectedLocalModel?: string;
  onLocalModelChange?: (modelId: string) => void;
  onSendMessage?: (content: string) => Promise<void> | void;
  onCancelGeneration?: () => void;
  onRetryGeneration?: () => void;
  onInspectSources?: (messageId: string, focusedCitationKey?: string) => void;
  onOpenSidebar?: () => void;
}

const STARTER_SUGGESTIONS = [
  "Summarize the core concepts covered in the lecture materials for this subject.",
  "List the key definitions and formulas I should review with source page citations.",
  "Quiz me with 3 practice questions based on these course documents.",
];

export function ChatInterface({
  conversation,
  messages,
  loading,
  loadingOlder,
  hasMoreOlder,
  onLoadOlder,
  onRename,
  onArchive,
  onRestore,
  onDelete,
  canGenerate = true,
  contributionReason,
  phase = "idle",
  isGenerationActive = false,
  isProvisionalMessage = () => false,
  wasContentReplaced = () => false,
  identifiedSourcesForMessage = () => undefined,
  queueInfo = null,
  generationError = null,
  hasRetryPayload = false,
  draftOverride = null,
  onClearDraftOverride,
  inferenceMode = "cloud",
  onInferenceModeChange = () => undefined,
  selectedLocalModel = "",
  onLocalModelChange = () => undefined,
  onSendMessage = () => undefined,
  onCancelGeneration = () => undefined,
  onRetryGeneration,
  onInspectSources = () => undefined,
  onOpenSidebar,
}: ChatInterfaceProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const isNear = distanceFromBottom < 120;
    nearBottomRef.current = isNear;
    setShowJumpToBottom(!isNear && messages.length > 0);
  }, [messages.length]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    messagesEndRef.current?.scrollIntoView?.({ behavior });
    nearBottomRef.current = true;
    setShowJumpToBottom(false);
  }, []);

  const lastMessage = messages[messages.length - 1];
  const lastContentLen = lastMessage?.content?.length ?? 0;

  // Smart scroll: stick to bottom during streaming/new messages only when user has not scrolled up
  useEffect(() => {
    if (loadingOlder) return;
    if (phase === "submitting") {
      scrollToBottom("smooth");
      return;
    }
    if (nearBottomRef.current && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView?.({
        behavior: isGenerationActive ? "auto" : "smooth",
      });
    }
  }, [messages.length, lastContentLen, loadingOlder, isGenerationActive, phase, scrollToBottom]);

  return (
    <section aria-label="Conversation detail" className="flex h-full flex-1 flex-col overflow-hidden bg-white">
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-white px-4 py-3 shadow-sm md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          {onOpenSidebar && (
            <button
              type="button"
              onClick={onOpenSidebar}
              aria-label="Open conversations menu"
              className="rounded-lg border border-gray-200 p-2 text-gray-600 hover:bg-gray-50 md:hidden"
            >
              <Menu className="h-4 w-4" />
            </button>
          )}
          <div className="min-w-0 flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
                <BookOpen className="h-3 w-3" />
                {conversation.subject_name}
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium capitalize ${
                  conversation.status === "active"
                    ? "border border-green-200 bg-green-50 text-green-700"
                    : "border border-gray-200 bg-gray-100 text-gray-700"
                }`}
              >
                {conversation.status}
              </span>
            </div>
            <h1 className="max-w-xl truncate text-base font-bold text-gray-900 md:text-lg">
              {conversation.title}
            </h1>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRename}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <Edit2 className="h-3.5 w-3.5 text-gray-500" />
            <span>Rename</span>
          </button>

          {conversation.status === "active" ? (
            <button
              type="button"
              onClick={onArchive}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <Archive className="h-3.5 w-3.5 text-gray-500" />
              <span>Archive</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={onRestore}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <ArchiveRestore className="h-3.5 w-3.5 text-gray-500" />
              <span>Restore</span>
            </button>
          )}

          <button
            type="button"
            onClick={onDelete}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-2.5 py-1.5 text-xs font-medium text-red-600 shadow-sm hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span>Delete</span>
          </button>
        </div>
      </header>

      {/* Messages Scroll Area */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="relative flex-1 space-y-5 overflow-y-auto px-4 py-6 md:px-8"
      >
        {hasMoreOlder && (
          <div className="flex justify-center pb-2">
            <button
              type="button"
              onClick={onLoadOlder}
              disabled={loadingOlder}
              className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
            >
              {loadingOlder && <Loader2 className="h-3 w-3 animate-spin text-blue-600" />}
              <span>Load older messages</span>
            </button>
          </div>
        )}

        {loading && messages.length === 0 ? (
          <div className="flex h-64 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          </div>
        ) : messages.length === 0 ? (
          <div className="mx-auto max-w-2xl py-8 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 shadow-inner">
              <Sparkles className="h-7 w-7" />
            </div>
            <h2 className="mt-4 text-xl font-bold text-gray-900">
              Grounded Study Chat: {conversation.subject_name}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-gray-600">
              Answers are grounded in verified course materials for{" "}
              <span className="font-semibold text-gray-800">{conversation.subject_name}</span>.
              Click any inline citation badge like <span className="font-mono text-blue-700">[S1]</span> to inspect exact source excerpts and jump directly to the referenced PDF page.
            </p>

            {conversation.status === "active" && canGenerate && (
              <div className="mt-6 grid gap-2 text-left sm:grid-cols-1">
                {STARTER_SUGGESTIONS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    disabled={isGenerationActive}
                    onClick={() => void onSendMessage(prompt)}
                    className="rounded-xl border border-gray-200 bg-gray-50/70 px-4 py-2.5 text-left text-xs font-medium text-gray-700 transition-colors hover:border-blue-300 hover:bg-blue-50/60 hover:text-blue-900 disabled:opacity-50"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-5">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                isProvisional={isProvisionalMessage(msg.id)}
                wasContentReplaced={wasContentReplaced(msg.id)}
                identifiedSourcesCount={identifiedSourcesForMessage(msg.id)?.length}
                onCitationClick={(citationKey, msgId) => onInspectSources(msgId, citationKey)}
                onInspectSources={(msgId) => onInspectSources(msgId)}
              />
            ))}
          </div>
        )}

        {/* Live generation queue / error banners */}
        <div className="mx-auto max-w-4xl space-y-2" aria-live="polite">
          {phase === "admission_waiting" && (
            <div className="flex items-center gap-2.5 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-xs text-blue-900">
              <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
              <span>
                Waiting for inference slot
                {queueInfo
                  ? ` — queue position #${queueInfo.position} (est. ~${queueInfo.estimatedWaitSeconds}s)`
                  : "..."}
              </span>
            </div>
          )}

          {phase === "cancelling" && (
            <div className="flex items-center gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900">
              <Loader2 className="h-4 w-4 animate-spin text-amber-700" />
              <span>Stopping generation and reconciling persisted partial output...</span>
            </div>
          )}

          {generationError && (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-xs text-red-800"
            >
              <div className="flex items-start gap-2">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
                <span>{generationError.message}</span>
              </div>
              {generationError.retryable && hasRetryPayload && onRetryGeneration && (
                <button
                  type="button"
                  onClick={onRetryGeneration}
                  className="inline-flex items-center gap-1 rounded-lg border border-red-300 bg-white px-2.5 py-1 font-semibold text-red-700 hover:bg-red-100"
                >
                  <RefreshCw className="h-3 w-3" />
                  <span>Retry</span>
                </button>
              )}
            </div>
          )}
        </div>

        <div ref={messagesEndRef} />

        {showJumpToBottom && (
          <div className="sticky bottom-3 flex justify-center pointer-events-none">
            <button
              type="button"
              onClick={() => scrollToBottom("smooth")}
              className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-md hover:bg-gray-50"
            >
              <ArrowDown className="h-3.5 w-3.5 text-blue-600" />
              <span>Jump to latest</span>
            </button>
          </div>
        )}
      </div>

      {/* M4.7 Composer */}
      <ChatComposer
        conversationStatus={conversation.status}
        subjectName={conversation.subject_name}
        canGenerate={canGenerate}
        contributionReason={contributionReason}
        inferenceMode={inferenceMode}
        onInferenceModeChange={onInferenceModeChange}
        selectedLocalModel={selectedLocalModel}
        onLocalModelChange={onLocalModelChange}
        isGenerationActive={isGenerationActive}
        phase={phase}
        draftOverride={draftOverride}
        onClearDraftOverride={onClearDraftOverride}
        onSend={onSendMessage}
        onCancel={onCancelGeneration}
      />
    </section>
  );
}
