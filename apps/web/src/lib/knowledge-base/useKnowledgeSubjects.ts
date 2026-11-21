import { useCallback, useEffect, useState } from "react";
import { getSubjects } from "./knowledge-base-api";
import type { SubjectRead } from "./knowledge-base-types";

export function useKnowledgeSubjects() {
  const [subjects, setSubjects] = useState<SubjectRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSubjects = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getSubjects();
      setSubjects(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load academic subjects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSubjects();
  }, [fetchSubjects]);

  return {
    subjects,
    loading,
    error,
    refetch: fetchSubjects,
  };
}
