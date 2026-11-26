import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  abortAndCancelGeneration,
  parseSseByteStream,
  streamConversationMessage,
} from "@/lib/chat/streaming-client";

const mockGetSession = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: mockGetSession,
    },
  }),
}));

vi.mock("@/lib/chat-client", () => ({
  cancelGeneration: vi.fn().mockResolvedValue({
    message_id: "msg-1",
    conversation_id: "conv-1",
    status: "cancelled",
    already_cancelled: false,
    retained_length: 12,
    reason: "user_requested",
  }),
}));

import { cancelGeneration } from "@/lib/chat-client";

function streamFromChunks(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let idx = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (idx < chunks.length) {
        controller.enqueue(chunks[idx]);
        idx += 1;
      } else {
        controller.close();
      }
    },
  });
}

describe("SSE Streaming Client Helper (M4.6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSession.mockResolvedValue({
      data: { session: { access_token: "test-jwt-token" } },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses split UTF-8 Bengali/Hindi multibyte chunks, comments, CRLF, and multi-line data cleanly", async () => {
    const enc = new TextEncoder();
    const bengaliText = "অপারেটিং সিস্টেম — পেজিং এবং সেগমেন্টেশন [S1]।" ;
    const fullStreamBytes = enc.encode(
      `: keep-alive\r\n\r\n` +
        `id: 1\r\nevent: generation_started\r\ndata: {"message_id":"m1","user_message_id":"u1","conversation_id":"c1","generation_request_id":"req-1","model_id":"llama","provider_id":"groq","resolved_model_id":"llama","streaming":true,"started_at":"2026-03-07T00:00:00Z"}\r\n\r\n` +
        `id: 2\nevent: text_delta\ndata: {"message_id":"m1",\ndata: "delta":"${bengaliText}"}\n\n` +
        `: ping-mid-stream\n\n`
    );

    // Slice right through multi-byte UTF-8 code points (7 bytes at a time)
    const tinyChunks: Uint8Array[] = [];
    for (let i = 0; i < fullStreamBytes.length; i += 7) {
      tinyChunks.push(fullStreamBytes.slice(i, i + 7));
    }

    const frames = [];
    for await (const frame of parseSseByteStream(streamFromChunks(tinyChunks))) {
      frames.push(frame);
    }

    expect(frames).toHaveLength(2);
    expect(frames[0].event).toBe("generation_started");
    expect(frames[0].id).toBe("1");
    expect(frames[1].event).toBe("text_delta");
    expect(frames[1].id).toBe("2");
    const deltaPayload = JSON.parse(frames[1].data);
    expect(deltaPayload.delta).toBe(bengaliText);
  });

  it("streams multiple events delivered inside a single TCP chunk including repair content_replaced", async () => {
    const enc = new TextEncoder();
    const ssePayload =
      `id: 1\nevent: generation_started\ndata: {"message_id":"m2","user_message_id":"u2","conversation_id":"conv-1","generation_request_id":"req-2","model_id":"llama","provider_id":"groq","resolved_model_id":"llama","streaming":true,"started_at":"2026-03-07T00:00:00Z"}\n\n` +
      `id: 2\nevent: sources_identified\ndata: {"message_id":"m2","conversation_id":"conv-1","sources":[],"omitted_count":0,"retrieval_candidate_count":1}\n\n` +
      `id: 3\nevent: text_delta\ndata: {"message_id":"m2","delta":"Draft answer"}\n\n` +
      `id: 4\nevent: content_replaced\ndata: {"message_id":"m2","content":"Draft answer [S1]","reason":"citation_repair"}\n\n` +
      `id: 5\nevent: generation_completed\ndata: {"message_id":"m2","user_message_id":"u2","conversation_id":"conv-1","content":"Draft answer [S1]","finish_reason":"stop","usage":{},"citation_validation_status":"valid_grounded","valid_citations":["S1"],"repair_attempted":true,"sources":[],"user_message":{},"assistant_message":{},"telemetry":{}}\n\n`;

    const mockFetch = vi.fn().mockResolvedValueOnce(
      new Response(streamFromChunks([enc.encode(ssePayload)]), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    );
    vi.stubGlobal("fetch", mockFetch);

    const seen: string[] = [];
    const events = [];
    for await (const ev of streamConversationMessage(
      "conv-1",
      { content: "Explain paging" },
      {
        apiBase: "http://api.local",
        onEvent: (ev) => seen.push(ev.event),
      }
    )) {
      events.push(ev);
    }

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [reqUrl, reqInit] = mockFetch.mock.calls[0];
    expect(reqUrl).toBe("http://api.local/api/v1/chat/conversations/conv-1/messages");
    expect((reqInit.headers as Headers).get("Authorization")).toBe("Bearer test-jwt-token");
    expect((reqInit.headers as Headers).get("Accept")).toBe("text/event-stream");
    expect(JSON.parse(String(reqInit.body))).toEqual({
      content: "Explain paging",
      stream: true,
    });
    expect(seen).toEqual([
      "generation_started",
      "sources_identified",
      "text_delta",
      "content_replaced",
      "generation_completed",
    ]);
    expect(events[3].event).toBe("content_replaced");
  });

  it("throws typed ApiError on pre-stream HTTP errors such as 409/429/503", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "USER_CONCURRENCY_LIMIT",
            message: "Maximum 2 concurrent generations per student reached.",
            details: { scope: "user", limit: 2 },
          },
        }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", mockFetch);

    await expect(async () => {
      for await (const _ of streamConversationMessage("conv-1", { content: "Hi" })) {
        // should reject before iterating
      }
    }).rejects.toMatchObject({
      name: "ApiError",
      code: "USER_CONCURRENCY_LIMIT",
      status: 429,
    } satisfies Partial<ApiError>);
  });

  it("aborts AbortController and calls cancelGeneration endpoint cleanly", async () => {
    const controller = new AbortController();
    await abortAndCancelGeneration(controller, "msg-1", "conv-1");

    expect(controller.signal.aborted).toBe(true);
    expect(cancelGeneration).toHaveBeenCalledWith("msg-1", "conv-1");
  });

  it("skips malformed JSON payloads cleanly and preserves stream flow across unknown SSE event names", async () => {
    const ssePayload = [
      ": heartbeat\n\n",
      "event: future_unknown_event\ndata: {\"foo\":true}\n\n",
      "event: text_delta\ndata: {not-valid-json\n\n",
      'event: text_delta\ndata: {"message_id":"asst-1","delta":"Valid"}\n\n',
    ].join("");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(
        new Response(streamFromChunks([new TextEncoder().encode(ssePayload)]), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        })
      )
    );

    const events = [];
    for await (const ev of streamConversationMessage("conv-1", { content: "Test" })) {
      events.push(ev);
    }

    expect(events.map((e) => e.event)).toEqual(["future_unknown_event", "text_delta"]);
    expect((events[1].data as { delta: string }).delta).toBe("Valid");
  });
});
