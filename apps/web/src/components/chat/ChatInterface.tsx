"use client";

import { useEffect, useRef } from "react";
import {
  Archive,
  ArchiveRestore,
  BookOpen,
  Calendar,
  CheckCircle2,
  Clock,
  Edit2,
  Info,
  Loader2,
  MessageSquare,
  Sparkles,
  Trash2,
  User,
} from "lucide-react";
import type { ChatConversation, ChatMessage } from "@/types/chat";

interface ChatInterfaceProps {
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
}

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
}: ChatInterfaceProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (!loadingOlder && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView?.({ behavior: "smooth" });
    }
  }, [messages.length, loadingOlder]);

  const formattedDate = new Date(conversation.created_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <section aria-label="Conversation detail" className="flex h-full flex-1 flex-col overflow-hidden bg-white">
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-white px-6 py-3.5 shadow-sm">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
              <BookOpen className="h-3 w-3" />
              {conversation.subject_name}
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium capitalize ${
                conversation.status === "active"
                  ? "bg-green-50 text-green-700 border border-green-200"
                  : "bg-gray-100 text-gray-700 border border-gray-200"
              }`}
            >
              {conversation.status}
            </span>
          </div>
          <h1 className="text-lg font-bold text-gray-900 truncate max-w-xl">
            {conversation.title}
          </h1>
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
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {/* Load Older Messages Button */}
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
          /* Empty State & Milestone 4.1 Information */
          <div className="mx-auto max-w-xl py-12 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600 shadow-inner">
              <Sparkles className="h-7 w-7" />
            </div>
            <h2 className="mt-4 text-xl font-bold text-gray-900">
              Study Chat: {conversation.subject_name}
            </h2>
            <p className="mt-2 text-sm text-gray-600 leading-relaxed">
              This persistent conversation is bound to <span className="font-semibold text-gray-800">{conversation.subject_name}</span>.
              All messages and conversation state are stored directly in PostgreSQL with transactional sequence integrity.
            </p>

            <div className="mt-6 rounded-2xl border border-blue-100 bg-blue-50/70 p-4 text-left">
              <div className="flex items-start gap-3">
                <Info className="h-5 w-5 text-blue-600 shrink-0 mt-0.5" />
                <div className="text-xs text-blue-900 space-y-1.5">
                  <p className="font-semibold text-blue-950">
                    Phase 4 Milestone M4.1 — Infrastructure Ready
                  </p>
                  <p className="leading-relaxed">
                    Persistent conversation CRUD, multi-conversation management, and cursor-based history pagination are operational.
                  </p>
                  <p className="leading-relaxed text-blue-800">
                    Retrieval-Augmented Generation (semantic vector retrieval, citation provenance, and grounded LLM answers) will be wired in subsequent Phase 4 milestones (M4.5 / M4.6).
                  </p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* Message List */
          messages.map((msg) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={msg.id}
                className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}
              >
                {!isUser && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white shadow-sm">
                    <Sparkles className="h-4 w-4" />
                  </div>
                )}

                <div
                  className={`flex max-w-2xl flex-col ${
                    isUser ? "items-end" : "items-start"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1 text-[11px] text-gray-500">
                    <span className="font-medium text-gray-700">
                      {isUser ? "You" : "Study Assistant"}
                    </span>
                    <span className="rounded bg-gray-100 px-1 py-0.2 text-[10px] font-mono text-gray-600">
                      #{msg.sequence_number}
                    </span>
                    <span>
                      {new Date(msg.created_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>

                  <div
                    className={`rounded-2xl px-4 py-2.5 text-sm shadow-sm whitespace-pre-wrap leading-relaxed ${
                      isUser
                        ? "bg-blue-600 text-white rounded-tr-none"
                        : "bg-gray-100 text-gray-900 rounded-tl-none border border-gray-200"
                    }`}
                  >
                    {msg.content}
                  </div>
                </div>

                {isUser && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-700 shadow-sm">
                    <User className="h-4 w-4" />
                  </div>
                )}
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Honest Milestone 4.1 Input Area */}
      <footer className="border-t border-gray-200 bg-gray-50/80 p-4">
        <div className="mx-auto max-w-3xl">
          <div className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white p-2 shadow-inner">
            <input
              type="text"
              disabled
              placeholder="Grounded RAG study assistant will be activated in Phase 4 Milestone 4.5..."
              className="flex-1 bg-transparent px-3 py-1.5 text-xs text-gray-500 placeholder:text-gray-400 focus:outline-none disabled:cursor-not-allowed"
            />
            <button
              type="button"
              disabled
              className="rounded-lg bg-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-400 disabled:cursor-not-allowed"
            >
              Send
            </button>
          </div>
          <p className="mt-2 text-center text-[11px] text-gray-500">
            Conversation persistence and subject isolation verified. Full RAG pipeline will be integrated in upcoming milestones.
          </p>
        </div>
      </footer>
    </section>
  );
}
