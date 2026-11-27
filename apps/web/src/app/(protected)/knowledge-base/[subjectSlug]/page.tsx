"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { BookOpen, FolderOpen, Loader2, MessageSquare, UploadCloud } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import { useKnowledgeTopics } from "@/lib/knowledge-base/useKnowledgeTopics";
import { useCurrentUser } from "@/lib/knowledge-base/useCurrentUser";
import { TopicBreadcrumbs } from "@/components/knowledge-base/TopicBreadcrumbs";
import { KnowledgeSearch } from "@/components/knowledge-base/KnowledgeSearch";
import { TopicCard } from "@/components/knowledge-base/TopicCard";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";

export default function SubjectDetailPage() {
  const routeParams = useParams<{ subjectSlug: string }>();
  const subjectSlug = routeParams?.subjectSlug;
  const { user: currentUser } = useCurrentUser();
  const isAdmin = currentUser?.role === "admin" || currentUser?.role === "super_admin";
  const canUpload = Boolean(currentUser && (isAdmin || currentUser.approval_status === "approved"));

  const { subjects, loading: subjectsLoading } = useKnowledgeSubjects();
  const currentSubject = subjects.find((s) => s.slug === subjectSlug);

  const { topics, loading: topicsLoading, refetch: refetchTopics } = useKnowledgeTopics(
    currentSubject?.id
  );

  const [search, setSearch] = useState("");
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const filteredTopics = useMemo(() => {
    if (!search.trim()) return topics;
    const q = search.toLowerCase();
    return topics.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.description && t.description.toLowerCase().includes(q))
    );
  }, [topics, search]);

  if (subjectsLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (!currentSubject) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-12 text-center">
        <h2 className="text-xl font-bold text-gray-900">Subject Not Found</h2>
        <p className="mt-2 text-sm text-gray-600">
          The requested subject does not exist or may have been archived.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Breadcrumbs */}
      <TopicBreadcrumbs items={[{ label: currentSubject.name }]} />

      {/* Header */}
      <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 sm:text-3xl">
            {currentSubject.name}
          </h1>
          {currentSubject.description && (
            <p className="mt-1.5 max-w-2xl text-sm text-gray-600">
              {currentSubject.description}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 self-start sm:self-auto">
          <Link
            href={`/chat?subject_id=${currentSubject.id}`}
            className="inline-flex items-center gap-2 rounded-xl border border-blue-600 bg-blue-50 px-4 py-2.5 text-sm font-semibold text-blue-700 shadow-sm hover:bg-blue-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <MessageSquare className="h-4 w-4" />
            <span>AI Chat</span>
          </Link>
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
      </div>

      {/* Search Toolbar */}
      <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <KnowledgeSearch
          value={search}
          onChange={setSearch}
          placeholder="Filter topics..."
          className="w-full sm:max-w-md"
        />
        <div className="text-xs text-gray-500">
          Showing <span className="font-semibold text-gray-900">{filteredTopics.length}</span>{" "}
          {filteredTopics.length === 1 ? "topic" : "topics"}
        </div>
      </div>

      {/* Topics Grid */}
      <div className="mt-6">
        {topicsLoading ? (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-36 animate-pulse rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
              />
            ))}
          </div>
        ) : filteredTopics.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-300 bg-white p-12 text-center shadow-sm">
            <FolderOpen className="mx-auto h-12 w-12 text-gray-400" />
            <h3 className="mt-2 text-sm font-semibold text-gray-900">No topics found</h3>
            <p className="mt-1 text-sm text-gray-500">
              {search
                ? `No topics match "${search}".`
                : "No topics have been configured for this subject yet."}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {filteredTopics.map((topic) => (
              <TopicCard
                key={topic.id}
                topic={topic}
                subjectSlug={currentSubject.slug}
              />
            ))}
          </div>
        )}
      </div>

      {/* Direct Upload Modal */}
      <DocumentUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onSuccess={() => refetchTopics()}
        initialSubjectId={currentSubject.id}
      />
    </div>
  );
}
