import { classifyFetchError, OllamaError } from "./ollama-errors";
import { parseNDJSONStream } from "./ollama-stream";
import type {
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
 * Strictly omits credentials and rejects redirects to protect student tokens and cookies.
 */
async function ollamaFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = `${OLLAMA_BASE_URL}${path}`;

  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.method && options.method !== "GET") {
    headers.set("Content-Type", "application/json");
  }

  // Strictly enforce credentials omit and redirect error
  const init: RequestInit = {
    ...options,
    mode: "cors",
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
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
   * Differentiates between connected, disconnected (not running), and CORS blocked.
   */
  async checkConnection(signal?: AbortSignal): Promise<OllamaConnectionInfo> {
    try {
      const res = await ollamaFetch("/api/version", {
        method: "GET",
        signal,
      });

      if (!res.ok) {
        return {
          state: "disconnected",
          error: `Ollama returned HTTP ${res.status}: ${res.statusText}`,
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
        if (err.code === "CORS_BLOCKED") {
          return {
            state: "cors_blocked",
            error:
              "Unable to reach http://127.0.0.1:11434. Ensure Ollama is running and OLLAMA_ORIGINS is configured to allow this browser origin.",
          };
        }
        return {
          state: "disconnected",
          error: err.message,
        };
      }
      return {
        state: "disconnected",
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
   * Parses the NDJSON stream in real-time, calling callbacks for deltas and final stats.
   */
  async chatStream(
    request: OllamaChatRequest,
    callbacks: {
      onDelta?: (content: string) => void;
      onThinkingDelta?: (thinking: string) => void;
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
      throw new OllamaError(
        res.status === 404 ? "MODEL_NOT_FOUND" : "STREAM_ERROR",
        errMsg,
        res.status
      );
    }

    if (!res.body) {
      throw new OllamaError("STREAM_ERROR", "Response body is empty or unavailable");
    }

    await parseNDJSONStream<OllamaChatChunk>(
      res.body,
      (chunk) => {
        if (chunk.message?.content && callbacks.onDelta) {
          callbacks.onDelta(chunk.message.content);
        }

        if (chunk.message?.thinking && callbacks.onThinkingDelta) {
          callbacks.onThinkingDelta(chunk.message.thinking);
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

// Export singleton instance for convenience
export const ollamaClient = new OllamaClient();
