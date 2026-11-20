import { fetchWithAuth } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import {
  CommunityAvailableModel,
  CommunityJobStatusResponse,
  CommunityJobSubmitRequest,
  CommunityJobSubmitResponse,
  CommunityNode,
  CommunityNodeModel,
  CommunityStreamChunk,
  PairingCodeResponse,
} from "@/types/community";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function createPairingCode(): Promise<PairingCodeResponse> {
  return fetchWithAuth<PairingCodeResponse>("/api/v1/community/nodes/pair-code", {
    method: "POST",
  });
}

export async function listOwnedNodes(): Promise<CommunityNode[]> {
  return fetchWithAuth<CommunityNode[]>("/api/v1/community/nodes");
}

export async function getOwnedNode(nodeId: string): Promise<CommunityNode> {
  return fetchWithAuth<CommunityNode>(`/api/v1/community/nodes/${nodeId}`);
}

export async function updateNodeConfig(
  nodeId: string,
  updates: {
    display_name?: string;
    sharing_enabled?: boolean;
    max_concurrent_jobs?: number;
    max_execution_duration?: number;
  }
): Promise<CommunityNode> {
  return fetchWithAuth<CommunityNode>(`/api/v1/community/nodes/${nodeId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function updateNodeModels(
  nodeId: string,
  models: { model_name: string; enabled: boolean }[]
): Promise<CommunityNodeModel[]> {
  return fetchWithAuth<CommunityNodeModel[]>(`/api/v1/community/nodes/${nodeId}/models`, {
    method: "PUT",
    body: JSON.stringify({ models }),
  });
}

export async function pauseNode(nodeId: string): Promise<void> {
  await fetchWithAuth<void>(`/api/v1/community/nodes/${nodeId}/pause`, {
    method: "POST",
  });
}

export async function resumeNode(nodeId: string): Promise<void> {
  await fetchWithAuth<void>(`/api/v1/community/nodes/${nodeId}/resume`, {
    method: "POST",
  });
}

export async function revokeNode(nodeId: string): Promise<void> {
  await fetchWithAuth<void>(`/api/v1/community/nodes/${nodeId}`, {
    method: "DELETE",
  });
}

export async function listAvailableCommunityModels(): Promise<CommunityAvailableModel[]> {
  return fetchWithAuth<CommunityAvailableModel[]>("/api/v1/community/models");
}

export async function submitCommunityJob(
  req: CommunityJobSubmitRequest
): Promise<CommunityJobSubmitResponse> {
  return fetchWithAuth<CommunityJobSubmitResponse>("/api/v1/community/jobs/submit", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function getCommunityJobStatus(jobId: string): Promise<CommunityJobStatusResponse> {
  return fetchWithAuth<CommunityJobStatusResponse>(`/api/v1/community/jobs/${jobId}`);
}

export async function cancelCommunityJob(jobId: string): Promise<void> {
  await fetchWithAuth<void>(`/api/v1/community/jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export async function streamCommunityJob(
  jobId: string,
  callbacks: {
    onChunk: (chunk: CommunityStreamChunk) => void;
    onError?: (error: Error) => void;
    onComplete?: (status?: CommunityJobStatusResponse) => void;
  },
  options?: {
    signal?: AbortSignal;
    lastEventId?: string;
  }
): Promise<void> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const url = `${API_BASE.replace(/\/$/, "")}/api/v1/community/jobs/${jobId}/stream`;
  const headers = new Headers();
  headers.set("Accept", "text/event-stream");

  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }

  if (options?.lastEventId) {
    headers.set("Last-Event-ID", options.lastEventId);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers,
      signal: options?.signal,
    });
  } catch (err) {
    if (options?.signal?.aborted) return;
    callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
    return;
  }

  if (!response.ok) {
    const errorMsg = `Stream failed with status ${response.status}`;
    callbacks.onError?.(new Error(errorMsg));
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onError?.(new Error("Response body is not readable"));
    return;
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      let currentEvent = "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          currentEvent = "";
          continue;
        }

        if (trimmed.startsWith("event:")) {
          currentEvent = trimmed.slice(6).trim();
        } else if (trimmed.startsWith("data:")) {
          const dataStr = trimmed.slice(5).trim();
          try {
            const parsed = JSON.parse(dataStr);
            if (currentEvent === "chunk" || !currentEvent) {
              const chunk = parsed as CommunityStreamChunk;
              callbacks.onChunk(chunk);
              if (chunk.done) {
                callbacks.onComplete?.();
              }
            } else if (currentEvent === "complete") {
              callbacks.onComplete?.(parsed as CommunityJobStatusResponse);
            } else if (currentEvent === "error") {
              callbacks.onError?.(new Error(parsed.detail || parsed.message || "Job failed"));
            }
          } catch {
            // Ignore partial non-JSON line
          }
        }
      }
    }
    callbacks.onComplete?.();
  } catch (err) {
    if (options?.signal?.aborted) return;
    callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
  } finally {
    reader.releaseLock();
  }
}
