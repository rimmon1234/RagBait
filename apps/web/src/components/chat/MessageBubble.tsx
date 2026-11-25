"use client";

import React from "react";
import { AlertCircle, BookOpen, CheckCircle2, Cpu, Loader2, Sparkles, StopCircle, User } from "lucide-react";
import { MarkdownMessageRenderer } from "@/components/chat/MarkdownMessageRenderer";
import type { ChatMessage } from "@/types/chat";

export interface MessageBubbleProps {
  message: ChatMessage;
  isProvisional: boolean;
  wasContentReplaced: boolean;
  identifiedSourcesCount?: number;
  onCitationClick: (citationKey: string, messageId: string) => void;
  onInspectSources: (messageId: string) => void;
}

export const MessageBubble = React.memo(function MessageBubble({
  message,
  isProvisional,
  wasContentReplaced,
  identifiedSourcesCount,
  onCitationClick,
  onInspectSources,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const timestamp = new Date(message.created_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  if (isUser) {
    return (
      <div className="flex justify-end gap-3" data-testid={`message-${message.id}`}>
        <div className="flex max-w-2xl flex-col items-end">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-gray-500">
            <span className="font-medium text-gray-700">You</span>
            <span className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[10px] text-gray-600">
              #{message.sequence_number}
            </span>
            <span>{timestamp}</span>
          </div>
          <div className="rounded-2xl rounded-tr-none bg-blue-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-sm whitespace-pre-wrap break-words">
            {message.content}
          </div>
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-700 shadow-sm">
          <User className="h-4 w-4" />
        </div>
      </div>
    );
  }

  const isFailed = message.status === "failed";
  const isCancelled = message.status === "cancelled";
  const isCompleted = message.status === "completed";
  const isStreamingOrPending =
    isProvisional || message.status === "streaming" || message.status === "pending";

  return (
    <div className="flex justify-start gap-3" data-testid={`message-${message.id}`}>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white shadow-sm">
        {isStreamingOrPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
      </div>

      <div className="flex min-w-0 max-w-3xl flex-1 flex-col items-start">
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
          <span className="font-medium text-gray-700">Study Assistant</span>
          <span className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[10px] text-gray-600">
            #{message.sequence_number}
          </span>
          <span>{timestamp}</span>

          {message.model_id && (
            <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
              <Cpu className="h-2.5 w-2.5" />
              {message.model_id}
            </span>
          )}

          {isStreamingOrPending && (
            <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700">
              Streaming draft (unvalidated)
            </span>
          )}

          {isCompleted && !isProvisional && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
              <CheckCircle2 className="h-2.5 w-2.5" />
              Completed
            </span>
          )}

          {isCancelled && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-800">
              <StopCircle className="h-2.5 w-2.5" />
              Cancelled (partial)
            </span>
          )}

          {isFailed && (
            <span className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
              <AlertCircle className="h-2.5 w-2.5" />
              Failed
            </span>
          )}
        </div>

        <div
          className={`w-full rounded-2xl rounded-tl-none border px-4 py-3 text-sm shadow-sm ${
            isFailed
              ? "border-red-200 bg-red-50/40 text-gray-900"
              : isCancelled
              ? "border-amber-200 bg-amber-50/30 text-gray-900"
              : "border-gray-200 bg-gray-50/80 text-gray-900"
          }`}
        >
          {wasContentReplaced && (
            <div className="mb-2 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-1 text-[11px] text-blue-800">
              Answer citations were reconciled against retrieved subject sources.
            </div>
          )}

          {message.content ? (
            <MarkdownMessageRenderer
              content={message.content}
              isProvisional={isStreamingOrPending}
              onCitationClick={(key) => onCitationClick(key, message.id)}
            />
          ) : isStreamingOrPending ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
              <span>Preparing grounded study answer...</span>
            </div>
          ) : isFailed ? (
            <p className="text-xs text-red-700">
              {message.error_message || "Answer generation failed before output was produced."}
            </p>
          ) : isCancelled ? (
            <p className="text-xs italic text-amber-800">
              Generation cancelled before any output tokens were emitted.
            </p>
          ) : null}

          {isFailed && message.content && message.error_message && (
            <p className="mt-2 border-t border-red-200 pt-2 text-xs text-red-700">
              {message.error_message}
            </p>
          )}
        </div>

        {/* Footer action bar for authoritative sources inspection */}
        <div className="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            onClick={() => onInspectSources(message.id)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-700 shadow-sm hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <BookOpen className="h-3 w-3 text-blue-600" />
            <span>
              {isStreamingOrPending
                ? "Candidate Sources"
                : "Inspect Sources"}
            </span>
            {typeof identifiedSourcesCount === "number" && identifiedSourcesCount > 0 && (
              <span className="rounded bg-blue-100 px-1 py-0.2 font-mono text-[10px] font-semibold text-blue-800">
                {identifiedSourcesCount}
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
});
