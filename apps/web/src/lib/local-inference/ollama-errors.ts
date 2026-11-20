/**
 * Categorized error types for Ollama local inference.
 */

export type OllamaErrorCode =
  | "CONNECTION_FAILED"
  | "LOOPBACK_PERMISSION_DENIED"
  | "LOOPBACK_PERMISSION_REQUIRED"
  | "INSECURE_CONTEXT"
  | "OLLAMA_UNREACHABLE"
  | "CORS_BLOCKED"
  | "MODEL_NOT_FOUND"
  | "STREAM_ERROR"
  | "HARDWARE_LIMIT_EXCEEDED"
  | "ABORTED"
  | "PARSE_ERROR"
  | "UNKNOWN_ERROR";

export class OllamaError extends Error {
  readonly code: OllamaErrorCode;
  readonly status?: number;
  readonly details?: unknown;

  constructor(code: OllamaErrorCode, message: string, status?: number, details?: unknown) {
    super(message);
    this.name = "OllamaError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

/**
 * Translates raw Ollama runner errors (e.g. C++ stack overflow / CUDA / OOM crashes)
 * into reassuring, actionable guidance for students.
 */
export function translateOllamaErrorMessage(rawMessage: string, modelName?: string): string {
  const lower = rawMessage.toLowerCase();
  const isOomOrCrash =
    lower.includes("llama-server process no longer running") ||
    lower.includes("0xc0000409") ||
    lower.includes("cuda error") ||
    lower.includes("out of memory") ||
    lower.includes("failed to allocate") ||
    lower.includes("shared object initialization failed") ||
    lower.includes("stack-based buffer");

  if (isOomOrCrash) {
    const target = modelName ? `'${modelName}'` : "this model";
    return `Hardware / Memory Limit Exceeded: Ollama ran out of GPU memory (VRAM) or RAM while loading ${target}. This model is likely too large for your machine's available hardware. Please select a lighter model (such as a 3B or 0.6B model) or close heavy applications to free up system memory.`;
  }

  return rawMessage;
}

/**
 * Checks whether an error is an AbortError.
 */
export function isAbortError(err: unknown): boolean {
  if (err instanceof Error) {
    return err.name === "AbortError" || (err as OllamaError).code === "ABORTED";
  }
  return false;
}

/**
 * Normalizes an unknown network error when connecting to Ollama.
 *
 * NOTE: Modern web browsers strictly redact cross-origin network errors for security reasons.
 * A failure due to Ollama being stopped, a CORS preflight failure, or a Local Network Access (LNA)
 * block all result in an identical generic TypeError ("Failed to fetch" or "NetworkError").
 * We must NOT invent certainty from opaque browser errors by assuming CORS is the only cause.
 */
export function classifyFetchError(err: unknown): OllamaError {
  if (err instanceof OllamaError) {
    return err;
  }
  if (err instanceof Error) {
    if (err.name === "AbortError") {
      return new OllamaError("ABORTED", "Request was aborted by user", undefined, err);
    }

    const msg = err.message.toLowerCase();
    // Generic opaque browser errors for network failure, connection refused, or blocked fetch
    if (msg.includes("failed to fetch") || msg.includes("networkerror") || msg.includes("load failed")) {
      return new OllamaError(
        "CONNECTION_FAILED",
        "Unable to connect to Ollama at http://127.0.0.1:11434. The browser blocked or failed the network request. Check that the Ollama daemon is running, that OLLAMA_ORIGINS allows this origin, and that browser local network permissions are granted.",
        undefined,
        err
      );
    }
    return new OllamaError("OLLAMA_UNREACHABLE", err.message, undefined, err);
  }
  return new OllamaError("UNKNOWN_ERROR", "An unexpected error occurred while communicating with Ollama", undefined, err);
}
