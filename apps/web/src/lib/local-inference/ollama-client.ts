import { isSecureLoopbackContext, queryLoopbackPermission } from "./lna-permissions";
import { classifyFetchError, OllamaError, translateOllamaErrorMessage } from "./ollama-errors";
import { parseNDJSONStream } from "./ollama-stream";
import type {
  LoopbackRequestInit,
  OllamaChatChunk,
  OllamaChatMessage,
  OllamaChatOptions,
  OllamaChatRequest,
  OllamaConnectionInfo,
  OllamaShowResponse,
  OllamaTagModel,
  OllamaTagsResponse,
  OllamaUsageStats,
  OllamaVersionResponse,
} from "./ollama-types";

/**
 * Hardcoded loopback URL for local Ollama daemon.
 * In accordance with RagBait privacy and security architecture, this is strictly
 * hardcoded to loopback and cannot be overridden by user configuration or query parameters.
 */
export const OLLAMA_BASE_URL = "http://127.0.0.1:11434";

/**
 * Isolated fetch helper targeting local Ollama daemon.
 * Enforces:
 * 1. credentials: "omit" - never sends cookies or Supabase auth headers.
 * 2. redirect: "error" - rejects any HTTP redirect to prevent traversal attacks.
 * 3. targetAddressSpace: "loopback" - declares loopback address space for Chrome Local Network Access (LNA).
 * 4. cache: "no-store" - prevents browser caching of local API responses.
 */
async function ollamaFetch(
  path: string,
  options: LoopbackRequestInit = {}
): Promise<Response> {
  const url = `${OLLAMA_BASE_URL}${path}`;

  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.method && options.method !== "GET") {
    headers.set("Content-Type", "application/json");
  }

  // Type-safe declaration of loopback address space compatible with modern browser LNA
  const init: LoopbackRequestInit = {
    ...options,
    mode: "cors",
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
    targetAddressSpace: "loopback",
    headers,
  };

  try {
    return await fetch(url, init);
  } catch (err) {
    throw classifyFetchError(err);
  }
}

export class OllamaClient {
  /**
   * Probes the Ollama daemon for reachability and version.
   * Differentiates between connected, disconnected, permission denied, and opaque connection failures.
   */
  async checkConnection(signal?: AbortSignal): Promise<OllamaConnectionInfo> {
    // 1. Verify secure context requirement
    if (!isSecureLoopbackContext()) {
      return {
        state: "insecure_context",
        error:
          "Local Network Access requires a secure HTTPS origin or localhost development context.",
      };
    }

    // 2. Query permission state if supported
    const permResult = await queryLoopbackPermission();
    if (permResult.state === "denied") {
      return {
        state: "loopback_permission_denied",
        error:
          "Browser Local Network Access permission was denied for http://127.0.0.1:11434.",
      };
    }

    try {
      const res = await ollamaFetch("/api/version", {
        method: "GET",
        signal,
      });

      if (!res.ok) {
        return {
          state: "disconnected",
          error: `Ollama daemon returned HTTP ${res.status}: ${res.statusText}`,
        };
      }

      const data = (await res.json()) as OllamaVersionResponse;
      return {
        state: "connected",
        version: data.version,
      };
    } catch (err) {
      if (err instanceof OllamaError) {
        if (err.code === "ABORTED") {
          return { state: "disconnected", error: "Connection probe canceled" };
        }

        // Re-check permission state after failed fetch: user might have clicked Block in prompt
        const postPermResult = await queryLoopbackPermission();
        if (postPermResult.state === "denied") {
          return {
            state: "loopback_permission_denied",
            error:
              "Browser Local Network Access permission was denied for http://127.0.0.1:11434.",
          };
        }

        if (err.code === "CONNECTION_FAILED") {
          return {
            state: "connection_failed",
            error: err.message,
          };
        }

        return {
          state: "disconnected",
          error: err.message,
        };
      }

      return {
        state: "connection_failed",
        error: err instanceof Error ? err.message : "Unknown connection error",
      };
    }
  }

  /**
   * Retrieves all locally installed models from Ollama (/api/tags).
   */
  async listModels(signal?: AbortSignal): Promise<OllamaTagModel[]> {
    const res = await ollamaFetch("/api/tags", {
      method: "GET",
      signal,
    });

    if (!res.ok) {
      throw new OllamaError(
        "OLLAMA_UNREACHABLE",
        `Failed to fetch local models (HTTP ${res.status})`,
        res.status
      );
    }

    const data = (await res.json()) as OllamaTagsResponse;
    return data.models || [];
  }

  /**
   * Fetches detailed information for a specific local model (/api/show).
   */
  async showModel(name: string, signal?: AbortSignal): Promise<OllamaShowResponse | null> {
    const res = await ollamaFetch("/api/show", {
      method: "POST",
      body: JSON.stringify({ name }),
      signal,
    });

    if (res.status === 404) {
      return null;
    }

    if (!res.ok) {
      throw new OllamaError(
        "MODEL_NOT_FOUND",
        `Failed to inspect model ${name} (HTTP ${res.status})`,
        res.status
      );
    }

    return (await res.json()) as OllamaShowResponse;
  }

  /**
   * Initiates a streaming chat completion with the local Ollama instance (/api/chat).
   *
   * NOTE: In accordance with M2.7 audit requirements, raw thinking/reasoning tokens
   * (e.g. from Qwen 3 or DeepSeek R1) are strictly ignored and never exposed to the UI,
   * caller callbacks, state, logs, or analytics. Only ordinary message.content is delivered.
   */
  async chatStream(
    request: OllamaChatRequest,
    callbacks: {
      onDelta?: (content: string) => void;
      onStats?: (stats: OllamaUsageStats) => void;
    },
    signal?: AbortSignal
  ): Promise<void> {
    const res = await ollamaFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        ...request,
        stream: true,
      }),
      signal,
    });

    if (!res.ok) {
      let errMsg = `Ollama generation failed (HTTP ${res.status})`;
      try {
        const errJson = await res.json();
        if (errJson.error) {
          errMsg = errJson.error;
        }
      } catch {
        // Body was not JSON
      }
      if (res.status === 404) {
        throw new OllamaError("MODEL_NOT_FOUND", errMsg, res.status);
      }

      const translatedMsg = translateOllamaErrorMessage(errMsg, request.model);
      const isHardwareLimit = translatedMsg !== errMsg;

      throw new OllamaError(
        isHardwareLimit ? "HARDWARE_LIMIT_EXCEEDED" : "STREAM_ERROR",
        translatedMsg,
        res.status
      );
    }

    if (!res.body) {
      throw new OllamaError("STREAM_ERROR", "Response body is empty or unavailable");
    }

    await parseNDJSONStream<OllamaChatChunk>(
      res.body,
      (chunk) => {
        // Exclusively process and yield user-visible content; ignore chunk.message?.thinking
        if (chunk.message?.content && callbacks.onDelta) {
          callbacks.onDelta(chunk.message.content);
        }

        if (chunk.done && callbacks.onStats) {
          const evalDurationMs = (chunk.eval_duration || 0) / 1_000_000;
          const evalCount = chunk.eval_count || 0;
          const tokensPerSecond =
            evalDurationMs > 0 ? Number(((evalCount / (evalDurationMs / 1000))).toFixed(1)) : 0;

          const stats: OllamaUsageStats = {
            totalDurationMs: Math.round((chunk.total_duration || 0) / 1_000_000),
            loadDurationMs: Math.round((chunk.load_duration || 0) / 1_000_000),
            promptEvalCount: chunk.prompt_eval_count || 0,
            promptEvalDurationMs: Math.round((chunk.prompt_eval_duration || 0) / 1_000_000),
            evalCount,
            evalDurationMs: Math.round(evalDurationMs),
            tokensPerSecond,
          };

          callbacks.onStats(stats);
        }
      },
      signal
    );
  }
}

// Export singleton instance
export const ollamaClient = new OllamaClient();
