"use client";

import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Layers,
  Loader2,
  Trash2,
  XCircle,
} from "lucide-react";

interface ExtractionStatusBadgeProps {
  uploadStatus?: string;
  extractionStatus?: string;
  status?: string;
}

export function ExtractionStatusBadge({
  uploadStatus,
  extractionStatus,
  status,
}: ExtractionStatusBadgeProps) {
  const effectiveUpload = uploadStatus || "uploaded";
  const effectiveExtraction = extractionStatus || status || "pending";

  if (effectiveUpload === "pending_upload") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-50 px-2.5 py-0.5 text-xs font-medium text-yellow-700 ring-1 ring-yellow-600/20 ring-inset">
        <Clock className="h-3 w-3 animate-pulse" />
        Upload pending
      </span>
    );
  }

  if (effectiveUpload === "upload_failed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700 ring-1 ring-red-600/20 ring-inset">
        <AlertCircle className="h-3 w-3" />
        Upload failed
      </span>
    );
  }

  if (effectiveUpload === "deleted" || effectiveExtraction === "deleted" || effectiveExtraction === "deleting") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600 ring-1 ring-gray-500/20 ring-inset">
        <Trash2 className="h-3 w-3" />
        Deleted
      </span>
    );
  }

  switch (effectiveExtraction) {
    case "pending":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-blue-600/20 ring-inset">
          <Clock className="h-3 w-3" />
          Awaiting extraction
        </span>
      );
    case "queued":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-600/20 ring-inset">
          <Layers className="h-3 w-3" />
          Queued
        </span>
      );
    case "processing":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-blue-600/20 ring-inset">
          <Loader2 className="h-3 w-3 animate-spin" />
          Processing
        </span>
      );
    case "completed":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700 ring-1 ring-green-600/20 ring-inset">
          <CheckCircle2 className="h-3 w-3" />
          Ready
        </span>
      );
    case "completed_no_text":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-amber-600/20 ring-inset">
          <AlertTriangle className="h-3 w-3" />
          Scanned note
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700 ring-1 ring-red-600/20 ring-inset">
          <XCircle className="h-3 w-3" />
          Extraction failed
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-50 px-2.5 py-0.5 text-xs font-medium text-gray-600 ring-1 ring-gray-500/20 ring-inset">
          {extractionStatus}
        </span>
      );
  }
}
