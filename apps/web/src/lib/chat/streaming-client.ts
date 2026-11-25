import { ApiError } from "@/lib/api";
import { cancelGeneration } from "@/lib/chat-client";
import { createClient } from "@/lib/supabase/client";
import type {
  CancelGenerationResponse,
  ChatStreamEvent,
  CloudStreamMessageRequest,
} from "@/types/chat";

const DEFAULT_API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export interface RawSseFrame {
  id?: string;
  event: string;
  data: string;
}

/**
 * Incrementally parse a Web ReadableStream of UTF-8 SSE bytes into raw frames.
 * Safely handles:
 *   - Multi-byte UTF-8 characters split across chunks (TextDecoder stream mode)
 *   - Multiple SSE events packed inside one chunk or single events split across many chunks
 *   - Multi-line `data:` fields joined with `\n`
 *   - `\r\n` and `\n` line terminators
 *   - SSE comment / `: keep-alive` frames (silently ignored)
 */
export async function* parseSseByteStream(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<RawSseFrame, void, unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");

  let buffer = "";
  let currentEvent = "message";
  let currentId: string | undefined;
  let dataLines: string[] = [];

  const flushFrame = (): RawSseFrame | null => {
    if (dataLines.length === 0) {
      currentEvent = "message";
      currentId = undefined;
      return null;
    }
    const frame: RawSseFrame = {
      event: currentEvent,
      data: dataLines.join("\n"),
    };
    if (currentId !== undefined) {
      frame.id = currentId;
    }
    dataLines = [];
    currentEvent = "message";
    currentId = undefined;
    return frame;
  };

  const processLine = (rawLine: string): RawSseFrame | null => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      return flushFrame();
    }
    if (line.startsWith(":")) {
      return null;
    }
    const colonIdx = line.indexOf(":");
    const field = colonIdx === -1 ? line : line.slice(0, colonIdx);
    let value = colonIdx === -1 ? "" : line.slice(colonIdx + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      currentEvent = value || "message";
    } else if (field === "id") {
      currentId = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
    return null;
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        let nlIdx = buffer.indexOf("\n");
        while (nlIdx !== -1) {
          const line = buffer.slice(0, nlIdx);
          buffer = buffer.slice(nlIdx + 1);
          const emitted = processLine(line);
          if (emitted) {
            yield emitted;
          }
          nlIdx = buffer.indexOf("\n");
        }
      }
    }

    buffer += decoder.decode();
    if (buffer.length > 0) {
      for (const trailing of buffer.split("\n")) {
        const emitted = processLine(trailing);
        if (emitted) {
          yield emitted;
        }
      }
    }
    const dangling = flushFrame();
    if (dangling) {
      yield dangling;
    }
  } finally {
    reader.releaseLock();
  }
}

async function parseErrorResponse(res: Response): Promise<ApiError> {
  let errCode = "HTTP_ERROR";
  let errMsg = `Request failed with status ${res.status}`;
  let errDetails: unknown = null;

  try {
    const data = await res.json();
    if (data?.error) {
      errCode = data.error.code || errCode;
      errMsg = data.error.message || errMsg;
      errDetails = data.error.details;
    } else if (data?.detail) {
      errMsg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    }
  } catch {
    // Response body is not JSON
  }

  return new ApiError(errCode, errMsg, res.status, errDetails);
}

export interface StreamConversationOptions {
  signal?: AbortSignal;
  apiBase?: string;
  headers?: HeadersInit;
  onEvent?: (event: ChatStreamEvent) => void;
}

/**
 * Submit a student message to the cloud SSE streaming endpoint and yield typed lifecycle events.
 * Respects Supabase bearer auth and propagates pre-stream HTTP errors as typed `ApiError`.
 */
export async function* streamConversationMessage(
  conversationId: string,
  request: CloudStreamMessageRequest,
  options: StreamConversationOptions = {}
): AsyncGenerator<ChatStreamEvent, void, unknown> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  headers.set("Accept", "text/event-stream");
  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }

  const base = (options.apiBase || DEFAULT_API_BASE).replace(/\/$/, "");
  const url = `${base}/api/v1/chat/conversations/${encodeURIComponent(conversationId)}/messages`;

  const response = await fetch(url, {
    method: "POST",
    headers,
    signal: options.signal,
    body: JSON.stringify({
      ...request,
      stream: true,
    }),
  });

  if (!response.ok) {
    throw await parseErrorResponse(response);
  }

  if (!response.body) {
    throw new ApiError("EMPTY_STREAM_BODY", "Streaming response body was missing", 502);
  }

  for await (const frame of parseSseByteStream(response.body)) {
    let parsedData: unknown;
    try {
      parsedData = JSON.parse(frame.data);
    } catch {
      continue;
    }

    const typedEvent = {
      ...(frame.id !== undefined ? { id: frame.id } : {}),
      event: frame.event,
      data: parsedData,
    } as ChatStreamEvent;

    options.onEvent?.(typedEvent);
    yield typedEvent;
  }
}

export async function abortAndCancelGeneration(
  controller: AbortController,
  messageId: string | null | undefined,
  conversationId?: string
): Promise<CancelGenerationResponse | null> {
  controller.abort();
  if (!messageId) {
    return null;
  }
  return cancelGeneration(messageId, conversationId);
}
