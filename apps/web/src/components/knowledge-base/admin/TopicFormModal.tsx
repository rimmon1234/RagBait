"use client";

import { useEffect, useState } from "react";
import { FolderTree, Loader2, X } from "lucide-react";
import type { AdminTopicItem, TopicCreateRequest } from "@/lib/knowledge-base/knowledge-base-admin-types";

interface Props {
  isOpen: boolean;
  subjectName: string;
  topic?: AdminTopicItem | null;
  isLoading?: boolean;
  onSave: (data: TopicCreateRequest) => Promise<void> | void;
  onClose: () => void;
}

export function TopicFormModal({
  isOpen,
  subjectName,
  topic,
  isLoading = false,
  onSave,
  onClose,
}: Props) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [displayOrder, setDisplayOrder] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (topic) {
      setName(topic.name);
      setSlug(topic.slug);
      setDescription(topic.description || "");
      setDisplayOrder(topic.display_order);
    } else {
      setName("");
      setSlug("");
      setDescription("");
      setDisplayOrder(0);
    }
    setError(null);
  }, [topic, isOpen]);

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

  const handleNameChange = (val: string) => {
    setName(val);
    if (!topic) {
      const generated = val
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      setSlug(generated);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Topic name is required.");
      return;
    }
    if (!slug.trim() || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug.trim())) {
      setError("Slug must be lowercase alphanumeric with single hyphens.");
      return;
    }

    try {
      setError(null);
      await onSave({
        name: name.trim(),
        slug: slug.trim().toLowerCase(),
        description: description.trim() || null,
        display_order: displayOrder,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save topic.");
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
        aria-labelledby="topic-form-title"
        className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-xl transition-all"
      >
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div className="flex items-center gap-2">
            <FolderTree className="h-5 w-5 text-indigo-600" />
            <div>
              <h3 id="topic-form-title" className="text-base font-semibold text-gray-900">
                {topic ? "Edit Topic" : "Create Topic"}
              </h3>
              <p className="text-xs text-gray-500">Subject: {subjectName}</p>
            </div>
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
          <div>
            <label htmlFor="topic-name" className="block text-xs font-medium text-gray-700">Topic Name *</label>
            <input
              id="topic-name"
              type="text"
              required
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="e.g. Memory Management"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="topic-slug" className="block text-xs font-medium text-gray-700">URL Slug *</label>
            <input
              id="topic-slug"
              type="text"
              required
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="e.g. memory-management"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <p className="mt-1 text-xs text-gray-400">Must be unique within this subject.</p>
          </div>

          <div>
            <label htmlFor="topic-order" className="block text-xs font-medium text-gray-700">Display Order</label>
            <input
              id="topic-order"
              type="number"
              min={0}
              value={displayOrder}
              onChange={(e) => setDisplayOrder(parseInt(e.target.value, 10) || 0)}
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="topic-description" className="block text-xs font-medium text-gray-700">Description</label>
            <textarea
              id="topic-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief description of materials under this topic..."
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

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
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
            >
              {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              {topic ? "Update Topic" : "Create Topic"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
