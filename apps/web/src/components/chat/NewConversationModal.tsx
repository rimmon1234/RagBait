"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, MessageSquarePlus, X } from "lucide-react";
import type { SubjectRead } from "@/lib/knowledge-base/knowledge-base-types";
import { ApiError } from "@/lib/api";
import { createConversation } from "@/lib/chat-client";
import type { ChatConversation } from "@/types/chat";

interface NewConversationModalProps {
  isOpen: boolean;
  onClose: () => void;
  subjects: SubjectRead[];
  defaultSubjectId?: string;
  onConversationCreated: (conversation: ChatConversation) => void;
}

export function NewConversationModal({
  isOpen,
  onClose,
  subjects,
  defaultSubjectId,
  onConversationCreated,
}: NewConversationModalProps) {
  const [selectedSubjectId, setSelectedSubjectId] = useState<string>(
    defaultSubjectId || (subjects.length > 0 ? subjects[0].id : "")
  );
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contributionRequired, setContributionRequired] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setContributionRequired(false);
    setTitle("");
    setSelectedSubjectId((prev) => {
      if (defaultSubjectId) return defaultSubjectId;
      if (prev && subjects.some((s) => s.id === prev)) return prev;
      return subjects.length > 0 ? subjects[0].id : "";
    });
  }, [isOpen, defaultSubjectId, subjects]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    if (!selectedSubjectId) {
      setError("Please select an academic subject.");
      return;
    }

    try {
      setLoading(true);
      setError(null);
      setContributionRequired(false);
      const conversation = await createConversation({
        subject_id: selectedSubjectId,
        title: title.trim() || undefined,
      });
      setTitle("");
      onConversationCreated(conversation);
      onClose();
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.status === 403 &&
        err.code === "CHAT_CONTRIBUTION_REQUIRED"
      ) {
        setContributionRequired(true);
      }
      setError(err instanceof Error ? err.message : "Failed to create conversation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-chat-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl transition-all">
        <div className="flex items-center justify-between pb-4 border-b border-gray-100">
          <div className="flex items-center gap-2 text-gray-900">
            <MessageSquarePlus className="h-5 w-5 text-blue-600" />
            <h2 id="new-chat-modal-title" className="text-lg font-bold">New Study Conversation</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="Close modal"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {error && (
          <div className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700 border border-red-200 space-y-2">
            <p>{error}</p>
            {contributionRequired && (
              <div className="flex flex-wrap gap-3 text-xs font-semibold">
                <Link
                  href="/access/contribution-required"
                  onClick={onClose}
                  className="text-red-800 underline hover:text-red-900"
                >
                  View contribution requirements
                </Link>
                <Link
                  href="/credentials"
                  onClick={onClose}
                  className="text-blue-700 underline hover:text-blue-800"
                >
                  Connect OpenRouter key
                </Link>
              </div>
            )}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="subject-select" className="block text-sm font-semibold text-gray-700">
              Academic Subject <span className="text-red-500">*</span>
            </label>
            <select
              id="subject-select"
              value={selectedSubjectId}
              onChange={(e) => setSelectedSubjectId(e.target.value)}
              className="mt-1 block w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={loading}
              required
            >
              {subjects.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="title-input" className="block text-sm font-semibold text-gray-700">
              Conversation Title (Optional)
            </label>
            <input
              id="title-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Chapter 4 Practice Questions"
              maxLength={255}
              className="mt-1 block w-full rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={loading}
            />
            <p className="mt-1 text-xs text-gray-500">
              If omitted, will default to &quot;New conversation — [Subject]&quot;.
            </p>
          </div>

          <div className="flex items-center justify-end gap-3 pt-4 border-t border-gray-100">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
            >
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              <span>Create Chat</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
