import { useCallback, useEffect, useState } from "react";
import { getTopicDocuments } from "./knowledge-base-api";
import type { DocumentDetail, DocumentListItem } from "./knowledge-base-types";

interface UseKnowledgeDocumentsOptions {
  pageSize?: number;
}

export function useKnowledgeDocuments(
  topicId: string | null | undefined,
  options: UseKnowledgeDocumentsOptions = {}
) {
  const pageSize = options.pageSize || 20;
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDocuments = useCallback(async () => {
    if (!topicId) {
      setDocuments([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const offset = (page - 1) * pageSize;
      const res = await getTopicDocuments(topicId, pageSize, offset);
      setDocuments(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, [topicId, page, pageSize]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // Reset to page 1 if topic changes
  useEffect(() => {
    setPage(1);
  }, [topicId]);

  const removeDocument = useCallback((documentId: string) => {
    setDocuments((prev) => prev.filter((d) => d.id !== documentId));
    setTotal((prev) => Math.max(0, prev - 1));
  }, []);

  const updateDocument = useCallback((updated: DocumentListItem | DocumentDetail) => {
    setDocuments((prev) =>
      prev.map((d) => (d.id === updated.id ? { ...d, ...updated } : d))
    );
  }, []);

  return {
    documents,
    total,
    page,
    pageSize,
    setPage,
    loading,
    error,
    refetch: fetchDocuments,
    removeDocument,
    updateDocument,
  };
}
