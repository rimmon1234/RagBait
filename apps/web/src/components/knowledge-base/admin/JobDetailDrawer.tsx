"use client";

import { useEffect } from "react";
import { AlertCircle, Clock, FileText, RotateCcw, X } from "lucide-react";
import type { AdminJobDetail } from "@/lib/knowledge-base/knowledge-base-admin-types";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";

interface Props {
  isOpen: boolean;
  job: AdminJobDetail | null;
  onRetry: (job: AdminJobDetail) => void;
  onClose: () => void;
}

export function JobDetailDrawer({ isOpen, job, onRetry, onClose }: Props) {
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !job) return null;

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
          aria-labelledby="job-drawer-title"
          className="w-screen max-w-xl bg-white shadow-2xl flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                <Clock className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <h3 id="job-drawer-title" className="text-base font-semibold text-gray-900 truncate">
                  Job {job.id.slice(0, 8)}
                </h3>
                <p className="text-xs text-gray-500 truncate">
                  Document: {job.document_title}
                </p>
              </div>
            </div>

            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-500"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Action Toolbar */}
          <div className="flex items-center justify-between bg-gray-50 px-6 py-3 border-b border-gray-200">
            <div className="flex items-center gap-2">
              <ExtractionStatusBadge status={job.status} />
              <span className="text-xs text-gray-500">
                Attempt {job.attempts} of {job.max_attempts}
              </span>
            </div>

            {job.is_retryable && (
              <button
                type="button"
                onClick={() => onRetry(job)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-blue-700"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span>Retry Extraction</span>
              </button>
            )}
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {/* Eligibility Note */}
            {job.retry_eligibility_reason && (
              <div
                className={`rounded-lg p-4 border text-xs ${
                  job.is_retryable
                    ? "bg-green-50 border-green-200 text-green-800"
                    : "bg-gray-50 border-gray-200 text-gray-700"
                }`}
              >
                <div className="flex items-center gap-2 font-semibold">
                  <AlertCircle className="h-4 w-4" />
                  <span>Retry Policy Status</span>
                </div>
                <p className="mt-1">{job.retry_eligibility_reason}</p>
              </div>
            )}

            {/* Error Detail */}
            {job.error_message && (
              <div className="rounded-lg bg-red-50 p-4 border border-red-200">
                <p className="text-xs font-semibold text-red-800">
                  Failure Reason ({job.error_code || "UNKNOWN"})
                </p>
                <p className="mt-1 text-xs text-red-700">{job.error_message}</p>
              </div>
            )}

            {/* Job Metadata */}
            <div className="rounded-xl border border-gray-200 bg-gray-50/50 p-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
                Job Execution Parameters
              </h4>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                <div>
                  <dt className="text-gray-400">Job ID</dt>
                  <dd className="font-mono text-gray-900 truncate">{job.id}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Document ID</dt>
                  <dd className="font-mono text-gray-900 truncate">{job.document_id}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Subject / Topic</dt>
                  <dd className="font-medium text-gray-900">{job.subject_name} • {job.topic_name}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Uploader</dt>
                  <dd className="font-medium text-gray-900">{job.uploader_display_name || "N/A"}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Scheduled At</dt>
                  <dd className="font-medium text-gray-900">{new Date(job.scheduled_at).toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Created At</dt>
                  <dd className="font-medium text-gray-900">{new Date(job.created_at).toLocaleString()}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Manual Retry Count</dt>
                  <dd className="font-medium text-gray-900">{job.retry_count} (max 2)</dd>
                </div>
                {job.retry_of_job_id && (
                  <div>
                    <dt className="text-gray-400">Preceding Job ID</dt>
                    <dd className="font-mono text-gray-700 truncate">{job.retry_of_job_id}</dd>
                  </div>
                )}
              </dl>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
