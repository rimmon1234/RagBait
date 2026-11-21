"use client";

import { useState } from "react";
import {
  Download,
  Eye,
  FileText,
  Loader2,
  Trash2,
} from "lucide-react";
import { formatFileSize } from "./UploadDropzone";
import { ExtractionStatusBadge } from "./ExtractionStatusBadge";
import type { DocumentListItem } from "@/lib/knowledge-base/knowledge-base-types";
import type { UserProfile } from "@/types";

interface DocumentRowProps {
  document: DocumentListItem;
  currentUser: UserProfile | null;
  onPreview: (doc: DocumentListItem) => void;
  onDownload: (doc: DocumentListItem) => Promise<void>;
  onDelete: (doc: DocumentListItem) => Promise<void>;
}

export function DocumentRow({
  document,
  currentUser,
  onPreview,
  onDownload,
  onDelete,
}: DocumentRowProps) {
  const [downloading, setDownloading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isOwner = currentUser?.id && document.uploader_id === currentUser.id;
  const isAdmin = currentUser?.role === "admin" || currentUser?.role === "super_admin";
  const canDelete = isOwner || isAdmin;

  const canPreview =
    document.upload_status === "uploaded" &&
    (document.extraction_status === "completed" ||
      document.extraction_status === "completed_no_text");

  const isPdf = document.mime_type === "application/pdf" || document.original_filename.toLowerCase().endsWith(".pdf");

  const handleDownloadClick = async () => {
    try {
      setDownloading(true);
      await onDownload(document);
    } finally {
      setDownloading(false);
    }
  };

  const handleDeleteClick = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      setDeleting(true);
      await onDelete(document);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50/70 transition-colors">
      {/* Title & Filename */}
      <td className="py-3.5 pl-4 pr-3 text-sm sm:pl-6">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
            <FileText className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium text-gray-900 truncate max-w-xs sm:max-w-md">
              {document.title}
            </p>
            <p className="text-xs text-gray-500 truncate max-w-xs sm:max-w-md">
              {document.original_filename} &bull; {formatFileSize(document.file_size_bytes)}
            </p>
          </div>
        </div>
      </td>

      {/* Format / Type */}
      <td className="hidden px-3 py-3.5 text-xs text-gray-500 sm:table-cell">
        <span className="inline-flex rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700 uppercase">
          {isPdf ? "PDF" : "TXT"}
        </span>
      </td>

      {/* Pages Count */}
      <td className="hidden px-3 py-3.5 text-xs text-gray-500 md:table-cell">
        {document.extraction_status === "completed" ? (
          <span>{document.total_pages} {document.total_pages === 1 ? "page" : "pages"}</span>
        ) : document.extraction_status === "completed_no_text" ? (
          <span className="text-amber-600">Scanned ({document.total_pages} p.)</span>
        ) : (
          <span className="text-gray-400">&mdash;</span>
        )}
      </td>

      {/* Uploader */}
      <td className="hidden px-3 py-3.5 text-xs text-gray-500 lg:table-cell">
        <span className="truncate max-w-[120px] block">
          {document.uploader_display_name || "Anonymous"}
        </span>
      </td>

      {/* Extraction Status */}
      <td className="px-3 py-3.5 text-xs">
        <ExtractionStatusBadge
          uploadStatus={document.upload_status}
          extractionStatus={document.extraction_status}
        />
      </td>

      {/* Actions */}
      <td className="py-3.5 pl-3 pr-4 text-right text-sm font-medium sm:pr-6">
        <div className="flex items-center justify-end gap-1.5">
          {/* Preview Button */}
          {canPreview && (
            <button
              type="button"
              onClick={() => onPreview(document)}
              className="inline-flex items-center gap-1 rounded-md bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm ring-1 ring-gray-300 ring-inset hover:bg-gray-50"
              title="Preview extracted notes"
            >
              <Eye className="h-3.5 w-3.5 text-gray-500" />
              <span>Preview</span>
            </button>
          )}

          {/* Download Original File Button */}
          <button
            type="button"
            onClick={handleDownloadClick}
            disabled={downloading}
            className="inline-flex items-center gap-1 rounded-md bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-sm ring-1 ring-gray-300 ring-inset hover:bg-gray-50 disabled:opacity-50"
            title="Download original file"
          >
            {downloading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
            ) : (
              <Download className="h-3.5 w-3.5 text-gray-500" />
            )}
            <span className="hidden sm:inline">Download</span>
          </button>

          {/* Delete Button */}
          {canDelete && (
            confirmDelete ? (
              <div className="inline-flex items-center gap-1">
                <button
                  type="button"
                  onClick={handleDeleteClick}
                  disabled={deleting}
                  className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white shadow-sm hover:bg-red-700 disabled:opacity-50"
                >
                  {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Confirm"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="rounded-md bg-gray-200 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-300"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
                title="Delete study document"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )
          )}
        </div>
      </td>
    </tr>
  );
}
