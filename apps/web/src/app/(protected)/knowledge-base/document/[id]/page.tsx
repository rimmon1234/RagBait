"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  Download,
  FileText,
  Loader2,
} from "lucide-react";
import {
  getDocument,
  getDocumentPages,
  getDownloadUrl,
} from "@/lib/knowledge-base/knowledge-base-api";
import { DocumentPageViewer } from "@/components/knowledge-base/DocumentPageViewer";
import type {
  DocumentDetail,
  DocumentPageItem,
} from "@/lib/knowledge-base/knowledge-base-types";

const PAGE_BATCH_SIZE = 100;

function DocumentViewerContent() {
  const router = useRouter();
  const routeParams = useParams<{ id: string }>();
  const searchParams = useSearchParams();

  const documentId = routeParams?.id;
  const rawPageParam = searchParams.get("page");
  const requestedPage = rawPageParam ? Math.max(1, Number.parseInt(rawPageParam, 10) || 1) : 1;

  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [pages, setPages] = useState<DocumentPageItem[]>([]);
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const loadDocumentAndTargetPage = useCallback(async () => {
    if (!documentId) return;

    setLoading(true);
    setError(null);
    setPageNotice(null);

    try {
      const doc = await getDocument(documentId);
      setDocument(doc);

      if (doc.extraction_status === "completed_no_text") {
        setPages([]);
        setCurrentPageIndex(0);
        return;
      }

      // Compute primary 100-page slice containing requested physical page number
      const initialOffset = Math.max(
        0,
        Math.floor((requestedPage - 1) / PAGE_BATCH_SIZE) * PAGE_BATCH_SIZE
      );
      const firstBatch = await getDocumentPages(documentId, PAGE_BATCH_SIZE, initialOffset);

      let activeItems = firstBatch.items || [];
      let matchIdx = activeItems.findIndex((p) => p.page_number === requestedPage);

      // Scan remaining batches only if sparse/non-contiguous numbering shifted the physical page
      if (matchIdx === -1 && firstBatch.total > activeItems.length) {
        for (let offset = 0; offset < firstBatch.total; offset += PAGE_BATCH_SIZE) {
          if (offset === initialOffset) continue;
          const batch = await getDocumentPages(documentId, PAGE_BATCH_SIZE, offset);
          const idx = batch.items.findIndex((p) => p.page_number === requestedPage);
          if (idx !== -1) {
            activeItems = batch.items;
            matchIdx = idx;
            break;
          }
        }
      }

      if (matchIdx === -1) {
        // Fallback safely to first available batch & surface clear notice
        const fallbackBatch =
          initialOffset === 0 ? firstBatch : await getDocumentPages(documentId, PAGE_BATCH_SIZE, 0);
        setPages(fallbackBatch.items || []);
        setCurrentPageIndex(0);
        if (rawPageParam) {
          setPageNotice(
            `Physical page ${requestedPage} had no extracted text slice; displaying available pages.`
          );
        }
      } else {
        setPages(activeItems);
        setCurrentPageIndex(matchIdx);
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Document not found, unavailable, or you do not have permission to view it."
      );
    } finally {
      setLoading(false);
    }
  }, [documentId, rawPageParam, requestedPage]);

  useEffect(() => {
    void loadDocumentAndTargetPage();
  }, [loadDocumentAndTargetPage]);

  const handleDownload = async () => {
    if (!document) return;
    try {
      setDownloading(true);
      const res = await getDownloadUrl(document.id);
      if (res?.download_url) {
        const anchor = window.document.createElement("a");
        anchor.href = res.download_url;
        anchor.download = res.original_filename || document.original_filename;
        anchor.target = "_blank";
        window.document.body.appendChild(anchor);
        anchor.click();
        window.document.body.removeChild(anchor);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate download URL.");
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[calc(100vh-4rem)] flex-col items-center justify-center gap-3 text-sm text-gray-500">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        <span>Loading document and navigating to page {requestedPage}...</span>
      </div>
    );
  }

  if (error || !document) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 text-center">
        <AlertCircle className="mx-auto h-10 w-10 text-red-500" />
        <h1 className="mt-3 text-lg font-bold text-gray-900">Document Unavailable</h1>
        <p className="mt-1 text-sm text-gray-600">
          {error || "This document has been archived, deleted, or is outside your access scope."}
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => router.back()}
            className="inline-flex items-center gap-1.5 rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <ArrowLeft className="h-4 w-4" />
            Go Back
          </button>
          <Link
            href="/knowledge-base"
            className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            Knowledge Base
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 pb-4">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
            <FileText className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-gray-900 truncate sm:text-xl">
              {document.title}
            </h1>
            <p className="text-xs text-gray-500">
              {document.original_filename} &bull; Target physical page {requestedPage}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Link
            href="/chat"
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Study Chat
          </Link>
          <button
            type="button"
            onClick={handleDownload}
            disabled={downloading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {downloading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            <span>Download Original File</span>
          </button>
        </div>
      </div>

      {pageNotice && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900">
          {pageNotice}
        </div>
      )}

      <DocumentPageViewer
        pages={pages}
        currentPageIndex={currentPageIndex}
        onPageChange={setCurrentPageIndex}
        isScannedNote={document.extraction_status === "completed_no_text"}
      />
    </div>
  );
}

export default function DocumentViewerPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-[calc(100vh-4rem)] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      }
    >
      <DocumentViewerContent />
    </Suspense>
  );
}
