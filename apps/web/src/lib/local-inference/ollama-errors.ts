/**
 * Categorized error types for Ollama local inference.
 */

export type OllamaErrorCode =
  | "OLLAMA_UNREACHABLE"
  | "CORS_BLOCKED"
  | "MODEL_NOT_FOUND"
  | "STREAM_ERROR"
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
 * In browser environments, a CORS block or refusal typically produces a TypeError ("Failed to fetch" or "NetworkError").
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
    // Common browser messages for CORS or connection failure to loopback
    if (msg.includes("failed to fetch") || msg.includes("networkerror") || msg.includes("load failed")) {
      return new OllamaError(
        "CORS_BLOCKED",
        "Unable to connect to Ollama at http://127.0.0.1:11434. Ensure Ollama is running and OLLAMA_ORIGINS allows this web origin.",
        undefined,
        err
      );
    }
    return new OllamaError("OLLAMA_UNREACHABLE", err.message, undefined, err);
  }
  return new OllamaError("UNKNOWN_ERROR", "An unexpected error occurred while communicating with Ollama", undefined, err);
}
