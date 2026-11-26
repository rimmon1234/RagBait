"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { BookOpen, FileText, Loader2, UploadCloud } from "lucide-react";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import { useKnowledgeTopics } from "@/lib/knowledge-base/useKnowledgeTopics";
import { useKnowledgeDocuments } from "@/lib/knowledge-base/useKnowledgeDocuments";
import { useDocumentStatus } from "@/lib/knowledge-base/useDocumentStatus";
import { useCurrentUser } from "@/lib/knowledge-base/useCurrentUser";
import { deleteDocument, getDownloadUrl } from "@/lib/knowledge-base/knowledge-base-api";
import { TopicBreadcrumbs } from "@/components/knowledge-base/TopicBreadcrumbs";
import { DocumentList } from "@/components/knowledge-base/DocumentList";
import { DocumentUploadModal } from "@/components/knowledge-base/DocumentUploadModal";
import { DocumentPreviewDrawer } from "@/components/knowledge-base/DocumentPreviewDrawer";
import type { DocumentListItem } from "@/lib/knowledge-base/knowledge-base-types";

export default function TopicDocumentsPage() {
  const routeParams = useParams<{ subjectSlug: string; topicSlug: string }>();
  const subjectSlug = routeParams?.subjectSlug;
  const topicSlug = routeParams?.topicSlug;

  const { user: currentUser } = useCurrentUser();
  const isAdmin = currentUser?.role === "admin" || currentUser?.role === "super_admin";

  // Find Subject
  const { subjects, loading: subjectsLoading } = useKnowledgeSubjects();
  const currentSubject = subjects.find((s) => s.slug === subjectSlug);

  // Find Topic
  const { topics, loading: topicsLoading } = useKnowledgeTopics(currentSubject?.id);
  const currentTopic = topics.find((t) => t.slug === topicSlug);

  // Documents
  const {
    documents,
    total,
    page,
    pageSize,
    setPage,
    loading: docsLoading,
    error: docsError,
    refetch: refetchDocs,
    removeDocument,
    updateDocument,
  } = useKnowledgeDocuments(currentTopic?.id);

  // Background extraction polling (every 3s for non-terminal extraction jobs)
  useDocumentStatus(documents, updateDocument);

  // UI States
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<DocumentListItem | null>(null);

  // Download Handler
  const handleDownload = async (doc: DocumentListItem) => {
    try {
      const res = await getDownloadUrl(doc.id);
      if (res?.download_url) {
        // Trigger download
        const a = window.document.createElement("a");
        a.href = res.download_url;
        a.download = res.original_filename || doc.original_filename;
        a.target = "_blank";
        window.document.body.appendChild(a);
        a.click();
        window.document.body.removeChild(a);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to obtain download URL");
    }
  };

  // Delete Handler
  const handleDelete = async (doc: DocumentListItem) => {
    try {
      await deleteDocument(doc.id);
      removeDocument(doc.id);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete document");
    }
  };

  if (subjectsLoading || (currentSubject && topicsLoading)) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (!currentSubject || !currentTopic) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-12 text-center">
        <h2 className="text-xl font-bold text-gray-900">Topic Not Found</h2>
        <p className="mt-2 text-sm text-gray-600">
          The requested subject or topic does not exist or may have been archived.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Breadcrumbs */}
      <TopicBreadcrumbs
        items={[
          { label: currentSubject.name, href: `/knowledge-base/${currentSubject.slug}` },
          { label: currentTopic.name },
        ]}
      />

      {/* Header */}
      <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 sm:text-3xl">
            {currentTopic.name}
          </h1>
          {currentTopic.description && (
            <p className="mt-1.5 max-w-2xl text-sm text-gray-600">
              {currentTopic.description}
            </p>
          )}
        </div>

        {isAdmin && (
          <button
            type="button"
            onClick={() => setIsUploadModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 self-start sm:self-auto"
          >
            <UploadCloud className="h-4 w-4" />
            <span>Upload Material</span>
          </button>
        )}
      </div>

      {/* Error state */}
      {docsError && (
        <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {docsError}
        </div>
      )}

      {/* Document Listing */}
      <div className="mt-8">
        <DocumentList
          documents={documents}
          total={total}
          page={page}
          pageSize={pageSize}
          loading={docsLoading}
          onPageChange={setPage}
          currentUser={currentUser}
          onPreview={(doc) => setPreviewDoc(doc)}
          onDownload={handleDownload}
          onDelete={handleDelete}
          onUploadClick={isAdmin ? () => setIsUploadModalOpen(true) : undefined}
        />
      </div>

      {/* Document Upload Modal */}
      {isAdmin && (
        <DocumentUploadModal
          isOpen={isUploadModalOpen}
          onClose={() => setIsUploadModalOpen(false)}
          onSuccess={() => refetchDocs()}
          initialSubjectId={currentSubject.id}
          initialTopicId={currentTopic.id}
        />
      )}

      {/* Document Preview Drawer */}
      <DocumentPreviewDrawer
        document={previewDoc}
        isOpen={Boolean(previewDoc)}
        onClose={() => setPreviewDoc(null)}
        onDownload={handleDownload}
      />
    </div>
  );
}
