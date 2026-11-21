"use client";

import { AlertTriangle, ChevronLeft, ChevronRight, FileText } from "lucide-react";
import type { DocumentPageItem } from "@/lib/knowledge-base/knowledge-base-types";

interface DocumentPageViewerProps {
  pages: DocumentPageItem[];
  currentPageIndex: number;
  onPageChange: (index: number) => void;
  isScannedNote?: boolean;
}

export function DocumentPageViewer({
  pages,
  currentPageIndex,
  onPageChange,
  isScannedNote = false,
}: DocumentPageViewerProps) {
  if (isScannedNote) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-6 text-amber-900">
        <div className="flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
          <div>
            <h4 className="font-semibold text-sm text-amber-900">
              Scanned / Non-extractable Document
            </h4>
            <p className="mt-1 text-xs text-amber-700 leading-relaxed">
              This document contains scanned images or non-selectable text without a digital text layer.
              RagBait&apos;s Phase 3 document ingestion runs deterministic text extraction without OCR.
              You can still download and inspect the original file using the download button above.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (pages.length === 0) {
    return (
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-8 text-center text-sm text-gray-500">
        No extracted text available for this document.
      </div>
    );
  }

  const activePage = pages[currentPageIndex] || pages[0];
  const totalPages = pages.length;

  return (
    <div className="flex flex-col space-y-4">
      {/* Page Navigation & Stats Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 pb-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onPageChange(currentPageIndex - 1)}
            disabled={currentPageIndex <= 0}
            className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-40"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            Previous
          </button>
          <span className="text-xs font-semibold text-gray-800">
            Page {activePage.page_number} of {totalPages}
          </span>
          <button
            type="button"
            onClick={() => onPageChange(currentPageIndex + 1)}
            disabled={currentPageIndex >= totalPages - 1}
            className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-40"
          >
            Next
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span>{activePage.word_count.toLocaleString()} words</span>
          <span>&bull;</span>
          <span>{activePage.character_count.toLocaleString()} characters</span>
        </div>
      </div>

      {/* Extracted Text Body - SAFE PRE tag with no dangerouslySetInnerHTML */}
      <div className="rounded-lg border border-gray-200 bg-gray-50/50 p-4 font-mono text-xs text-gray-800 overflow-x-auto min-h-[300px]">
        {activePage.raw_text ? (
          <pre className="whitespace-pre-wrap font-sans text-sm text-gray-800 leading-relaxed select-text">
            {activePage.raw_text}
          </pre>
        ) : (
          <div className="flex h-32 items-center justify-center text-xs text-gray-400 italic">
            This page contains no text.
          </div>
        )}
      </div>
    </div>
  );
}
