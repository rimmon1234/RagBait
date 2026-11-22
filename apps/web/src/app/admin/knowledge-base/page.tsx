"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Clock,
  FileText,
  FolderTree,
  History,
  ListTodo,
  Loader2,
  Plus,
  RefreshCw,
  UploadCloud,
} from "lucide-react";
import { getAdminOverview } from "@/lib/knowledge-base/knowledge-base-admin-api";
import type { AdminOverviewMetrics } from "@/lib/knowledge-base/knowledge-base-admin-types";
import { AdminKnowledgeBaseNavigation } from "@/components/knowledge-base/admin/AdminKnowledgeBaseNavigation";
import { AdminOverviewCards } from "@/components/knowledge-base/admin/AdminOverviewCards";
import { KnowledgeBaseAdminHeader } from "@/components/knowledge-base/admin/KnowledgeBaseAdminHeader";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";

export default function KnowledgeBaseAdminOverviewPage() {
  const [metrics, setMetrics] = useState<AdminOverviewMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getAdminOverview();
      setMetrics(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load overview data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div>
      <KnowledgeBaseAdminHeader
        title="Knowledge Base Administration"
        description="Academic taxonomy management, document moderation, extraction queue monitoring, and audit trails."
        actions={
          <div className="flex items-center gap-2">
            <Link
              href="/admin/knowledge-base/subjects"
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Add Subject</span>
            </Link>
            <button
              type="button"
              onClick={() => setIsUploadModalOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 shadow-sm hover:bg-gray-50"
            >
              <UploadCloud className="h-3.5 w-3.5 text-blue-600" />
              <span>Upload Document</span>
            </button>
            <button
              type="button"
              onClick={loadData}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        }
      />

      <AdminKnowledgeBaseNavigation />

      {loading && !metrics ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-sm font-semibold text-red-800">Failed to load overview</h3>
          <p className="mt-1 text-xs text-red-600">{error}</p>
          <button
            type="button"
            onClick={loadData}
            className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-xs font-medium text-white hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      ) : metrics ? (
        <div className="space-y-8">
          {/* KPI Stat Cards */}
          <AdminOverviewCards metrics={metrics} />

          {/* Quick Actions Grid */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Link
              href="/admin/knowledge-base/subjects"
              className="group flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-all hover:border-blue-500 hover:shadow-md"
            >
              <div>
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                  <FolderTree className="h-5 w-5" />
                </div>
                <h4 className="mt-4 text-base font-semibold text-gray-900 group-hover:text-blue-600">
                  Manage Taxonomy
                </h4>
                <p className="mt-1 text-xs text-gray-500">
                  Create, edit, reorder, and archive academic subjects and topics.
                </p>
              </div>
              <div className="mt-4 flex items-center text-xs font-medium text-blue-600">
                <span>View subjects</span>
                <ArrowRight className="ml-1 h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
              </div>
            </Link>

            <Link
              href="/admin/knowledge-base/documents"
              className="group flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-all hover:border-blue-500 hover:shadow-md"
            >
              <div>
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-50 text-green-600">
                  <FileText className="h-5 w-5" />
                </div>
                <h4 className="mt-4 text-base font-semibold text-gray-900 group-hover:text-blue-600">
                  Moderate Documents
                </h4>
                <p className="mt-1 text-xs text-gray-500">
                  Inspect student uploads, archive inappropriate materials, or permanently delete files.
                </p>
              </div>
              <div className="mt-4 flex items-center text-xs font-medium text-blue-600">
                <span>Moderate library</span>
                <ArrowRight className="ml-1 h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
              </div>
            </Link>

            <Link
              href="/admin/knowledge-base/jobs"
              className="group flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-all hover:border-blue-500 hover:shadow-md"
            >
              <div>
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
                  <ListTodo className="h-5 w-5" />
                </div>
                <h4 className="mt-4 text-base font-semibold text-gray-900 group-hover:text-blue-600">
                  Extraction Monitor
                </h4>
                <p className="mt-1 text-xs text-gray-500">
                  Monitor background extraction worker health and safely retry eligible failed jobs.
                </p>
              </div>
              <div className="mt-4 flex items-center text-xs font-medium text-blue-600">
                <span>Monitor queue</span>
                <ArrowRight className="ml-1 h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
              </div>
            </Link>

            <Link
              href="/admin/knowledge-base/audit"
              className="group flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-all hover:border-blue-500 hover:shadow-md"
            >
              <div>
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-50 text-purple-600">
                  <History className="h-5 w-5" />
                </div>
                <h4 className="mt-4 text-base font-semibold text-gray-900 group-hover:text-blue-600">
                  Audit History
                </h4>
                <p className="mt-1 text-xs text-gray-500">
                  Review tamper-proof immutable audit events and administrative decision records.
                </p>
              </div>
              <div className="mt-4 flex items-center text-xs font-medium text-blue-600">
                <span>Inspect audit trail</span>
                <ArrowRight className="ml-1 h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
              </div>
            </Link>
          </div>

          {/* Tables Preview: Recent Uploads & Recent Audit */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Recent Uploads */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between border-b border-gray-100 pb-3 mb-4">
                <h3 className="text-sm font-semibold text-gray-900">Recent Document Uploads</h3>
                <Link
                  href="/admin/knowledge-base/documents"
                  className="text-xs font-medium text-blue-600 hover:text-blue-700"
                >
                  View all
                </Link>
              </div>
              {metrics.recent_documents.length === 0 ? (
                <p className="py-6 text-center text-xs text-gray-400">No documents uploaded yet.</p>
              ) : (
                <div className="divide-y divide-gray-100">
                  {metrics.recent_documents.map((doc) => (
                    <div key={doc.id} className="flex items-center justify-between py-3">
                      <div className="min-w-0 pr-4">
                        <p className="truncate text-sm font-medium text-gray-900">{doc.title}</p>
                        <p className="truncate text-xs text-gray-400">
                          {doc.subject_name} • {doc.topic_name} • by {doc.uploader_display_name || "Unknown"}
                        </p>
                      </div>
                      <ExtractionStatusBadge status={doc.extraction_status} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Recent Administrative Actions */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between border-b border-gray-100 pb-3 mb-4">
                <h3 className="text-sm font-semibold text-gray-900">Recent Admin Actions</h3>
                <Link
                  href="/admin/knowledge-base/audit"
                  className="text-xs font-medium text-blue-600 hover:text-blue-700"
                >
                  View all
                </Link>
              </div>
              {metrics.recent_actions.length === 0 ? (
                <p className="py-6 text-center text-xs text-gray-400">No admin actions recorded yet.</p>
              ) : (
                <div className="divide-y divide-gray-100">
                  {metrics.recent_actions.map((act) => (
                    <div key={act.id} className="flex items-center justify-between py-3">
                      <div className="min-w-0 pr-4">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-semibold text-blue-600">{act.action}</span>
                          <span className="text-xs text-gray-400">({act.target_type})</span>
                        </div>
                        <p className="truncate text-xs text-gray-500 mt-0.5">
                          by {act.actor_display_name || "System"} • {new Date(act.created_at).toLocaleString()}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* Admin Document Upload Modal */}
      <DocumentUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onSuccess={() => {
          setIsUploadModalOpen(false);
          loadData();
        }}
      />
    </div>
  );
}
