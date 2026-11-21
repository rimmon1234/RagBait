"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  FileText,
  Filter,
  History,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";
import { getAdminAuditLogs } from "@/lib/knowledge-base/knowledge-base-admin-api";
import type { AdminAuditItem } from "@/lib/knowledge-base/knowledge-base-admin-types";
import { AdminKnowledgeBaseNavigation } from "@/components/knowledge-base/admin/AdminKnowledgeBaseNavigation";
import { KnowledgeBaseAdminHeader } from "@/components/knowledge-base/admin/KnowledgeBaseAdminHeader";

export default function KnowledgeBaseAuditPage() {
  const [logs, setLogs] = useState<AdminAuditItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [limit] = useState(25);
  const [offset, setOffset] = useState(0);
  const [actionFilter, setActionFilter] = useState<string>("");
  const [targetTypeFilter, setTargetTypeFilter] = useState<string>("");

  const loadAuditLogs = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getAdminAuditLogs({
        limit,
        offset,
        action: actionFilter || undefined,
        target_type: targetTypeFilter || undefined,
      });
      setLogs(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit history.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAuditLogs();
  }, [offset, actionFilter, targetTypeFilter]);

  const renderPayload = (payload: Record<string, unknown>) => {
    if (!payload || Object.keys(payload).length === 0) return null;
    return (
      <div className="mt-1 max-w-md rounded bg-gray-50 p-2 font-mono text-[11px] text-gray-700 overflow-x-auto border border-gray-100">
        {Object.entries(payload).map(([k, v]) => (
          <div key={k} className="truncate">
            <span className="font-semibold text-gray-500">{k}:</span>{" "}
            <span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div>
      <KnowledgeBaseAdminHeader
        title="Knowledge Base Audit Activity"
        description="Immutable administrative decision trail and taxonomy/document mutations."
        actions={
          <button
            type="button"
            onClick={loadAuditLogs}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        }
      />

      <AdminKnowledgeBaseNavigation />

      {/* Filter Bar */}
      <div className="mb-6 flex flex-wrap gap-4 items-center justify-between rounded-xl border border-gray-200 bg-white p-4 shadow-sm text-xs">
        <div className="flex flex-wrap gap-3 items-center">
          <div>
            <label className="font-medium text-gray-600 mr-2">Target Type:</label>
            <select
              value={targetTypeFilter}
              onChange={(e) => {
                setTargetTypeFilter(e.target.value);
                setOffset(0);
              }}
              className="rounded-lg border border-gray-300 p-2 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="">All Types</option>
              <option value="kb_subject">Subjects</option>
              <option value="kb_topic">Topics</option>
              <option value="kb_document">Documents</option>
              <option value="kb_extraction_job">Extraction Jobs</option>
            </select>
          </div>

          <div>
            <label className="font-medium text-gray-600 mr-2">Action:</label>
            <select
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value);
                setOffset(0);
              }}
              className="rounded-lg border border-gray-300 p-2 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="">All Actions</option>
              <option value="kb.subject_create">Subject Create</option>
              <option value="kb.subject_update">Subject Update</option>
              <option value="kb.subject_archive">Subject Archive</option>
              <option value="kb.subject_restore">Subject Restore</option>
              <option value="kb.topic_create">Topic Create</option>
              <option value="kb.topic_update">Topic Update</option>
              <option value="kb.topic_reorder">Topic Reorder</option>
              <option value="kb.topic_archive">Topic Archive</option>
              <option value="kb.topic_restore">Topic Restore</option>
              <option value="kb.document_archive">Document Archive</option>
              <option value="kb.document_restore">Document Restore</option>
              <option value="kb.document_delete">Document Delete</option>
              <option value="kb.job_retry">Job Retry</option>
            </select>
          </div>
        </div>

        {(actionFilter || targetTypeFilter) && (
          <button
            type="button"
            onClick={() => {
              setActionFilter("");
              setTargetTypeFilter("");
              setOffset(0);
            }}
            className="text-xs text-blue-600 hover:text-blue-700"
          >
            Clear filters
          </button>
        )}
      </div>

      {loading && logs.length === 0 ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-sm font-semibold text-red-800">Failed to load audit history</h3>
          <p className="mt-1 text-xs text-red-600">{error}</p>
        </div>
      ) : logs.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
          <History className="mx-auto h-12 w-12 text-gray-300" />
          <h3 className="mt-2 text-base font-semibold text-gray-900">No audit records found</h3>
          <p className="mt-1 text-sm text-gray-500">
            Administrative actions on subjects, topics, documents, and extraction jobs will be recorded here.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-4 py-3 font-medium">Timestamp</th>
                  <th className="px-4 py-3 font-medium">Administrator</th>
                  <th className="px-4 py-3 font-medium">Action</th>
                  <th className="px-4 py-3 font-medium">Target</th>
                  <th className="px-4 py-3 font-medium">Details & Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {logs.map((log) => (
                  <tr key={log.id} className="hover:bg-gray-50/80 transition-colors">
                    <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </td>

                    <td className="px-4 py-3 font-medium text-gray-800 whitespace-nowrap">
                      <p>{log.actor_display_name || "Admin"}</p>
                      <p className="font-mono text-[10px] text-gray-400">{log.actor_email || "System"}</p>
                    </td>

                    <td className="px-4 py-3">
                      <span className="font-mono text-xs font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded">
                        {log.action}
                      </span>
                    </td>

                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      <p className="font-medium text-gray-800">{log.target_type}</p>
                      <p className="font-mono text-[10px] text-gray-400">{log.target_id || "-"}</p>
                    </td>

                    <td className="px-4 py-3 text-gray-700">
                      {renderPayload(log.payload)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination Footer */}
          <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
            <span>
              Showing {logs.length > 0 ? offset + 1 : 0} to {Math.min(offset + limit, total)} of {total} records
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
    </div>
  );
}
