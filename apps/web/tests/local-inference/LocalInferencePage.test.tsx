import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LocalInferencePage from "@/app/(protected)/local-inference/page";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import type { OllamaTagModel, OllamaUsageStats } from "@/lib/local-inference/ollama-types";

// Mock Supabase client
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: {
          session: {
            access_token: "mock-token",
            user: { id: "user-123" },
          },
        },
      }),
    },
  }),
}));

// Mock Next.js router
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}));

// Mock scrollIntoView
window.HTMLElement.prototype.scrollIntoView = vi.fn();

describe("LocalInferencePage Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default global fetch mock for /api/v1/me
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      if (String(url).includes("/api/v1/me")) {
        return new Response(
          JSON.stringify({
            id: "user-123",
            display_name: "Test Student",
            role: "student",
            approval_status: "approved",
          }),
          { status: 200 }
        );
      }
      return new Response("Not found", { status: 404 });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders connected status and lists available models", async () => {
    const mockModels: OllamaTagModel[] = [
      {
        name: "llama3.2:3b",
        model: "llama3.2:3b",
        modified_at: "2026-01-01T00:00:00Z",
        size: 2000000000,
        digest: "abc",
        details: { parameter_size: "3.2B", family: "llama" },
      },
      {
        name: "qwen2.5-coder:7b",
        model: "qwen2.5-coder:7b",
        modified_at: "2026-01-01T00:00:00Z",
        size: 4000000000,
        digest: "def",
        details: { parameter_size: "7.6B", family: "qwen2" },
      },
    ];

    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue(mockModels);

    render(<LocalInferencePage />);

    // Wait for connection banner to show connected
    await waitFor(() => {
      expect(screen.getByText("Ollama Connected")).toBeInTheDocument();
      expect(screen.getByText(/v0.40.16/)).toBeInTheDocument();
    });

    // Check model selector options
    const select = screen.getByLabelText("Local Model:") as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.options).toHaveLength(2);
    expect(select.options[0].text).toContain("llama3.2:3b");
    expect(select.options[1].text).toContain("qwen2.5-coder:7b");
  });

  it("renders CORS warning banner when connection state is cors_blocked", async () => {
    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValueOnce({
      state: "cors_blocked",
      error: "Unable to reach http://127.0.0.1:11434. Ensure OLLAMA_ORIGINS is configured.",
    });

    render(<LocalInferencePage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Blocked by CORS \(`OLLAMA_ORIGINS`\)/i)
      ).toBeInTheDocument();
    });
  });

  it("renders offline banner when Ollama is disconnected", async () => {
    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValueOnce({
      state: "disconnected",
      error: "Connection refused",
    });

    render(<LocalInferencePage />);

    await waitFor(() => {
      expect(screen.getByText("Ollama Daemon Offline")).toBeInTheDocument();
    });
  });

  it("handles prompt submission and streams response tokens", async () => {
    const mockModels: OllamaTagModel[] = [
      {
        name: "llama3.2:3b",
        model: "llama3.2:3b",
        modified_at: "2026-01-01T00:00:00Z",
        size: 2000000000,
        digest: "abc",
      },
    ];

    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue(mockModels);

    const mockStats: OllamaUsageStats = {
      totalDurationMs: 1500,
      loadDurationMs: 50,
      promptEvalCount: 12,
      promptEvalDurationMs: 300,
      evalCount: 45,
      evalDurationMs: 1150,
      tokensPerSecond: 39.1,
    };

    vi.spyOn(ollamaClient, "chatStream").mockImplementation(
      async (_req, callbacks) => {
        callbacks.onDelta?.("Hello! ");
        callbacks.onDelta?.("I am running locally.");
        callbacks.onStats?.(mockStats);
      }
    );

    render(<LocalInferencePage />);

    const textarea = await screen.findByPlaceholderText(/Ask llama3.2:3b/i);
    fireEvent.change(textarea, { target: { value: "Tell me a fun fact" } });

    const sendBtn = screen.getByRole("button", { name: /send/i });
    fireEvent.click(sendBtn);

    // Verify user prompt and assistant response appear
    await waitFor(() => {
      expect(screen.getByText("Tell me a fun fact")).toBeInTheDocument();
      expect(
        screen.getByText("Hello! I am running locally.")
      ).toBeInTheDocument();
    });

    // Verify usage statistics card updated
    expect(screen.getByText("39.1")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument();
  });
});
