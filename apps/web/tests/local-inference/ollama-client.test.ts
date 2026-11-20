import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OllamaClient, OLLAMA_BASE_URL } from "@/lib/local-inference/ollama-client";
import { OllamaError } from "@/lib/local-inference/ollama-errors";

describe("OllamaClient", () => {
  let client: OllamaClient;

  beforeEach(() => {
    client = new OllamaClient();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("Endpoint and Security Isolation", () => {
    it("strictly targets http://127.0.0.1:11434 and enforces credentials omit", async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ version: "0.40.16" }), { status: 200 })
      );

      await client.checkConnection();

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe(`${OLLAMA_BASE_URL}/api/version`);
      expect(init?.credentials).toBe("omit");
      expect(init?.redirect).toBe("error");
      expect(init?.mode).toBe("cors");

      // Verify no Auth headers are leaked
      const headers = new Headers(init?.headers);
      expect(headers.has("Authorization")).toBe(false);
      expect(headers.has("Cookie")).toBe(false);
    });
  });

  describe("checkConnection", () => {
    it("returns connected state with version on 200 OK", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ version: "0.4.0" }), { status: 200 })
      );

      const res = await client.checkConnection();
      expect(res.state).toBe("connected");
      expect(res.version).toBe("0.4.0");
    });

    it("returns cors_blocked when fetch throws TypeError (Failed to fetch)", async () => {
      vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
        new TypeError("Failed to fetch")
      );

      const res = await client.checkConnection();
      expect(res.state).toBe("cors_blocked");
      expect(res.error).toContain("OLLAMA_ORIGINS");
    });

    it("returns disconnected when Ollama returns HTTP 500", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response("Internal Server Error", { status: 500, statusText: "Internal Server Error" })
      );

      const res = await client.checkConnection();
      expect(res.state).toBe("disconnected");
      expect(res.error).toContain("HTTP 500");
    });
  });

  describe("listModels", () => {
    it("returns models from /api/tags", async () => {
      const mockModels = [
        {
          name: "llama3.2:3b",
          model: "llama3.2:3b",
          modified_at: "2026-01-01T00:00:00Z",
          size: 2019393189,
          digest: "abc123",
          details: {
            family: "llama",
            parameter_size: "3.2B",
            quantization_level: "Q4_K_M",
          },
        },
      ];

      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify({ models: mockModels }), { status: 200 })
      );

      const models = await client.listModels();
      expect(models).toHaveLength(1);
      expect(models[0].name).toBe("llama3.2:3b");
      expect(models[0].details?.parameter_size).toBe("3.2B");
    });

    it("throws OllamaError when /api/tags fails", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response("Bad Gateway", { status: 502 })
      );

      await expect(client.listModels()).rejects.toThrow(OllamaError);
    });
  });

  describe("showModel", () => {
    it("returns model details on /api/show", async () => {
      const mockShow = {
        parameters: "temperature 0.7",
        details: {
          family: "llama",
          parameter_size: "3.2B",
        },
      };

      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(JSON.stringify(mockShow), { status: 200 })
      );

      const info = await client.showModel("llama3.2:3b");
      expect(info?.details?.family).toBe("llama");
    });

    it("returns null if model is 404 not found", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response("Not Found", { status: 404 })
      );

      const info = await client.showModel("non-existent:latest");
      expect(info).toBeNull();
    });
  });

  describe("chatStream", () => {
    it("parses text, thinking, and statistics correctly", async () => {
      const encoder = new TextEncoder();
      const chunks = [
        JSON.stringify({
          model: "qwen3:0.6b",
          created_at: "2026-01-01T00:00:00Z",
          message: { role: "assistant", content: "", thinking: "Thinking..." },
          done: false,
        }) + "\n",
        JSON.stringify({
          model: "qwen3:0.6b",
          created_at: "2026-01-01T00:00:00Z",
          message: { role: "assistant", content: "Hello world!" },
          done: false,
        }) + "\n",
        JSON.stringify({
          model: "qwen3:0.6b",
          created_at: "2026-01-01T00:00:00Z",
          message: { role: "assistant", content: "" },
          done: true,
          total_duration: 2_000_000_000, // 2s
          load_duration: 100_000_000,     // 100ms
          prompt_eval_count: 10,
          prompt_eval_duration: 500_000_000,
          eval_count: 50,
          eval_duration: 1_000_000_000,   // 1s -> 50 tok / 1s = 50.0 tok/s
        }) + "\n",
      ];

      const stream = new ReadableStream({
        start(controller) {
          for (const c of chunks) {
            controller.enqueue(encoder.encode(c));
          }
          controller.close();
        },
      });

      vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
        new Response(stream, { status: 200 })
      );

      const deltas: string[] = [];
      const thinkingDeltas: string[] = [];
      let finalStats: any = null;

      await client.chatStream(
        {
          model: "qwen3:0.6b",
          messages: [{ role: "user", content: "Hi" }],
        },
        {
          onDelta: (d) => deltas.push(d),
          onThinkingDelta: (td) => thinkingDeltas.push(td),
          onStats: (s) => {
            finalStats = s;
          },
        }
      );

      expect(deltas.join("")).toBe("Hello world!");
      expect(thinkingDeltas.join("")).toBe("Thinking...");
      expect(finalStats).toEqual({
        totalDurationMs: 2000,
        loadDurationMs: 100,
        promptEvalCount: 10,
        promptEvalDurationMs: 500,
        evalCount: 50,
        evalDurationMs: 1000,
        tokensPerSecond: 50,
      });
    });
  });
});
