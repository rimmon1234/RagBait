"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";

interface Props {
  isOpen: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  isDestructive?: boolean;
  requireReason?: boolean;
  reasonPlaceholder?: string;
  isLoading?: boolean;
  onConfirm: (reason: string) => Promise<void> | void;
  onClose: () => void;
}

export function AdminConfirmModal({
  isOpen,
  title,
  description,
  confirmLabel = "Confirm",
  isDestructive = false,
  requireReason = false,
  reasonPlaceholder = "Provide a moderation reason...",
  isLoading = false,
  onConfirm,
  onClose,
}: Props) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setReason("");
      setError(null);
    }
  }, [isOpen]);

  // Handle Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isLoading) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isLoading, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (requireReason && (!reason.trim() || reason.trim().length < 3)) {
      setError("A moderation reason of at least 3 characters is required.");
      return;
    }
    setError(null);
    try {
      await onConfirm(reason.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-sm transition-opacity"
        onClick={() => !isLoading && onClose()}
      />

      {/* Modal Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-modal-title"
        className="relative w-full max-w-md rounded-xl bg-white p-6 shadow-xl transition-all"
      >
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div className="flex items-center gap-2">
            {isDestructive ? (
              <AlertTriangle className="h-5 w-5 text-red-600" />
            ) : (
              <div className="h-2 w-2 rounded-full bg-blue-600" />
            )}
            <h3 id="confirm-modal-title" className="text-base font-semibold text-gray-900">
              {title}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isLoading}
            className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-500 disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <p className="text-sm text-gray-600">{description}</p>

          {(requireReason || reasonPlaceholder) && (
            <div>
              <label className="block text-xs font-medium text-gray-700">
                Moderation Reason {requireReason && <span className="text-red-500">*</span>}
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={reasonPlaceholder}
                disabled={isLoading}
                rows={3}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isLoading}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white shadow-sm disabled:opacity-50 ${
                isDestructive
                  ? "bg-red-600 hover:bg-red-700 focus:ring-red-500"
                  : "bg-blue-600 hover:bg-blue-700 focus:ring-blue-500"
              }`}
            >
              {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
