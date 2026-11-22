"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Archive,
  Eye,
  FileText,
  Filter,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  Undo2,
  UploadCloud,
} from "lucide-react";
import {
  archiveAdminDocument,
  deleteAdminDocument,
  getAdminDocument,
  getAdminDocuments,
  getAdminSubjects,
  getAdminTopics,
  restoreAdminDocument,
} from "@/lib/knowledge-base/knowledge-base-admin-api";
import type {
  AdminDocumentDetail,
  AdminDocumentListItem,
  AdminSubjectItem,
  AdminTopicItem,
} from "@/lib/knowledge-base/knowledge-base-admin-types";
import { AdminConfirmModal } from "@/components/knowledge-base/admin/AdminConfirmModal";
import { AdminKnowledgeBaseNavigation } from "@/components/knowledge-base/admin/AdminKnowledgeBaseNavigation";
import { DocumentModerationDrawer } from "@/components/knowledge-base/admin/DocumentModerationDrawer";
import { KnowledgeBaseAdminHeader } from "@/components/knowledge-base/admin/KnowledgeBaseAdminHeader";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";
import { ExtractionStatusBadge } from "@/components/knowledge-base/ExtractionStatusBadge";

export default function DocumentModerationPage() {
  const [documents, setDocuments] = useState<AdminDocumentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  // Filters
  const [limit] = useState(20);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [selectedSubjectId, setSelectedSubjectId] = useState<string>("");
  const [selectedTopicId, setSelectedTopicId] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [extractionFilter, setExtractionFilter] = useState<string>("");
  const [archivedFilter, setArchivedFilter] = useState<string>("all"); // 'all', 'active', 'archived'

  // Subject and Topic dropdown options
  const [subjects, setSubjects] = useState<AdminSubjectItem[]>([]);
  const [topics, setTopics] = useState<AdminTopicItem[]>([]);

  // Selected document for inspection drawer
  const [selectedDocDetail, setSelectedDocDetail] = useState<AdminDocumentDetail | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  // Confirm Modal state
  const [confirmDialog, setConfirmDialog] = useState<{
    isOpen: boolean;
    title: string;
    description: string;
    confirmLabel: string;
    isDestructive: boolean;
    requireReason: boolean;
    reasonPlaceholder: string;
    onConfirm: (reason: string) => Promise<void>;
  }>({
    isOpen: false,
    title: "",
    description: "",
    confirmLabel: "Confirm",
    isDestructive: false,
    requireReason: false,
    reasonPlaceholder: "",
    onConfirm: async () => {},
  });

  // Load subject options on mount
  useEffect(() => {
    getAdminSubjects(true)
      .then(setSubjects)
      .catch(() => {});
  }, []);

  // When subject changes, load its topics
  useEffect(() => {
    if (selectedSubjectId) {
      getAdminTopics(selectedSubjectId, true)
        .then(setTopics)
        .catch(() => setTopics([]));
    } else {
      setTopics([]);
      setSelectedTopicId("");
    }
  }, [selectedSubjectId]);

  const loadDocuments = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      let isArchivedParam: boolean | undefined = undefined;
      if (archivedFilter === "active") isArchivedParam = false;
      else if (archivedFilter === "archived") isArchivedParam = true;

      const data = await getAdminDocuments({
        limit,
        offset,
        search: search.trim() || undefined,
        subject_id: selectedSubjectId || undefined,
        topic_id: selectedTopicId || undefined,
        upload_status: statusFilter || undefined,
        extraction_status: extractionFilter || undefined,
        is_archived: isArchivedParam,
      });

      setDocuments(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents.");
    } finally {
      setLoading(false);
    }
  }, [
    limit,
    offset,
    search,
    selectedSubjectId,
    selectedTopicId,
    statusFilter,
    extractionFilter,
    archivedFilter,
  ]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const openDrawer = async (docId: string) => {
    try {
      const detail = await getAdminDocument(docId);
      setSelectedDocDetail(detail);
      setIsDrawerOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load document details.");
    }
  };

  const handleArchive = (doc: AdminDocumentListItem | AdminDocumentDetail) => {
    setConfirmDialog({
      isOpen: true,
      title: "Archive Study Document",
      description: `Archive '${doc.title}'? This will immediately hide the document from students and fence any running extraction jobs.`,
      confirmLabel: "Archive Document",
      isDestructive: true,
      requireReason: true,
      reasonPlaceholder: "Reason for archiving (e.g. copyright violation, poor quality)...",
      onConfirm: async (reason: string) => {
        await archiveAdminDocument(doc.id, reason);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        setIsDrawerOpen(false);
        loadDocuments();
      },
    });
  };

  const handleRestore = (doc: AdminDocumentListItem | AdminDocumentDetail) => {
    setConfirmDialog({
      isOpen: true,
      title: "Restore Document",
      description: `Restore '${doc.title}'? The parent subject and topic must both be active.`,
      confirmLabel: "Restore Document",
      isDestructive: false,
      requireReason: false,
      reasonPlaceholder: "Optional restore reason...",
      onConfirm: async (reason: string) => {
        await restoreAdminDocument(doc.id, reason);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        setIsDrawerOpen(false);
        loadDocuments();
      },
    });
  };

  const handleDelete = (doc: AdminDocumentListItem | AdminDocumentDetail) => {
    setConfirmDialog({
      isOpen: true,
      title: "Permanently Delete Document",
      description: `Are you sure you want to permanently delete '${doc.title}'? The underlying file in Supabase Storage and extracted page records will be permanently removed.`,
      confirmLabel: "Permanently Delete",
      isDestructive: true,
      requireReason: true,
      reasonPlaceholder: "Mandatory deletion reason...",
      onConfirm: async (reason: string) => {
        await deleteAdminDocument(doc.id, reason);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        setIsDrawerOpen(false);
        loadDocuments();
      },
    });
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1048576).toFixed(1)} MiB`;
  };

  return (
    <div>
      <KnowledgeBaseAdminHeader
        title="Document Moderation"
        description="Inspect shared student study documents, review extraction outcomes, archive, and delete materials."
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setIsUploadModalOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              Upload Document
            </button>
            <button
              type="button"
              onClick={loadDocuments}
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

      {/* Filter Toolbar */}
      <div className="mb-6 rounded-xl border border-gray-200 bg-white p-4 shadow-sm space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          {/* Search */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setOffset(0);
              loadDocuments();
            }}
            className="relative flex-1 max-w-sm"
          >
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by title, filename, or uploader..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-gray-300 pl-9 pr-4 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </form>

          {/* Quick Clear */}
          {(selectedSubjectId || selectedTopicId || statusFilter || extractionFilter || archivedFilter !== "all" || search) && (
            <button
              type="button"
              onClick={() => {
                setSelectedSubjectId("");
                setSelectedTopicId("");
                setStatusFilter("");
                setExtractionFilter("");
                setArchivedFilter("all");
                setSearch("");
                setOffset(0);
              }}
              className="text-xs text-blue-600 hover:text-blue-700 self-end sm:self-auto"
            >
              Reset filters
            </button>
          )}
        </div>

        {/* Filter Selects */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5 text-xs">
          <div>
            <label className="block font-medium text-gray-600 mb-1">Subject</label>
            <select
              value={selectedSubjectId}
              onChange={(e) => {
                setSelectedSubjectId(e.target.value);
                setOffset(0);
              }}
              className="w-full rounded-md border border-gray-300 p-1.5 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="">All Subjects</option>
              {subjects.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} {!s.is_active ? "(Archived)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block font-medium text-gray-600 mb-1">Topic</label>
            <select
              value={selectedTopicId}
              onChange={(e) => {
                setSelectedTopicId(e.target.value);
                setOffset(0);
              }}
              disabled={!selectedSubjectId}
              className="w-full rounded-md border border-gray-300 p-1.5 shadow-sm focus:border-blue-500 focus:ring-blue-500 disabled:opacity-50"
            >
              <option value="">All Topics</option>
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} {!t.is_active ? "(Archived)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block font-medium text-gray-600 mb-1">Upload Status</label>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setOffset(0);
              }}
              className="w-full rounded-md border border-gray-300 p-1.5 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="">All Statuses</option>
              <option value="uploaded">Uploaded</option>
              <option value="pending_upload">Pending Upload</option>
              <option value="upload_failed">Upload Failed</option>
            </select>
          </div>

          <div>
            <label className="block font-medium text-gray-600 mb-1">Extraction</label>
            <select
              value={extractionFilter}
              onChange={(e) => {
                setExtractionFilter(e.target.value);
                setOffset(0);
              }}
              className="w-full rounded-md border border-gray-300 p-1.5 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="">All Extraction States</option>
              <option value="pending">Pending</option>
              <option value="queued">Queued</option>
              <option value="processing">Processing</option>
              <option value="completed">Completed</option>
              <option value="completed_no_text">Completed (No Text)</option>
              <option value="failed">Failed</option>
            </select>
          </div>

          <div>
            <label className="block font-medium text-gray-600 mb-1">Archived</label>
            <select
              value={archivedFilter}
              onChange={(e) => {
                setArchivedFilter(e.target.value);
                setOffset(0);
              }}
              className="w-full rounded-md border border-gray-300 p-1.5 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="all">All Documents</option>
              <option value="active">Active Only</option>
              <option value="archived">Archived Only</option>
            </select>
          </div>
        </div>
      </div>

      {/* Documents Table */}
      {loading && documents.length === 0 ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-sm font-semibold text-red-800">Failed to load documents</h3>
          <p className="mt-1 text-xs text-red-600">{error}</p>
        </div>
      ) : documents.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
          <FileText className="mx-auto h-12 w-12 text-gray-300" />
          <h3 className="mt-2 text-base font-semibold text-gray-900">No documents found</h3>
          <p className="mt-1 text-sm text-gray-500">
            No documents matched your active filters or search terms.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-4 py-3 font-medium">Document Title</th>
                  <th className="px-4 py-3 font-medium">Taxonomy</th>
                  <th className="px-4 py-3 font-medium">Uploader</th>
                  <th className="px-4 py-3 font-medium">Size & Pages</th>
                  <th className="px-4 py-3 font-medium">Extraction</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {documents.map((doc) => (
                  <tr key={doc.id} className="hover:bg-gray-50/80 transition-colors">
                    <td className="px-4 py-3 font-medium text-gray-900">
                      <div className="flex items-center gap-2">
                        <span className="truncate max-w-xs">{doc.title}</span>
                        {doc.is_archived && (
                          <span className="rounded-full bg-amber-100 px-1.5 py-0.2 text-[10px] font-medium text-amber-800">
                            Archived
                          </span>
                        )}
                      </div>
                      <p className="font-mono text-[11px] text-gray-400 truncate max-w-xs">{doc.original_filename}</p>
                    </td>

                    <td className="px-4 py-3 text-gray-600">
                      <p className="font-medium text-gray-800">{doc.subject_name}</p>
                      <p className="text-[11px] text-gray-400">{doc.topic_name}</p>
                    </td>

                    <td className="px-4 py-3 text-gray-600">
                      <p className="font-medium text-gray-800">{doc.uploader_display_name || "Unknown"}</p>
                      <p className="text-[11px] text-gray-400">{doc.uploader_email || "N/A"}</p>
                    </td>

                    <td className="px-4 py-3 text-gray-600">
                      <p>{formatBytes(doc.file_size_bytes)}</p>
                      <p className="text-[11px] text-gray-400">{doc.total_pages} pages ({doc.pages_with_text} text)</p>
                    </td>

                    <td className="px-4 py-3">
                      <ExtractionStatusBadge status={doc.extraction_status} />
                    </td>

                    <td className="px-4 py-3">
                      <span className="font-mono text-xs text-gray-600">{doc.upload_status}</span>
                    </td>

                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => openDrawer(doc.id)}
                          className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-blue-600"
                          title="Inspect document"
                        >
                          <Eye className="h-4 w-4" />
                        </button>

                        {doc.is_archived ? (
                          <button
                            type="button"
                            onClick={() => handleRestore(doc)}
                            className="rounded p-1 text-emerald-600 hover:bg-emerald-50"
                            title="Restore document"
                          >
                            <Undo2 className="h-4 w-4" />
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleArchive(doc)}
                            className="rounded p-1 text-amber-600 hover:bg-amber-50"
                            title="Archive document"
                          >
                            <Archive className="h-4 w-4" />
                          </button>
                        )}

                        <button
                          type="button"
                          onClick={() => handleDelete(doc)}
                          className="rounded p-1 text-red-600 hover:bg-red-50"
                          title="Permanently delete"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
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
              Showing {documents.length > 0 ? offset + 1 : 0} to {Math.min(offset + limit, total)} of {total} documents
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

      {/* Moderation Inspection Drawer */}
      <DocumentModerationDrawer
        isOpen={isDrawerOpen}
        document={selectedDocDetail}
        onArchive={handleArchive}
        onRestore={handleRestore}
        onDelete={handleDelete}
        onClose={() => {
          setIsDrawerOpen(false);
          setSelectedDocDetail(null);
        }}
      />

      {/* Confirmation Modal */}
      <AdminConfirmModal
        isOpen={confirmDialog.isOpen}
        title={confirmDialog.title}
        description={confirmDialog.description}
        confirmLabel={confirmDialog.confirmLabel}
        isDestructive={confirmDialog.isDestructive}
        requireReason={confirmDialog.requireReason}
        reasonPlaceholder={confirmDialog.reasonPlaceholder}
        onConfirm={confirmDialog.onConfirm}
        onClose={() => setConfirmDialog((prev) => ({ ...prev, isOpen: false }))}
      />

      {/* Admin Document Upload Modal */}
      <DocumentUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onSuccess={() => {
          setIsUploadModalOpen(false);
          loadDocuments();
        }}
      />
    </div>
  );
}
