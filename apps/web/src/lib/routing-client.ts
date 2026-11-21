import { createClient } from "@/lib/supabase/client";
import { fetchWithAuth } from "@/lib/api";
import type { ChatEntitlementResponse, RoutePreviewResponse, RoutingMode } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export type CredentialScope = "eligible_pool" | "owner_only";

export interface RoutePreviewParams {
  messages: { role: string; content: string }[];
  routing_mode?: RoutingMode;
  credential_scope?: CredentialScope;
  model?: string;
  provider_id?: string;
}

export interface StreamChatCompletionParams {
  messages: { role: string; content: string }[];
  routing_mode?: RoutingMode;
  credential_scope?: CredentialScope;
  model?: string;
  temperature?: number;
}

export interface RouteHeaders {
  routingMode?: string;
  selectedSource?: string;
  selectedModel?: string;
  decisionReason?: string;
}

export async function getChatEntitlement(): Promise<ChatEntitlementResponse> {
  return fetchWithAuth<ChatEntitlementResponse>("/api/v1/me/chat-entitlement");
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

  const idempotencyKey = `idem-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  const genParams: Record<string, unknown> = {};
  if (params.temperature !== undefined) {
    genParams.temperature = params.temperature;
  }

  // Filter out any messages with empty or whitespace-only content
  const cleanMessages = (params.messages || []).filter(
    (m) => m && m.content && m.content.trim().length > 0
  );

  const requestBody: Record<string, unknown> = {
    idempotency_key: idempotencyKey,
    credential_scope: params.credential_scope || "eligible_pool",
    routing_mode: params.routing_mode || "auto",
    messages: cleanMessages,
    params: genParams,
    stream: true,
  };
  if (params.model) {
    requestBody.model = params.model;
    requestBody.model_id = params.model;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(requestBody),
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
    if (response.status === 401 && typeof window !== "undefined") {
      window.location.href = "/login";
      return;
    }
    let errorDetail = `Request failed with status ${response.status}`;
    try {
      const errJson = await response.json();
      if (errJson.error?.message) {
        errorDetail = errJson.error.message;
        if (errJson.error.details && Array.isArray(errJson.error.details)) {
          const detailMsgs = errJson.error.details
            .map((d: any) => `${d.loc ? d.loc.join(".") + ": " : ""}${d.msg || JSON.stringify(d)}`)
            .join("; ");
          if (detailMsgs) {
            errorDetail += ` (${detailMsgs})`;
          }
        }
      } else if (errJson.detail) {
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
            const delta =
              parsed.delta ??
              parsed.choices?.[0]?.delta?.content ??
              parsed.content ??
              parsed.text;
            if (delta) {
              callbacks.onDelta(delta);
            }
            if (parsed.error_code || (parsed.message && !delta && parsed.error_code)) {
              callbacks.onError?.(
                new Error(parsed.message || parsed.error_code || "Generation failed")
              );
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
