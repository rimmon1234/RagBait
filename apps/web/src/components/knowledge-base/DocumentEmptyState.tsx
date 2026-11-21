"use client";

import { BookOpen, FolderPlus, UploadCloud } from "lucide-react";

interface DocumentEmptyStateProps {
  title?: string;
  description?: string;
  onUploadClick?: () => void;
  uploadButtonText?: string;
}

export function DocumentEmptyState({
  title = "No study documents found",
  description = "No notes or documents have been uploaded to this section yet.",
  onUploadClick,
  uploadButtonText = "Upload Note / Material",
}: DocumentEmptyStateProps) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white p-12 text-center shadow-sm">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-blue-50 text-blue-600">
        <BookOpen className="h-7 w-7" />
      </div>
      <h3 className="mt-4 text-base font-semibold text-gray-900">{title}</h3>
      <p className="mx-auto mt-1 max-w-sm text-sm text-gray-500">{description}</p>
      {onUploadClick && (
        <div className="mt-6">
          <button
            type="button"
            onClick={onUploadClick}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <UploadCloud className="h-4 w-4" />
            {uploadButtonText}
          </button>
        </div>
      )}
    </div>
  );
}
