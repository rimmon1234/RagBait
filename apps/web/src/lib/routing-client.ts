import { createClient } from "@/lib/supabase/client";
import { fetchWithAuth } from "@/lib/api";
import type { RoutePreviewResponse, RoutingMode } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export interface RoutePreviewParams {
  messages: { role: string; content: string }[];
  routing_mode?: RoutingMode;
  model?: string;
  provider_id?: string;
  community_opt_in?: boolean;
}

export interface StreamChatCompletionParams {
  messages: { role: string; content: string }[];
  routing_mode?: RoutingMode;
  model?: string;
  temperature?: number;
  community_opt_in?: boolean;
}

export interface RouteHeaders {
  routingMode?: string;
  selectedSource?: string;
  selectedModel?: string;
  decisionReason?: string;
}

export async function previewRoute(params: RoutePreviewParams): Promise<RoutePreviewResponse> {
  return fetchWithAuth<RoutePreviewResponse>("/api/v1/routing/preview", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function streamChatCompletion(
  params: StreamChatCompletionParams,
  callbacks: {
    onDelta: (delta: string) => void;
    onRouteHeaders?: (headers: RouteHeaders) => void;
    onComplete?: () => void;
    onError?: (error: Error) => void;
  },
  options?: {
    signal?: AbortSignal;
  }
): Promise<void> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const url = `${API_BASE.replace(/\/$/, "")}/api/v1/llm/chat/completions`;
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("Accept", "text/event-stream");

  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        ...params,
        stream: true,
      }),
      signal: options?.signal,
    });
  } catch (err) {
    if (options?.signal?.aborted) return;
    callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
    return;
  }

  // Extract routing response headers if available
  const routeHeaders: RouteHeaders = {
    routingMode: response.headers.get("x-ragbait-routing-mode") || undefined,
    selectedSource: response.headers.get("x-ragbait-selected-source") || undefined,
    selectedModel: response.headers.get("x-ragbait-selected-model") || undefined,
    decisionReason: response.headers.get("x-ragbait-decision-reason") || undefined,
  };
  callbacks.onRouteHeaders?.(routeHeaders);

  if (!response.ok) {
    let errorDetail = `Request failed with status ${response.status}`;
    try {
      const errJson = await response.json();
      if (errJson.detail) {
        errorDetail = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // Body not JSON
    }
    callbacks.onError?.(new Error(errorDetail));
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

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(":")) continue;

        if (trimmed.startsWith("data:")) {
          const dataStr = trimmed.slice(5).trim();
          if (dataStr === "[DONE]") {
            callbacks.onComplete?.();
            return;
          }

          try {
            const parsed = JSON.parse(dataStr);
            const delta = parsed.choices?.[0]?.delta?.content;
            if (delta) {
              callbacks.onDelta(delta);
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
