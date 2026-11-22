import { beforeEach, describe, expect, it, vi } from "vitest";
import { previewRoute, streamChatCompletion, type RouteHeaders } from "@/lib/routing-client";

// Mock Supabase client
const mockGetSession = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: mockGetSession,
    },
  }),
}));

describe("M2.9 Routing Client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSession.mockResolvedValue({
      data: { session: { access_token: "test-auth-jwt" } },
    });
  });

  it("calls /api/v1/routing/preview with bearer auth and JSON body", async () => {
    let capturedUrl = "";
    let capturedInit: RequestInit | undefined;

    const mockResponse = {
      routing_mode: "auto",
      selected_candidate: {
        source_type: "cloud",
        provider_id: "google",
        model_id: "gemini-2.5-flash",
        display_name: "Google Gemini 2.5 Flash",
        family: "gemini",
        free_tier_eligible: true,
      },
      candidates_considered: 4,
      candidates_eligible: 3,
      decision_reason: "Free tier candidate selected",
      rejected_reasons_summary: {},
    };

    globalThis.fetch = vi.fn().mockImplementation((url, init) => {
      capturedUrl = String(url);
      capturedInit = init;
      return Promise.resolve(
        new Response(JSON.stringify(mockResponse), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
    });

    const result = await previewRoute({
      messages: [{ role: "user", content: "Hello router" }],
      routing_mode: "auto",
    });

    expect(capturedUrl).toContain("/api/v1/routing/preview");
    expect((capturedInit?.headers as Headers).get("Authorization")).toBe("Bearer test-auth-jwt");
    expect(result.routing_mode).toBe("auto");
    expect(result.selected_candidate?.model_id).toBe("gemini-2.5-flash");
  });

  it("streams chat completion and extracts routing response headers", async () => {
    let capturedUrl = "";
    let capturedBody: string | undefined;

    const ssePayload = [
      'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"}}]}\n\n',
      'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":" from router!"}}]}\n\n',
      "data: [DONE]\n\n",
    ].join("");

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(ssePayload));
        controller.close();
      },
    });

    const responseHeaders = new Headers({
      "Content-Type": "text/event-stream",
      "X-RagBait-Routing-Mode": "auto",
      "X-RagBait-Selected-Source": "cloud",
      "X-RagBait-Selected-Model": "gemini-2.5-flash",
      "X-RagBait-Decision-Reason": "Optimal free tier model",
    });

    globalThis.fetch = vi.fn().mockImplementation((url, init) => {
      capturedUrl = String(url);
      capturedBody = init?.body as string;
      return Promise.resolve(new Response(stream, { status: 200, headers: responseHeaders }));
    });

    const deltas: string[] = [];
    let receivedHeaders: RouteHeaders = {};
    let completed = false;

    await streamChatCompletion(
      {
        messages: [{ role: "user", content: "Hello world" }],
        routing_mode: "auto",
        community_opt_in: false,
      },
      {
        onDelta: (d) => deltas.push(d),
        onRouteHeaders: (h) => {
          receivedHeaders = h;
        },
        onComplete: () => {
          completed = true;
        },
      }
    );

    expect(capturedUrl).toContain("/api/v1/llm/chat/completions");
    expect(JSON.parse(capturedBody || "{}")).toMatchObject({
      routing_mode: "auto",
      community_opt_in: false,
      stream: true,
    });
    expect(deltas.join("")).toBe("Hello from router!");
    expect(receivedHeaders.routingMode).toBe("auto");
    expect(receivedHeaders.selectedSource).toBe("cloud");
    expect(receivedHeaders.selectedModel).toBe("gemini-2.5-flash");
    expect(completed).toBe(true);
  });

  it("handles HTTP error during routed chat completion stream", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "User daily spending limit exceeded" }), {
        status: 403,
      })
    );

    let capturedError: Error | undefined;
    await streamChatCompletion(
      {
        messages: [{ role: "user", content: "Hello" }],
        routing_mode: "cloud_only",
      },
      {
        onDelta: vi.fn(),
        onError: (err) => {
          capturedError = err;
        },
      }
    );

    expect(capturedError).toBeDefined();
    expect(capturedError?.message).toContain("User daily spending limit exceeded");
  });

  it("extracts and formats detailed validation errors from error.details on 422 response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "VALIDATION_ERROR",
            message: "Input validation failed",
            details: [
              {
                type: "string_too_short",
                loc: ["body", "messages", 0, "content"],
                msg: "String should have at least 1 character",
              },
            ],
          },
        }),
        { status: 422 }
      )
    );

    let capturedError: Error | undefined;
    await streamChatCompletion(
      {
        messages: [{ role: "user", content: "Test" }],
        routing_mode: "auto",
      },
      {
        onDelta: vi.fn(),
        onError: (err) => {
          capturedError = err;
        },
      }
    );

    expect(capturedError).toBeDefined();
    expect(capturedError?.message).toContain("Input validation failed: messages.0.content: String should have at least 1 character");
  });

  it("streams chat completion with native RagBait text_delta SSE events", async () => {
    const ssePayload = [
      'event: generation_started\n',
      'data: {"request_id":"req-1","model_id":"openrouter/free"}\n\n',
      'event: text_delta\n',
      'data: {"request_id":"req-1","delta":"Hello "}\n\n',
      'event: text_delta\n',
      'data: {"request_id":"req-1","delta":"from native RagBait!"}\n\n',
      'event: generation_completed\n',
      'data: {"request_id":"req-1","finish_reason":"stop"}\n\n',
    ].join("");

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(ssePayload));
        controller.close();
      },
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: new Headers({ "Content-Type": "text/event-stream" }),
      })
    );

    const deltas: string[] = [];
    let completed = false;

    await streamChatCompletion(
      {
        messages: [{ role: "user", content: "Hi" }],
        routing_mode: "auto",
      },
      {
        onDelta: (d) => deltas.push(d),
        onComplete: () => {
          completed = true;
        },
      }
    );

    expect(deltas.join("")).toBe("Hello from native RagBait!");
    expect(completed).toBe(true);
  });

  it("handles server-emitted generation_error event in SSE stream", async () => {
    const ssePayload = [
      'event: generation_started\n',
      'data: {"request_id":"req-2","model_id":"openrouter/free"}\n\n',
      'event: generation_error\n',
      'data: {"request_id":"req-2","error_code":"PROVIDER_INVALID_CREDENTIALS","message":"The configured provider credential was rejected as invalid or unauthorized"}\n\n',
    ].join("");

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(ssePayload));
        controller.close();
      },
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: new Headers({ "Content-Type": "text/event-stream" }),
      })
    );

    let capturedError: Error | undefined;

    await streamChatCompletion(
      {
        messages: [{ role: "user", content: "Hi" }],
        routing_mode: "auto",
      },
      {
        onDelta: vi.fn(),
        onError: (err) => {
          capturedError = err;
        },
      }
    );

    expect(capturedError).toBeDefined();
    expect(capturedError?.message).toContain("The configured provider credential was rejected as invalid or unauthorized");
  });
});
