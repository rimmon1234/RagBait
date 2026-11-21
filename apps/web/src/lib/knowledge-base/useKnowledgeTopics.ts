import { useCallback, useEffect, useState } from "react";
import { getTopics } from "./knowledge-base-api";
import type { TopicRead } from "./knowledge-base-types";

export function useKnowledgeTopics(subjectId: string | null | undefined) {
  const [topics, setTopics] = useState<TopicRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTopics = useCallback(async () => {
    if (!subjectId) {
      setTopics([]);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const data = await getTopics(subjectId);
      setTopics(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load topics");
    } finally {
      setLoading(false);
    }
  }, [subjectId]);

  useEffect(() => {
    fetchTopics();
  }, [fetchTopics]);

  return {
    topics,
    loading,
    error,
    refetch: fetchTopics,
  };
}
