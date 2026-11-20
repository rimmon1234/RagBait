import { OllamaError } from "./ollama-errors";

/**
 * Parses a ReadableStream of Uint8Array chunks containing newline-delimited JSON (NDJSON).
 * Handles partial chunks, multibyte UTF-8 codepoints across boundaries, and abort signals.
 *
 * @param stream The stream of bytes from the response body.
 * @param onChunk Callback invoked for each successfully parsed JSON object.
 * @param signal Optional AbortSignal to cancel streaming.
 */
export async function parseNDJSONStream<T>(
  stream: ReadableStream<Uint8Array>,
  onChunk: (data: T) => void,
  signal?: AbortSignal
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true });
  let buffer = "";

  const abortHandler = () => {
    reader.cancel().catch(() => {});
  };

  if (signal?.aborted) {
    await reader.cancel().catch(() => {});
    throw new OllamaError("ABORTED", "Stream reading was aborted");
  }

  signal?.addEventListener("abort", abortHandler, { once: true });

  try {
    while (true) {
      if (signal?.aborted) {
        throw new OllamaError("ABORTED", "Stream reading was aborted");
      }

      const { done, value } = await reader.read();

      if (done) {
        // Flush any remaining characters in the decoder
        buffer += decoder.decode();
        break;
      }

      if (value) {
        buffer += decoder.decode(value, { stream: true });
      }

      // Split lines by newline
      const lines = buffer.split(/\r?\n/);
      // The last element is whatever comes after the last newline (could be empty or partial line)
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }

        try {
          const parsed = JSON.parse(trimmed) as T;
          onChunk(parsed);
        } catch (err) {
          throw new OllamaError(
            "PARSE_ERROR",
            `Failed to parse NDJSON line: ${trimmed.slice(0, 100)}`,
            undefined,
            err
          );
        }
      }
    }

    // Process any remaining non-empty line in buffer after stream ends
    const remaining = buffer.trim();
    if (remaining) {
      try {
        const parsed = JSON.parse(remaining) as T;
        onChunk(parsed);
      } catch (err) {
        throw new OllamaError(
          "PARSE_ERROR",
          `Failed to parse trailing NDJSON line: ${remaining.slice(0, 100)}`,
          undefined,
          err
        );
      }
    }
  } catch (err) {
    if (signal?.aborted || (err instanceof Error && err.name === "AbortError")) {
      throw new OllamaError("ABORTED", "Stream reading was aborted");
    }
    if (err instanceof OllamaError) {
      throw err;
    }
    throw new OllamaError("STREAM_ERROR", `Stream error: ${err instanceof Error ? err.message : String(err)}`, undefined, err);
  } finally {
    signal?.removeEventListener("abort", abortHandler);
    try {
      reader.releaseLock();
    } catch {
      // Lock might already be released
    }
  }
}
