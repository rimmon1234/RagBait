"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Clock,
  Eye,
  Filter,
  ListTodo,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import {
  getAdminJob,
  getAdminJobs,
  retryAdminJob,
} from "@/lib/knowledge-base/knowledge-base-admin-api";
import type {
  AdminJobDetail,
  AdminJobListItem,
} from "@/lib/knowledge-base/knowledge-base-admin-types";
import { AdminConfirmModal } from "@/components/knowledge-base/admin/AdminConfirmModal";
import { AdminKnowledgeBaseNavigation } from "@/components/knowledge-base/admin/AdminKnowledgeBaseNavigation";
import { JobDetailDrawer } from "@/components/knowledge-base/admin/JobDetailDrawer";
import { KnowledgeBaseAdminHeader } from "@/components/knowledge-base/admin/KnowledgeBaseAdminHeader";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";

export default function ExtractionJobsAdminPage() {
  const [jobs, setJobs] = useState<AdminJobListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [limit] = useState(20);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState("");

  // Drawer & Modals state
  const [selectedJobDetail, setSelectedJobDetail] = useState<AdminJobDetail | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  // Retry Confirm Dialog
  const [retryDialog, setRetryDialog] = useState<{
    isOpen: boolean;
    job: AdminJobListItem | AdminJobDetail | null;
  }>({
    isOpen: false,
    job: null,
  });

  const loadJobs = useCallback(
    async (silent = false) => {
      try {
        if (!silent) setLoading(true);
        setError(null);
        const data = await getAdminJobs({
          limit,
          offset,
          status: statusFilter || undefined,
          search: search.trim() || undefined,
        });
        setJobs(data.items);
        setTotal(data.total);
      } catch (err) {
        if (!silent) {
          setError(err instanceof Error ? err.message : "Failed to load extraction jobs.");
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [limit, offset, statusFilter, search]
  );

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // Live Bounded Polling (every 5 seconds, pauses when tab is hidden)
  useEffect(() => {
    let timerId: NodeJS.Timeout | null = null;

    const poll = async () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      await loadJobs(true);
    };

    timerId = setInterval(poll, 5000);

    const handleVisibilityChange = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        loadJobs(true);
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      if (timerId) clearInterval(timerId);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [loadJobs]);

  const openDrawer = async (jobId: string) => {
    try {
      const detail = await getAdminJob(jobId);
      setSelectedJobDetail(detail);
      setIsDrawerOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load job details.");
    }
  };

  const handleTriggerRetry = async (reason: string) => {
    if (!retryDialog.job) return;
    try {
      await retryAdminJob(retryDialog.job.id, reason);
      setRetryDialog({ isOpen: false, job: null });
      setIsDrawerOpen(false);
      loadJobs();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to trigger retry.");
    }
  };

  return (
    <div>
      <KnowledgeBaseAdminHeader
        title="Extraction Queue & Workers"
        description="Monitor durable PostgreSQL extraction queue, inspect worker failures, and safely retry eligible jobs."
        actions={
          <button
            type="button"
            onClick={() => loadJobs()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        }
      />

      <AdminKnowledgeBaseNavigation />

      {/* Filter / Search Bar */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            loadJobs();
          }}
          className="relative max-w-sm flex-1"
        >
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by document title or error code..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-gray-300 pl-9 pr-4 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </form>

        <div className="flex items-center gap-2 text-xs">
          <label className="font-medium text-gray-600">Filter Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setOffset(0);
            }}
            className="rounded-lg border border-gray-300 p-2 text-xs shadow-sm focus:border-blue-500 focus:ring-blue-500"
          >
            <option value="">All Statuses</option>
            <option value="queued">Queued</option>
            <option value="processing">Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      {loading && jobs.length === 0 ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-sm font-semibold text-red-800">Failed to load jobs</h3>
          <p className="mt-1 text-xs text-red-600">{error}</p>
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
          <ListTodo className="mx-auto h-12 w-12 text-gray-300" />
          <h3 className="mt-2 text-base font-semibold text-gray-900">No extraction jobs</h3>
          <p className="mt-1 text-sm text-gray-500">
            No extraction jobs match your selected filters.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-4 py-3 font-medium">Job & Document</th>
                  <th className="px-4 py-3 font-medium">Subject / Topic</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Attempts & Lineage</th>
                  <th className="px-4 py-3 font-medium">Error Code</th>
                  <th className="px-4 py-3 font-medium">Created</th>
                  <th className="px-4 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-gray-50/80 transition-colors">
                    <td className="px-4 py-3 font-medium text-gray-900">
                      <p className="truncate max-w-xs">{job.document_title}</p>
                      <p className="font-mono text-[11px] text-gray-400">Job: {job.id.slice(0, 8)}</p>
                    </td>

                    <td className="px-4 py-3 text-gray-600">
                      <p className="font-medium text-gray-800">{job.subject_name}</p>
                      <p className="text-[11px] text-gray-400">{job.topic_name}</p>
                    </td>

                    <td className="px-4 py-3">
                      <ExtractionStatusBadge status={job.status} />
                    </td>

                    <td className="px-4 py-3 text-gray-600">
                      <p>Attempt {job.attempts} / {job.max_attempts}</p>
                      <p className="text-[11px] text-gray-400">
                        {job.retry_count > 0 ? `Manual retry #${job.retry_count}` : "Initial extraction"}
                      </p>
                    </td>

                    <td className="px-4 py-3 font-mono text-[11px]">
                      {job.error_code ? (
                        <span className="text-red-700 font-semibold">{job.error_code}</span>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>

                    <td className="px-4 py-3 text-gray-500">
                      {new Date(job.created_at).toLocaleTimeString()}
                    </td>

                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => openDrawer(job.id)}
                          className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-blue-600"
                          title="Inspect job"
                        >
                          <Eye className="h-4 w-4" />
                        </button>

                        {job.is_retryable && (
                          <button
                            type="button"
                            onClick={() => setRetryDialog({ isOpen: true, job })}
                            className="rounded p-1 text-blue-600 hover:bg-blue-50"
                            title="Retry extraction"
                          >
                            <RotateCcw className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination Footer */}
          <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
            <span>
              Showing {jobs.length > 0 ? offset + 1 : 0} to {Math.min(offset + limit, total)} of {total} jobs
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOffset((prev) => Math.max(0, prev - limit))}
                disabled={offset === 0}
                className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => setOffset((prev) => prev + limit)}
                disabled={offset + limit >= total}
                className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Job Details Drawer */}
      <JobDetailDrawer
        isOpen={isDrawerOpen}
        job={selectedJobDetail}
        onRetry={(job) => setRetryDialog({ isOpen: true, job })}
        onClose={() => {
          setIsDrawerOpen(false);
          setSelectedJobDetail(null);
        }}
      />

      {/* Retry Confirmation Modal */}
      <AdminConfirmModal
        isOpen={retryDialog.isOpen}
        title="Retry Extraction Job"
        description={`Requeue extraction for '${retryDialog.job?.document_title}'? A fresh queued job will be created and picked up by the background extraction worker.`}
        confirmLabel="Retry Extraction"
        isDestructive={false}
        requireReason={false}
        reasonPlaceholder="Optional administrative retry note..."
        onConfirm={handleTriggerRetry}
        onClose={() => setRetryDialog({ isOpen: false, job: null })}
      />
    </div>
  );
}
