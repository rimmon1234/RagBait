"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Archive,
  ChevronDown,
  ChevronRight,
  Edit,
  FolderTree,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Undo2,
} from "lucide-react";
import {
  archiveAdminSubject,
  archiveAdminTopic,
  createAdminSubject,
  createAdminTopic,
  getAdminSubjects,
  getAdminTopics,
  reorderAdminTopic,
  restoreAdminSubject,
  restoreAdminTopic,
  updateAdminSubject,
  updateAdminTopic,
} from "@/lib/knowledge-base/knowledge-base-admin-api";
import type {
  AdminSubjectItem,
  AdminTopicItem,
  SubjectCreateRequest,
  TopicCreateRequest,
} from "@/lib/knowledge-base/knowledge-base-admin-types";
import { AdminConfirmModal } from "@/components/knowledge-base/admin/AdminConfirmModal";
import { AdminKnowledgeBaseNavigation } from "@/components/knowledge-base/admin/AdminKnowledgeBaseNavigation";
import { KnowledgeBaseAdminHeader } from "@/components/knowledge-base/admin/KnowledgeBaseAdminHeader";
import { SubjectFormModal } from "@/components/knowledge-base/admin/SubjectFormModal";
import { TopicFormModal } from "@/components/knowledge-base/admin/TopicFormModal";

export default function SubjectsAdminPage() {
  const [subjects, setSubjects] = useState<AdminSubjectItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(true);

  // Expanded subject topics state
  const [expandedSubjectId, setExpandedSubjectId] = useState<string | null>(null);
  const [topics, setTopics] = useState<Record<string, AdminTopicItem[]>>({});
  const [topicsLoading, setTopicsLoading] = useState<Record<string, boolean>>({});

  // Modals state
  const [isSubjectModalOpen, setIsSubjectModalOpen] = useState(false);
  const [editingSubject, setEditingSubject] = useState<AdminSubjectItem | null>(null);
  const [isTopicModalOpen, setIsTopicModalOpen] = useState(false);
  const [editingTopic, setEditingTopic] = useState<AdminTopicItem | null>(null);
  const [targetSubjectForTopic, setTargetSubjectForTopic] = useState<AdminSubjectItem | null>(null);

  // Confirm dialog state
  const [confirmDialog, setConfirmDialog] = useState<{
    isOpen: boolean;
    title: string;
    description: string;
    confirmLabel: string;
    isDestructive: boolean;
    onConfirm: () => Promise<void>;
  }>({
    isOpen: false,
    title: "",
    description: "",
    confirmLabel: "Confirm",
    isDestructive: false,
    onConfirm: async () => {},
  });

  const loadSubjects = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getAdminSubjects(includeInactive, search);
      setSubjects(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load subjects.");
    } finally {
      setLoading(false);
    }
  }, [includeInactive, search]);

  useEffect(() => {
    loadSubjects();
  }, [loadSubjects]);

  const loadTopicsForSubject = async (subjectId: string) => {
    try {
      setTopicsLoading((prev) => ({ ...prev, [subjectId]: true }));
      const data = await getAdminTopics(subjectId, true);
      setTopics((prev) => ({ ...prev, [subjectId]: data }));
    } catch {
      // Ignored
    } finally {
      setTopicsLoading((prev) => ({ ...prev, [subjectId]: false }));
    }
  };

  const toggleExpand = (sub: AdminSubjectItem) => {
    if (expandedSubjectId === sub.id) {
      setExpandedSubjectId(null);
    } else {
      setExpandedSubjectId(sub.id);
      loadTopicsForSubject(sub.id);
    }
  };

  // Subject actions
  const handleSaveSubject = async (data: SubjectCreateRequest) => {
    if (editingSubject) {
      await updateAdminSubject(editingSubject.id, data);
    } else {
      await createAdminSubject(data);
    }
    setIsSubjectModalOpen(false);
    setEditingSubject(null);
    loadSubjects();
  };

  const handleArchiveSubject = (sub: AdminSubjectItem) => {
    setConfirmDialog({
      isOpen: true,
      title: "Archive Subject",
      description: `Are you sure you want to archive '${sub.name}'? Students will no longer be able to browse this subject or its study materials.`,
      confirmLabel: "Archive Subject",
      isDestructive: true,
      onConfirm: async () => {
        await archiveAdminSubject(sub.id);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        loadSubjects();
      },
    });
  };

  const handleRestoreSubject = (sub: AdminSubjectItem) => {
    setConfirmDialog({
      isOpen: true,
      title: "Restore Subject",
      description: `Restore '${sub.name}'? Approved students will be able to browse active topics within this subject again.`,
      confirmLabel: "Restore Subject",
      isDestructive: false,
      onConfirm: async () => {
        await restoreAdminSubject(sub.id);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        loadSubjects();
      },
    });
  };

  // Topic actions
  const handleSaveTopic = async (data: TopicCreateRequest) => {
    if (!targetSubjectForTopic) return;
    if (editingTopic) {
      await updateAdminTopic(editingTopic.id, data);
    } else {
      await createAdminTopic(targetSubjectForTopic.id, data);
    }
    setIsTopicModalOpen(false);
    setEditingTopic(null);
    loadTopicsForSubject(targetSubjectForTopic.id);
    loadSubjects();
  };

  const handleArchiveTopic = (topic: AdminTopicItem) => {
    setConfirmDialog({
      isOpen: true,
      title: "Archive Topic",
      description: `Archive topic '${topic.name}'? Students will no longer be able to view documents under this topic.`,
      confirmLabel: "Archive Topic",
      isDestructive: true,
      onConfirm: async () => {
        await archiveAdminTopic(topic.id);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        loadTopicsForSubject(topic.subject_id);
      },
    });
  };

  const handleRestoreTopic = (topic: AdminTopicItem) => {
    setConfirmDialog({
      isOpen: true,
      title: "Restore Topic",
      description: `Restore topic '${topic.name}'?`,
      confirmLabel: "Restore Topic",
      isDestructive: false,
      onConfirm: async () => {
        await restoreAdminTopic(topic.id);
        setConfirmDialog((prev) => ({ ...prev, isOpen: false }));
        loadTopicsForSubject(topic.subject_id);
      },
    });
  };

  const handleReorder = async (topic: AdminTopicItem, delta: number) => {
    const newOrder = Math.max(0, topic.display_order + delta);
    await reorderAdminTopic(topic.id, newOrder);
    loadTopicsForSubject(topic.subject_id);
  };

  return (
    <div>
      <KnowledgeBaseAdminHeader
        title="Subjects & Topics Taxonomy"
        description="Configure academic subjects, syllabus categories, topics, and ordering."
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setEditingSubject(null);
                setIsSubjectModalOpen(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"
            >
              <Plus className="h-4 w-4" />
              <span>Add Subject</span>
            </button>
            <button
              type="button"
              onClick={loadSubjects}
              disabled={loading}
              className="rounded-lg border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      />

      <AdminKnowledgeBaseNavigation />

      {/* Filter / Search Bar */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            loadSubjects();
          }}
          className="relative max-w-sm flex-1"
        >
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search subjects by name or slug..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-gray-300 pl-9 pr-4 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </form>

        <label className="flex items-center gap-2 text-xs font-medium text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
            className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          Show archived subjects
        </label>
      </div>

      {loading && subjects.length === 0 ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-sm font-semibold text-red-800">Failed to load taxonomy</h3>
          <p className="mt-1 text-xs text-red-600">{error}</p>
        </div>
      ) : subjects.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
          <FolderTree className="mx-auto h-12 w-12 text-gray-300" />
          <h3 className="mt-2 text-base font-semibold text-gray-900">No subjects found</h3>
          <p className="mt-1 text-sm text-gray-500">
            Create a subject to start building the academic catalogue.
          </p>
          <button
            type="button"
            onClick={() => {
              setEditingSubject(null);
              setIsSubjectModalOpen(true);
            }}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Create Subject
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {subjects.map((sub) => {
            const isExpanded = expandedSubjectId === sub.id;
            const subTopics = topics[sub.id] || [];
            const isTopicLoading = topicsLoading[sub.id] || false;

            return (
              <div
                key={sub.id}
                className={`rounded-xl border bg-white shadow-sm transition-all ${
                  sub.is_active ? "border-gray-200" : "border-gray-300 bg-gray-50/50 opacity-80"
                }`}
              >
                {/* Subject Row */}
                <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
                  <div
                    className="flex items-center gap-3 cursor-pointer select-none"
                    onClick={() => toggleExpand(sub)}
                  >
                    <button
                      type="button"
                      className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                    >
                      {isExpanded ? (
                        <ChevronDown className="h-5 w-5 text-gray-600" />
                      ) : (
                        <ChevronRight className="h-5 w-5 text-gray-400" />
                      )}
                    </button>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-gray-900">{sub.name}</span>
                        <span className="font-mono text-xs text-gray-400">/{sub.slug}</span>
                        {!sub.is_active && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                            Archived
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-gray-500 mt-0.5">
                        Icon: <span className="font-mono text-gray-600">{sub.icon_name}</span> • Order: {sub.display_order} • {sub.topic_count} topics • {sub.document_count} documents
                      </p>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 self-end sm:self-auto">
                    <button
                      type="button"
                      onClick={() => {
                        setTargetSubjectForTopic(sub);
                        setEditingTopic(null);
                        setIsTopicModalOpen(true);
                      }}
                      disabled={!sub.is_active}
                      className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      Add Topic
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setEditingSubject(sub);
                        setIsSubjectModalOpen(true);
                      }}
                      className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                    >
                      <Edit className="h-3.5 w-3.5" />
                      Edit
                    </button>

                    {sub.is_active ? (
                      <button
                        type="button"
                        onClick={() => handleArchiveSubject(sub)}
                        className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
                      >
                        <Archive className="h-3.5 w-3.5" />
                        Archive
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleRestoreSubject(sub)}
                        className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                      >
                        <Undo2 className="h-3.5 w-3.5" />
                        Restore
                      </button>
                    )}
                  </div>
                </div>

                {/* Expanded Topics Panel */}
                {isExpanded && (
                  <div className="border-t border-gray-100 bg-gray-50/70 p-5 rounded-b-xl">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                        Topics in {sub.name}
                      </h4>
                      <button
                        type="button"
                        onClick={() => {
                          setTargetSubjectForTopic(sub);
                          setEditingTopic(null);
                          setIsTopicModalOpen(true);
                        }}
                        disabled={!sub.is_active}
                        className="text-xs font-medium text-blue-600 hover:text-blue-700 disabled:opacity-40"
                      >
                        + Add topic
                      </button>
                    </div>

                    {isTopicLoading ? (
                      <div className="flex h-20 items-center justify-center">
                        <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
                      </div>
                    ) : subTopics.length === 0 ? (
                      <p className="py-4 text-center text-xs text-gray-400">
                        No topics created under this subject yet.
                      </p>
                    ) : (
                      <div className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
                        {subTopics.map((top) => (
                          <div
                            key={top.id}
                            className={`flex items-center justify-between p-3 text-xs ${
                              top.is_active ? "" : "bg-gray-50 text-gray-400"
                            }`}
                          >
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-gray-900">{top.name}</span>
                              <span className="font-mono text-gray-400">/{top.slug}</span>
                              {!top.is_active && (
                                <span className="rounded-full bg-amber-100 px-1.5 py-0.2 text-[10px] font-medium text-amber-800">
                                  Archived
                                </span>
                              )}
                              <span className="text-gray-400">• Order: {top.display_order} • {top.document_count} docs</span>
                            </div>

                            <div className="flex items-center gap-1.5">
                              {/* Reorder Buttons */}
                              <button
                                type="button"
                                onClick={() => handleReorder(top, -1)}
                                className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-100"
                                title="Move up"
                              >
                                ↑
                              </button>
                              <button
                                type="button"
                                onClick={() => handleReorder(top, 1)}
                                className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-100"
                                title="Move down"
                              >
                                ↓
                              </button>

                              <button
                                type="button"
                                onClick={() => {
                                  setTargetSubjectForTopic(sub);
                                  setEditingTopic(top);
                                  setIsTopicModalOpen(true);
                                }}
                                className="rounded p-1 text-gray-500 hover:bg-gray-100"
                              >
                                <Edit className="h-3.5 w-3.5" />
                              </button>

                              {top.is_active ? (
                                <button
                                  type="button"
                                  onClick={() => handleArchiveTopic(top)}
                                  className="rounded p-1 text-amber-600 hover:bg-amber-50"
                                  title="Archive topic"
                                >
                                  <Archive className="h-3.5 w-3.5" />
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => handleRestoreTopic(top)}
                                  className="rounded p-1 text-emerald-600 hover:bg-emerald-50"
                                  title="Restore topic"
                                >
                                  <Undo2 className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Subject Modal */}
      <SubjectFormModal
        isOpen={isSubjectModalOpen}
        subject={editingSubject}
        onSave={handleSaveSubject}
        onClose={() => {
          setIsSubjectModalOpen(false);
          setEditingSubject(null);
        }}
      />

      {/* Topic Modal */}
      <TopicFormModal
        isOpen={isTopicModalOpen}
        subjectName={targetSubjectForTopic?.name || ""}
        topic={editingTopic}
        onSave={handleSaveTopic}
        onClose={() => {
          setIsTopicModalOpen(false);
          setEditingTopic(null);
        }}
      />

      {/* Confirmation Modal */}
      <AdminConfirmModal
        isOpen={confirmDialog.isOpen}
        title={confirmDialog.title}
        description={confirmDialog.description}
        confirmLabel={confirmDialog.confirmLabel}
        isDestructive={confirmDialog.isDestructive}
        onConfirm={confirmDialog.onConfirm}
        onClose={() => setConfirmDialog((prev) => ({ ...prev, isOpen: false }))}
      />
    </div>
  );
}
