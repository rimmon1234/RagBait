"use client";

import { useEffect, useState } from "react";
import {
  Archive,
  Download,
  FileText,
  Loader2,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { getDocumentPages, getDownloadUrl } from "@/lib/knowledge-base/knowledge-base-api";
import type { AdminDocumentDetail } from "@/lib/knowledge-base/knowledge-base-admin-types";
import type { DocumentPageItem } from "@/lib/knowledge-base/knowledge-base-types";
import { DocumentPageViewer } from "@/components/knowledge-base/DocumentPageViewer";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";

interface Props {
  isOpen: boolean;
  document: AdminDocumentDetail | null;
  onArchive: (doc: AdminDocumentDetail) => void;
  onRestore: (doc: AdminDocumentDetail) => void;
  onDelete: (doc: AdminDocumentDetail) => void;
  onClose: () => void;
}

export function DocumentModerationDrawer({
  isOpen,
  document,
  onArchive,
  onRestore,
  onDelete,
  onClose,
}: Props) {
  const [downloading, setDownloading] = useState(false);
  const [pages, setPages] = useState<DocumentPageItem[]>([]);
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [pagesLoading, setPagesLoading] = useState(false);

  useEffect(() => {
    if (!isOpen || !document) {
      setPages([]);
      setCurrentPageIndex(0);
      return;
    }

    if (document.extraction_status === "completed_no_text") {
      setPages([]);
      return;
    }

    const loadPages = async () => {
      try {
        setPagesLoading(true);
        const res = await getDocumentPages(document.id, 100, 0);
        setPages(res.items || []);
        setCurrentPageIndex(0);
      } catch {
        setPages([]);
      } finally {
        setPagesLoading(false);
      }
    };

    loadPages();
  }, [isOpen, document]);

  // Handle Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !document) return null;

  const handleDownload = async () => {
    try {
      setDownloading(true);
      const res = await getDownloadUrl(document.id);
      window.open(res.download_url, "_blank");
    } catch {
      // Error handling
    } finally {
      setDownloading(false);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1048576).toFixed(1)} MiB`;
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="moderation-drawer-title"
          className="w-screen max-w-2xl bg-white shadow-2xl flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                <FileText className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <h3 id="moderation-drawer-title" className="text-base font-semibold text-gray-900 truncate">
                  {document.title}
                </h3>
                <p className="text-xs text-gray-500 truncate">
                  {document.subject_name} • {document.topic_name}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading || document.upload_status !== "uploaded"}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {downloading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                <span>Download</span>
              </button>

              <button
                type="button"
                onClick={onClose}
                className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-500"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          {/* Action Toolbar */}
          <div className="flex items-center justify-between bg-gray-50 px-6 py-2.5 border-b border-gray-200">
            <div className="flex items-center gap-2">
              <ExtractionStatusBadge status={document.extraction_status} />
              {document.is_archived && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  Archived
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              {document.is_archived ? (
                <button
                  type="button"
                  onClick={() => onRestore(document)}
                  className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                >
                  <Undo2 className="h-3.5 w-3.5" />
                  Restore
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onArchive(document)}
                  className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
                >
                  <Archive className="h-3.5 w-3.5" />
                  Archive
                </button>
              )}

              <button
                type="button"
                onClick={() => onDelete(document)}
                className="inline-flex items-center gap-1 rounded-md bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {/* Metadata Grid */}
            <div className="rounded-xl border border-gray-200 bg-gray-50/50 p-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
                Document Metadata & Provenance
              </h4>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                <div>
                  <dt className="text-gray-400">Original Filename</dt>
                  <dd className="font-medium text-gray-900 truncate">{document.original_filename}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">File Size & Type</dt>
                  <dd className="font-medium text-gray-900">{formatBytes(document.file_size_bytes)} ({document.mime_type})</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Uploader</dt>
                  <dd className="font-medium text-gray-900">{document.uploader_display_name || "Unknown"} ({document.uploader_email || "N/A"})</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Upload Date</dt>
                  <dd className="font-medium text-gray-900">{new Date(document.created_at).toLocaleString()}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-gray-400">Storage Path</dt>
                  <dd className="font-mono text-gray-700 truncate">{document.storage_path}</dd>
                </div>
                {document.sha256_checksum && (
                  <div className="col-span-2">
                    <dt className="text-gray-400">SHA-256 Checksum</dt>
                    <dd className="font-mono text-gray-600 truncate">{document.sha256_checksum}</dd>
                  </div>
                )}
              </dl>
            </div>

            {/* Error Message if any */}
            {document.error_message && (
              <div className="rounded-lg bg-red-50 p-4 border border-red-200">
                <p className="text-xs font-semibold text-red-800">Extraction Error ({document.error_code || "UNKNOWN"})</p>
                <p className="mt-1 text-xs text-red-700">{document.error_message}</p>
              </div>
            )}

            {/* Extracted Pages Preview */}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
                Extracted Text ({document.total_pages} Pages)
              </h4>
              {pagesLoading ? (
                <div className="flex h-32 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : (
                <DocumentPageViewer
                  pages={pages}
                  currentPageIndex={currentPageIndex}
                  onPageChange={setCurrentPageIndex}
                  isScannedNote={document.extraction_status === "completed_no_text"}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
