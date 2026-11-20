import { beforeEach, describe, expect, it, vi } from "vitest";
import { streamCommunityJob } from "@/lib/community-client";

// Mock Supabase client
const mockGetSession = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: mockGetSession,
    },
  }),
}));

describe("community-client streamCommunityJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSession.mockResolvedValue({
      data: { session: { access_token: "test-secret-bearer-jwt" } },
    });
  });

  it("includes Authorization Bearer header and does not put token in URL", async () => {
    let capturedUrl = "";
    let capturedHeaders: Headers | undefined;

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('event: chunk\ndata: {"delta": "Hello", "seq_id": 1, "done": false}\n\n')
        );
        controller.enqueue(
          new TextEncoder().encode('event: chunk\ndata: {"delta": " world", "seq_id": 2, "done": true}\n\n')
        );
        controller.close();
      },
    });

    globalThis.fetch = vi.fn().mockImplementation((url, init) => {
      capturedUrl = String(url);
      capturedHeaders = init?.headers as Headers;
      return Promise.resolve(new Response(stream, { status: 200 }));
    });

    const chunks: string[] = [];
    let completed = false;

    await streamCommunityJob("job-1234-abcd", {
      onChunk: (c) => chunks.push(c.delta),
      onComplete: () => {
        completed = true;
      },
    });

    // 1. Verify URL does NOT contain the token
    expect(capturedUrl).not.toContain("test-secret-bearer-jwt");
    expect(capturedUrl).toContain("/api/v1/community/jobs/job-1234-abcd/stream");

    // 2. Verify Authorization header is present
    expect(capturedHeaders?.get("Authorization")).toBe("Bearer test-secret-bearer-jwt");
    expect(capturedHeaders?.get("Accept")).toBe("text/event-stream");

    // 3. Verify chunks delivered
    expect(chunks).toEqual(["Hello", " world"]);
    expect(completed).toBe(true);
  });

  it("handles HTTP 401, 403, and 404 responses via onError callback", async () => {
    for (const statusCode of [401, 403, 404]) {
      globalThis.fetch = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Forbidden or Not Found" }), {
          status: statusCode,
          statusText: statusCode === 401 ? "Unauthorized" : statusCode === 403 ? "Forbidden" : "Not Found",
        })
      );

      let capturedError: Error | undefined;
      await streamCommunityJob("job-err", {
        onChunk: vi.fn(),
        onError: (err) => {
          capturedError = err;
        },
      });

      expect(capturedError).toBeDefined();
      expect((capturedError as unknown as Error)?.message).toContain(`status ${statusCode}`);
      // Token must not leak in error message
      expect((capturedError as unknown as Error)?.message).not.toContain("test-secret-bearer-jwt");
    }
  });

  it("attaches Last-Event-ID header for cursor resumption", async () => {
    let capturedHeaders: Headers | undefined;

    globalThis.fetch = vi.fn().mockImplementation((_url, init) => {
      capturedHeaders = init?.headers as Headers;
      const stream = new ReadableStream({
        start(controller) {
          controller.close();
        },
      });
      return Promise.resolve(new Response(stream, { status: 200 }));
    });

    await streamCommunityJob(
      "job-replay",
      { onChunk: vi.fn() },
      { lastEventId: "42" }
    );

    expect(capturedHeaders?.get("Last-Event-ID")).toBe("42");
  });

  it("correctly reassembles multi-byte UTF-8 characters split across network chunks", async () => {
    // UTF-8 representation of '€' (Euro sign) is 3 bytes: 0xE2, 0x82, 0xAC
    const euroByte1 = new Uint8Array([0xE2]);
    const euroByte23 = new Uint8Array([0x82, 0xAC]);

    const prefix = new TextEncoder().encode('event: chunk\ndata: {"delta": "');
    const suffix = new TextEncoder().encode('", "seq_id": 1, "done": true}\n\n');

    // Network chunk 1: prefix + first byte of €
    const chunk1 = new Uint8Array(prefix.length + 1);
    chunk1.set(prefix);
    chunk1.set(euroByte1, prefix.length);

    // Network chunk 2: remaining 2 bytes of € + suffix
    const chunk2 = new Uint8Array(2 + suffix.length);
    chunk2.set(euroByte23);
    chunk2.set(suffix, 2);

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(chunk1);
        controller.enqueue(chunk2);
        controller.close();
      },
    });

    globalThis.fetch = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));

    const chunks: string[] = [];
    await streamCommunityJob("job-utf8", {
      onChunk: (c) => chunks.push(c.delta),
    });

    expect(chunks).toEqual(["€"]);
  });

  it("cancels gracefully when AbortSignal is triggered", async () => {
    const controller = new AbortController();

    globalThis.fetch = vi.fn().mockImplementation((_url, init) => {
      const signal: AbortSignal = init?.signal;
      if (signal?.aborted) {
        return Promise.reject(new DOMException("The user aborted a request.", "AbortError"));
      }
      return new Promise((_, reject) => {
        signal.addEventListener("abort", () => {
          reject(new DOMException("The user aborted a request.", "AbortError"));
        });
      });
    });

    const onError = vi.fn();
    const promise = streamCommunityJob(
      "job-abort",
      { onChunk: vi.fn(), onError },
      { signal: controller.signal }
    );

    // Trigger abort
    controller.abort();
    await promise;

    // Abort should not trigger onError
    expect(onError).not.toHaveBeenCalled();
  });

  it("safely ignores malformed or non-JSON event lines without crashing", async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("invalid garbage text without event\n"));
        controller.enqueue(new TextEncoder().encode("event: chunk\ndata: {broken json\n\n"));
        controller.enqueue(
          new TextEncoder().encode('event: chunk\ndata: {"delta": "valid", "seq_id": 1, "done": true}\n\n')
        );
        controller.close();
      },
    });

    globalThis.fetch = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));

    const chunks: string[] = [];
    await streamCommunityJob("job-malformed", {
      onChunk: (c) => chunks.push(c.delta),
    });

    expect(chunks).toEqual(["valid"]);
  });
});
