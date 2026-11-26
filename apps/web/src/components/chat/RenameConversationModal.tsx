"use client";

import { useEffect, useState } from "react";
import { Edit2, Loader2, X } from "lucide-react";
import { renameConversation } from "@/lib/chat-client";
import type { ChatConversation } from "@/types/chat";

interface RenameConversationModalProps {
  conversation: ChatConversation | null;
  isOpen: boolean;
  onClose: () => void;
  onConversationRenamed: (updated: ChatConversation) => void;
}

export function RenameConversationModal({
  conversation,
  isOpen,
  onClose,
  onConversationRenamed,
}: RenameConversationModalProps) {
  const [title, setTitle] = useState(conversation?.title || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && conversation) {
      setTitle(conversation.title || "");
      setError(null);
    }
  }, [isOpen, conversation]);

  if (!isOpen || !conversation) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) {
      setError("Conversation title cannot be empty.");
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const updated = await renameConversation(conversation.id, { title: trimmed });
      onConversationRenamed(updated);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename conversation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="rename-chat-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl transition-all">
        <div className="flex items-center justify-between pb-4 border-b border-gray-100">
          <div className="flex items-center gap-2 text-gray-900">
            <Edit2 className="h-5 w-5 text-blue-600" />
            <h2 id="rename-chat-modal-title" className="text-lg font-bold">Rename Conversation</h2>
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
          <div className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700 border border-red-200">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label htmlFor="rename-title-input" className="block text-sm font-semibold text-gray-700">
              New Title <span className="text-red-500">*</span>
            </label>
            <input
              id="rename-title-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Operating Systems Concurrency"
              maxLength={255}
              required
              className="mt-1 block w-full rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={loading}
              autoFocus
            />
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
              <span>Save Changes</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
