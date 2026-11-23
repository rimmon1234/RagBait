"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { deleteConversation } from "@/lib/chat-client";
import type { ChatConversation } from "@/types/chat";

interface DeleteConversationModalProps {
  conversation: ChatConversation | null;
  isOpen: boolean;
  onClose: () => void;
  onConversationDeleted: (deletedId: string) => void;
}

export function DeleteConversationModal({
  conversation,
  isOpen,
  onClose,
  onConversationDeleted,
}: DeleteConversationModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen || !conversation) return null;

  const handleDelete = async () => {
    try {
      setLoading(true);
      setError(null);
      await deleteConversation(conversation.id);
      onConversationDeleted(conversation.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete conversation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-chat-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl transition-all">
        <div className="flex items-center justify-between pb-4 border-b border-gray-100">
          <div className="flex items-center gap-2 text-red-600">
            <AlertTriangle className="h-5 w-5" />
            <h2 id="delete-chat-modal-title" className="text-lg font-bold">Delete Conversation</h2>
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

        <div className="mt-4 space-y-3">
          <p className="text-sm text-gray-700">
            Are you sure you want to delete{" "}
            <span className="font-semibold text-gray-900">&quot;{conversation.title}&quot;</span>?
          </p>
          <div className="rounded-xl bg-red-50 p-3 text-xs text-red-800 border border-red-200">
            This action cannot be undone. The conversation and all its messages will be permanently removed.
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 pt-6 border-t border-gray-100">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-300"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 disabled:opacity-50"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            <span>Delete Conversation</span>
          </button>
        </div>
      </div>
    </div>
  );
}
