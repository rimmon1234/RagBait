"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Archive,
  ArchiveRestore,
  BookOpen,
  Edit2,
  FolderOpen,
  Loader2,
  MessageSquare,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import type { SubjectRead } from "@/lib/knowledge-base/knowledge-base-types";
import type { ChatConversation, ChatConversationStatus } from "@/types/chat";

interface ChatSidebarProps {
  conversations: ChatConversation[];
  activeConversationId?: string;
  subjects: SubjectRead[];
  selectedSubjectId?: string;
  onSubjectFilterChange?: (subjectId: string | undefined) => void;
  statusTab: "active" | "archived";
  onStatusTabChange: (status: "active" | "archived") => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onNewChat: () => void;
  onRename: (conversation: ChatConversation) => void;
  onArchive: (conversation: ChatConversation) => void;
  onRestore: (conversation: ChatConversation) => void;
  onDelete: (conversation: ChatConversation) => void;
  hasMore: boolean;
  onLoadMore: () => void;
  loading: boolean;
  loadingMore: boolean;
}

export function ChatSidebar({
  conversations,
  activeConversationId,
  subjects,
  selectedSubjectId,
  onSubjectFilterChange,
  statusTab,
  onStatusTabChange,
  searchQuery,
  onSearchChange,
  onNewChat,
  onRename,
  onArchive,
  onRestore,
  onDelete,
  hasMore,
  onLoadMore,
  loading,
  loadingMore,
}: ChatSidebarProps) {
  const filteredConversations = useMemo(() => {
    return conversations.filter((c) => {
      const matchesStatus = c.status === statusTab;
      const matchesSubject = !selectedSubjectId || c.subject_id === selectedSubjectId;
      const matchesSearch =
        !searchQuery.trim() ||
        c.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        c.subject_name.toLowerCase().includes(searchQuery.toLowerCase());
      return matchesStatus && matchesSubject && matchesSearch;
    });
  }, [conversations, statusTab, selectedSubjectId, searchQuery]);

  return (
    <aside
      className="flex h-full w-full flex-col border-r border-gray-200 bg-gray-50 md:w-80 lg:w-96"
      aria-label="Conversation history"
    >
      {/* Header & New Chat button */}
      <div className="p-4 border-b border-gray-200 bg-white">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5 text-blue-600" />
            <h2 className="text-base font-bold text-gray-900">Study Chats</h2>
          </div>
          <button
            type="button"
            onClick={onNewChat}
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>New Chat</span>
          </button>
        </div>

        {/* Status Tabs: Active / Archived */}
        <div className="mt-3 flex rounded-lg bg-gray-100 p-1">
          <button
            type="button"
            onClick={() => onStatusTabChange("active")}
            className={`flex-1 rounded-md py-1 text-xs font-medium transition-colors ${
              statusTab === "active"
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-900"
            }`}
          >
            Active
          </button>
          <button
            type="button"
            onClick={() => onStatusTabChange("archived")}
            className={`flex-1 rounded-md py-1 text-xs font-medium transition-colors ${
              statusTab === "archived"
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-900"
            }`}
          >
            Archived
          </button>
        </div>

        {/* Filters: Subject selection & Search */}
        <div className="mt-3 space-y-2">
          {subjects.length > 0 && onSubjectFilterChange && (
            <div className="relative">
              <select
                aria-label="Filter by subject"
                value={selectedSubjectId || ""}
                onChange={(e) => onSubjectFilterChange(e.target.value || undefined)}
                className="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">All Subjects</option>
                {subjects.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              aria-label="Search conversations"
              placeholder="Search conversations..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              className="w-full rounded-lg border border-gray-200 bg-white py-1.5 pl-8 pr-3 text-xs text-gray-900 shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {loading && conversations.length === 0 ? (
          <div className="flex h-32 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
          </div>
        ) : filteredConversations.length === 0 ? (
          <div className="py-8 text-center px-4">
            <FolderOpen className="mx-auto h-8 w-8 text-gray-300" />
            <p className="mt-2 text-xs font-medium text-gray-600">
              {statusTab === "active" ? "No active conversations" : "No archived conversations"}
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {searchQuery
                ? "No results match your search"
                : statusTab === "active"
                ? "Start a new conversation to begin studying"
                : "Archived conversations will appear here"}
            </p>
          </div>
        ) : (
          filteredConversations.map((conv) => {
            const isActive = conv.id === activeConversationId;
            return (
              <div
                key={conv.id}
                className={`group relative flex items-center justify-between rounded-xl border p-2.5 transition-all ${
                  isActive
                    ? "border-blue-300 bg-blue-50/60 shadow-sm"
                    : "border-transparent bg-white hover:border-gray-200 hover:bg-gray-100/80"
                }`}
              >
                <Link
                  href={`/chat/${conv.id}`}
                  className="flex min-w-0 flex-1 flex-col pr-2 text-left"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-semibold text-gray-900">
                      {conv.title}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-gray-500">
                    <span className="inline-flex items-center gap-0.5 rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600">
                      <BookOpen className="h-2.5 w-2.5" />
                      {conv.subject_name}
                    </span>
                    <span>•</span>
                    <span>
                      {conv.message_count} {conv.message_count === 1 ? "msg" : "msgs"}
                    </span>
                  </div>
                </Link>

                {/* Actions Menu */}
                <div className="flex items-center gap-1 opacity-80 group-hover:opacity-100">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      onRename(conv);
                    }}
                    className="rounded p-1 text-gray-400 hover:bg-gray-200/70 hover:text-gray-700"
                    title="Rename conversation"
                    aria-label={`Rename ${conv.title}`}
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>

                  {conv.status === "active" ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        onArchive(conv);
                      }}
                      className="rounded p-1 text-gray-400 hover:bg-gray-200/70 hover:text-gray-700"
                      title="Archive conversation"
                      aria-label={`Archive ${conv.title}`}
                    >
                      <Archive className="h-3.5 w-3.5" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        onRestore(conv);
                      }}
                      className="rounded p-1 text-gray-400 hover:bg-gray-200/70 hover:text-gray-700"
                      title="Restore conversation"
                      aria-label={`Restore ${conv.title}`}
                    >
                      <ArchiveRestore className="h-3.5 w-3.5" />
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      onDelete(conv);
                    }}
                    className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
                    title="Delete conversation"
                    aria-label={`Delete ${conv.title}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })
        )}

        {/* Load More Button */}
        {hasMore && (
          <div className="pt-2 text-center">
            <button
              type="button"
              onClick={onLoadMore}
              disabled={loadingMore}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            >
              {loadingMore && <Loader2 className="h-3 w-3 animate-spin" />}
              <span>Load older conversations</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
