"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { BookOpen, Plus, UploadCloud } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import { useCurrentUser } from "@/lib/knowledge-base/useCurrentUser";
import { KnowledgeSearch } from "@/components/knowledge-base/KnowledgeSearch";
import { SubjectGrid } from "@/components/knowledge-base/SubjectGrid";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";

export default function KnowledgeBaseCataloguePage() {
  const { user: currentUser } = useCurrentUser();
  const isAdmin = currentUser?.role === "admin" || currentUser?.role === "super_admin";
  const canUpload = Boolean(currentUser && (isAdmin || currentUser.approval_status === "approved"));
  const { subjects, loading, error, refetch } = useKnowledgeSubjects();
  const [search, setSearch] = useState("");
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const filteredSubjects = useMemo(() => {
    if (!search.trim()) return subjects;
    const q = search.toLowerCase();
    return subjects.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.description && s.description.toLowerCase().includes(q)) ||
        s.slug.toLowerCase().includes(q)
    );
  }, [subjects, search]);

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Top Banner & Header */}
        <div className="card-elevated flex flex-col gap-4 bg-gradient-to-r from-white via-white to-indigo-50/70 p-6 sm:flex-row sm:items-center sm:justify-between">
          <div>
                <div className="inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 text-indigo-700 ring-1 ring-indigo-200/70">
                  <BookOpen className="h-4 w-4" />
                  <span className="text-xs font-semibold uppercase tracking-wider">
                    Academic Library
                  </span>
                </div>
                  <h1 className="mt-3 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
                    Knowledge Base
                  </h1>
                    <p className="mt-1.5 max-w-2xl text-sm text-slate-600">
                      Browse and upload lecture notes, syllabus materials, and study guides organized by subject.
                      {isAdmin
                        ? " Administrators can also manage subjects and moderate all materials."
                        : " You can delete materials that you uploaded."}
                    </p>
                  </div>

                  {(isAdmin || canUpload) && (
                      <div className="flex flex-wrap items-center gap-2.5 self-start sm:self-auto">
                        {isAdmin && (
                          <Link
                            href="/admin/knowledge-base/subjects"
                            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
                          >
                            <Plus className="h-4 w-4 text-indigo-600" />
                            <span>Manage / Add Subjects</span>
                          </Link>
                        )}
                        {canUpload && (
                          <button
                            type="button"
                            onClick={() => setIsUploadModalOpen(true)}
                            className="btn-primary-gradient px-4 py-2.5"
                          >
                            <UploadCloud className="h-4 w-4" />
                            <span>Upload Material</span>
                          </button>
                        )}
                      </div>
                  )}
                    </div>

      {/* Search Bar */}
                  <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <KnowledgeSearch
                      value={search}
                      onChange={setSearch}
                      placeholder="Search subjects (e.g. DBMS, Operating Systems, AI)..."
                      className="w-full sm:max-w-md"
                    />
                    <div className="text-xs text-gray-500">
                      Showing <span className="font-semibold text-gray-900">{filteredSubjects.length}</span>{" "}
                      {filteredSubjects.length === 1 ? "subject" : "subjects"}
                    </div>
                  </div>

                  {/* Error state */}
                  {error && (
                    <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                      {error}
                    </div>
                  )}

                  {/* Subject Cards Grid */}
                  <div className="mt-6">
                    <SubjectGrid subjects={filteredSubjects} loading={loading} />
                  </div>

                  {/* Direct Upload Modal */}
                  {canUpload && (
                    <DocumentUploadModal
                      isOpen={isUploadModalOpen}
                      onClose={() => setIsUploadModalOpen(false)}
                      onSuccess={() => refetch()}
                      availableSubjects={subjects}
                    />
                  )}
                </div>
                );
}
