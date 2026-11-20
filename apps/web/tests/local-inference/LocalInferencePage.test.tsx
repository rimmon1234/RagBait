import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LocalInferencePage from "@/app/(protected)/local-inference/page";
import * as lnaPermissions from "@/lib/local-inference/lna-permissions";
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

    vi.spyOn(lnaPermissions, "isSecureLoopbackContext").mockReturnValue(true);
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
      state: "granted",
    });

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

  it("renders connected status and lists available models when permission is 'granted'", async () => {
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

    // Wait for connection banner to show connected automatically on page load
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

  it("requires explicit user click before connecting when LNA permission is 'prompt'", async () => {
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
      state: "prompt",
    });

    const checkSpy = vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue([]);

    render(<LocalInferencePage />);

    // Must show permission required banner and NOT initiate checkConnection on page load
    await waitFor(() => {
      expect(
        screen.getByText("Browser Local Network Permission Required")
      ).toBeInTheDocument();
    });

    expect(checkSpy).not.toHaveBeenCalled();

    // User gesture click on Connect button
    const connectBtn = screen.getByRole("button", { name: /Connect to Local Ollama/i });
    fireEvent.click(connectBtn);

    // Now checkConnection is triggered
    await waitFor(() => {
      expect(checkSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("requires explicit user click and fires NO fetch on page load when permission is 'unsupported'", async () => {
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
      state: "unsupported",
    });

    const checkSpy = vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue([]);

    render(<LocalInferencePage />);

    // Per Chrome LNA guidance: unsupported must be treated as prompt:
    // NO automatic connection probe on page load!
    await waitFor(() => {
      expect(
        screen.getByText("Browser Local Network Permission Required")
      ).toBeInTheDocument();
    });

    expect(checkSpy).not.toHaveBeenCalled();

    // Clicking Connect button triggers the probe
    const connectBtn = screen.getByRole("button", { name: /Connect to Local Ollama/i });
    fireEvent.click(connectBtn);

    await waitFor(() => {
      expect(checkSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("handles permission query exceptions cleanly as unsupported/prompt without unhandled rejections", async () => {
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockRejectedValue(
      new Error("Permissions API descriptor not recognized")
    );

    const checkSpy = vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue([]);

    render(<LocalInferencePage />);

    // When query throws: caught safely, treated as prompt/unsupported, NO fetch on page load!
    await waitFor(() => {
      expect(
        screen.getByText("Browser Local Network Permission Required")
      ).toBeInTheDocument();
    });

    expect(checkSpy).not.toHaveBeenCalled();

    const connectBtn = screen.getByRole("button", { name: /Connect to Local Ollama/i });
    fireEvent.click(connectBtn);

    await waitFor(() => {
      expect(checkSpy).toHaveBeenCalledTimes(1);
    });
  });

  it("displays remediation banner and prevents probing when LNA permission is 'denied'", async () => {
    vi.spyOn(lnaPermissions, "queryLoopbackPermission").mockResolvedValue({
      state: "denied",
    });

    const checkSpy = vi.spyOn(ollamaClient, "checkConnection");

    render(<LocalInferencePage />);

    await waitFor(() => {
      expect(
        screen.getByText("Loopback Access Blocked by Browser")
      ).toBeInTheDocument();
      expect(screen.getByText(/Site Settings/i)).toBeInTheDocument();
    });

    // Probing must be completely prevented
    expect(checkSpy).not.toHaveBeenCalled();
  });

  it("renders neutral connection failed banner when connection fails", async () => {
    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValueOnce({
      state: "connection_failed",
      error: "Opaque fetch failure",
    });

    render(<LocalInferencePage />);

    await waitFor(() => {
      expect(screen.getByText("Cannot Connect to Ollama")).toBeInTheDocument();
      expect(screen.getByText(/OLLAMA_ORIGINS/i)).toBeInTheDocument();
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

  it("handles prompt submission and ensures thinking fields NEVER appear in rendered UI", async () => {
    const mockModels: OllamaTagModel[] = [
      {
        name: "qwen3:0.6b",
        model: "qwen3:0.6b",
        modified_at: "2026-01-01T00:00:00Z",
        size: 520000000,
        digest: "abc",
      },
    ];

    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue(mockModels);

    const mockStats: OllamaUsageStats = {
      totalDurationMs: 1200,
      loadDurationMs: 30,
      promptEvalCount: 15,
      promptEvalDurationMs: 250,
      evalCount: 30,
      evalDurationMs: 920,
      tokensPerSecond: 32.6,
    };

    // Even if an internal model had thinking tokens, chatStream only passes content deltas
    vi.spyOn(ollamaClient, "chatStream").mockImplementation(
      async (_req, callbacks) => {
        callbacks.onDelta?.("Visible assistant response.");
        callbacks.onStats?.(mockStats);
      }
    );

    render(<LocalInferencePage />);

    const textarea = await screen.findByPlaceholderText(/Ask qwen3:0.6b/i);
    fireEvent.change(textarea, { target: { value: "Explain gravity" } });

    const sendBtn = screen.getByRole("button", { name: /send/i });
    fireEvent.click(sendBtn);

    // Verify user prompt and assistant response appear
    await waitFor(() => {
      expect(screen.getByText("Explain gravity")).toBeInTheDocument();
      expect(
        screen.getByText("Visible assistant response.")
      ).toBeInTheDocument();
    });

    // REGRESSION TEST: Thinking/reasoning process must NEVER appear in the rendered document
    expect(screen.queryByText(/Reasoning Process/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();

    // Verify usage telemetry
    expect(screen.getByText("32.6")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  it("displays heavy model warning badge and banner for models requiring substantial memory", async () => {
    const mockModels: OllamaTagModel[] = [
      {
        name: "qwen2.5-coder:14b",
        model: "qwen2.5-coder:14b",
        modified_at: "2026-01-01T00:00:00Z",
        digest: "xyz",
        size: 9_000_000_000,
        details: { parameter_size: "14B" },
      },
    ];

    vi.spyOn(ollamaClient, "checkConnection").mockResolvedValue({
      state: "connected",
      version: "0.40.16",
    });
    vi.spyOn(ollamaClient, "listModels").mockResolvedValue(mockModels);

    render(<LocalInferencePage />);

    // Wait for connection
    await waitFor(() => {
      expect(screen.getByText("Ollama Connected")).toBeInTheDocument();
    });

    // Check option badge
    const select = screen.getByLabelText("Local Model:") as HTMLSelectElement;
    expect(select.options[0].text).toContain("⚠️ Heavy");
    expect(select.options[0].text).toContain("9.0 GB");

    // Check Large Model Notice banner
    expect(screen.getByText(/Large Model Notice/i)).toBeInTheDocument();
    expect(
      screen.getByText(/requires substantial VRAM or free system RAM/i)
    ).toBeInTheDocument();
  });
});
