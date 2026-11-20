import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as lnaPermissions from "@/lib/local-inference/lna-permissions";
import { OllamaClient, OLLAMA_BASE_URL } from "@/lib/local-inference/ollama-client";
import { OllamaError } from "@/lib/local-inference/ollama-errors";

describe("OllamaClient", () => {
  let client: OllamaClient;

  beforeEach(() => {
    client = new OllamaClient();
    vi.restoreAllMocks();
    vi.spyOn(lnaPermissions, "isSecureLoopbackContext").mockReturnValue(true);
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
      state: "granted",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("Endpoint, Security and LNA Declaration", () => {
    it("strictly targets http://127.0.0.1:11434 with targetAddressSpace loopback and credentials omit", async () => {
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
      expect((init as any)?.targetAddressSpace).toBe("loopback");

      // Verify no Auth headers or cookies are leaked
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

    it("returns insecure_context when origin is not secure HTTPS or localhost", async () => {
      vi.spyOn(lnaPermissions, "isSecureLoopbackContext").mockReturnValue(false);

      const res = await client.checkConnection();
      expect(res.state).toBe("insecure_context");
      expect(res.error).toContain("secure HTTPS origin");
    });

    it("returns loopback_permission_denied when browser LNA permission is denied", async () => {
      vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
        state: "denied",
      });

      const res = await client.checkConnection();
      expect(res.state).toBe("loopback_permission_denied");
      expect(res.error).toContain("permission was denied");
    });

    it("returns connection_failed with neutral explanation when fetch throws opaque TypeError (Failed to fetch)", async () => {
      vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
        new TypeError("Failed to fetch")
      );

      const res = await client.checkConnection();
      // Opaque browser errors must not assume CORS as the only cause
      expect(res.state).toBe("connection_failed");
      expect(res.error).toContain("Ollama daemon is running");
      expect(res.error).toContain("OLLAMA_ORIGINS");
      expect(res.error).toContain("local network permissions");
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
    it("delivers content deltas and statistics while safely ignoring thinking deltas", async () => {
      const encoder = new TextEncoder();
      const chunks = [
        JSON.stringify({
          model: "qwen3:0.6b",
          created_at: "2026-01-01T00:00:00Z",
          message: { role: "assistant", content: "", thinking: "Thinking internal tokens..." },
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
      let finalStats: any = null;

      await client.chatStream(
        {
          model: "qwen3:0.6b",
          messages: [{ role: "user", content: "Hi" }],
        },
        {
          onDelta: (d) => deltas.push(d),
          onStats: (s) => {
            finalStats = s;
          },
        }
      );

      expect(deltas.join("")).toBe("Hello world!");
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
