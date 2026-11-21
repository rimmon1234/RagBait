"use client";

import { useEffect, useState } from "react";
import { Download, FileText, Loader2, X } from "lucide-react";
import { getDocumentPages } from "@/lib/knowledge-base/knowledge-base-api";
import { DocumentPageViewer } from "./DocumentPageViewer";
import type {
  DocumentListItem,
  DocumentPageItem,
} from "@/lib/knowledge-base/knowledge-base-types";

interface DocumentPreviewDrawerProps {
  document: DocumentListItem | null;
  isOpen: boolean;
  onClose: () => void;
  onDownload: (doc: DocumentListItem) => Promise<void>;
}

export function DocumentPreviewDrawer({
  document,
  isOpen,
  onClose,
  onDownload,
}: DocumentPreviewDrawerProps) {
  const [pages, setPages] = useState<DocumentPageItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!isOpen || !document) {
      setPages([]);
      setCurrentPageIndex(0);
      setError(null);
      return;
    }

    if (document.extraction_status === "completed_no_text") {
      setPages([]);
      setLoading(false);
      return;
    }

    const loadPages = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await getDocumentPages(document.id, 100, 0);
        setPages(res.items);
        setCurrentPageIndex(0);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load document pages");
      } finally {
        setLoading(false);
      }
    };

    loadPages();
  }, [isOpen, document]);

  if (!isOpen || !document) return null;

  const handleDownload = async () => {
    try {
      setDownloading(true);
      await onDownload(document);
    } finally {
      setDownloading(false);
    }
  };

  const isScanned = document.extraction_status === "completed_no_text";

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
        <div className="w-screen max-w-2xl bg-white shadow-2xl flex flex-col">
          {/* Drawer Header */}
          <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                <FileText className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <h3 className="text-base font-semibold text-gray-900 truncate">
                  {document.title}
                </h3>
                <p className="text-xs text-gray-500 truncate">
                  {document.original_filename} &bull; {document.uploader_display_name || "Uploaded document"}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
              >
                {downloading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
                ) : (
                  <Download className="h-3.5 w-3.5 text-gray-500" />
                )}
                <span>Original File</span>
              </button>

              <button
                type="button"
                onClick={onClose}
                className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                aria-label="Close preview"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          {/* Drawer Content */}
          <div className="flex-1 overflow-y-auto p-6">
            {loading ? (
              <div className="flex h-64 flex-col items-center justify-center gap-3 text-sm text-gray-500">
                <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                <span>Loading extracted text pages...</span>
              </div>
            ) : error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-xs text-red-700">
                {error}
              </div>
            ) : (
              <DocumentPageViewer
                pages={pages}
                currentPageIndex={currentPageIndex}
                onPageChange={setCurrentPageIndex}
                isScannedNote={isScanned}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
