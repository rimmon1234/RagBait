"use client";

import { useEffect, useState } from "react";
import { BookOpen, Loader2, X } from "lucide-react";
import type { AdminSubjectItem, SubjectCreateRequest } from "@/lib/knowledge-base/knowledge-base-admin-types";

const ALLOWED_ICONS = [
  "BookOpen",
  "Code",
  "Cpu",
  "Database",
  "Globe",
  "Layers",
  "Server",
  "Terminal",
  "Brain",
  "Compass",
  "FileText",
  "Bookmark",
];

interface Props {
  isOpen: boolean;
  subject?: AdminSubjectItem | null;
  isLoading?: boolean;
  onSave: (data: SubjectCreateRequest) => Promise<void> | void;
  onClose: () => void;
}

export function SubjectFormModal({
  isOpen,
  subject,
  isLoading = false,
  onSave,
  onClose,
}: Props) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [iconName, setIconName] = useState("BookOpen");
  const [displayOrder, setDisplayOrder] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (subject) {
      setName(subject.name);
      setSlug(subject.slug);
      setDescription(subject.description || "");
      setIconName(subject.icon_name);
      setDisplayOrder(subject.display_order);
    } else {
      setName("");
      setSlug("");
      setDescription("");
      setIconName("BookOpen");
      setDisplayOrder(0);
    }
    setError(null);
  }, [subject, isOpen]);

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
    if (!subject) {
      // Auto-generate slug suggestion for new subjects
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
      setError("Subject name is required.");
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
        icon_name: iconName,
        display_order: displayOrder,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save subject.");
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
        aria-labelledby="subject-form-title"
        className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-xl transition-all"
      >
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-blue-600" />
            <h3 id="subject-form-title" className="text-base font-semibold text-gray-900">
              {subject ? "Edit Academic Subject" : "Create Academic Subject"}
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
          <div>
            <label htmlFor="subject-name" className="block text-xs font-medium text-gray-700">Subject Name *</label>
            <input
              id="subject-name"
              type="text"
              required
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="e.g. Operating Systems"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="subject-slug" className="block text-xs font-medium text-gray-700">URL Slug *</label>
            <input
              id="subject-slug"
              type="text"
              required
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="e.g. operating-systems"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <p className="mt-1 text-xs text-gray-400">Lowercase letters, digits, and single hyphens only.</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="subject-icon" className="block text-xs font-medium text-gray-700">Display Icon</label>
              <select
                id="subject-icon"
                value={iconName}
                onChange={(e) => setIconName(e.target.value)}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {ALLOWED_ICONS.map((icon) => (
                  <option key={icon} value={icon}>
                    {icon}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="subject-order" className="block text-xs font-medium text-gray-700">Display Order</label>
              <input
                id="subject-order"
                type="number"
                min={0}
                value={displayOrder}
                onChange={(e) => setDisplayOrder(parseInt(e.target.value, 10) || 0)}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          <div>
            <label htmlFor="subject-description" className="block text-xs font-medium text-gray-700">Description</label>
            <textarea
              id="subject-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief summary of syllabus or study materials covered..."
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
              {subject ? "Update Subject" : "Create Subject"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
