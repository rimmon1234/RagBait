"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { BookOpen, Plus, UploadCloud } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import { KnowledgeSearch } from "@/components/knowledge-base/KnowledgeSearch";
import { SubjectGrid } from "@/components/knowledge-base/SubjectGrid";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";

export default function KnowledgeBaseCataloguePage() {
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
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-blue-600">
            <BookOpen className="h-6 w-6" />
            <span className="text-sm font-semibold uppercase tracking-wider">
              Academic Library
            </span>
          </div>
          <h1 className="mt-1 text-2xl font-bold text-gray-900 sm:text-3xl">
            Knowledge Base
          </h1>
          <p className="mt-1.5 text-sm text-gray-600 max-w-2xl">
            Browse peer-curated lecture notes, syllabus materials, and past papers organized by subject.
            Students and faculty administrators can contribute study materials directly.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
          <Link
            href="/admin/knowledge-base/subjects"
            className="inline-flex items-center gap-1.5 rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm font-semibold text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <Plus className="h-4 w-4 text-blue-600" />
            <span>Manage / Add Subjects</span>
          </Link>
          <button
            type="button"
            onClick={() => setIsUploadModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <UploadCloud className="h-4 w-4" />
            <span>Upload Material</span>
          </button>
        </div>
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
      <DocumentUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onSuccess={() => refetch()}
        availableSubjects={subjects}
      />
    </div>
  );
}
