import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownMessageRenderer } from "@/components/chat/MarkdownMessageRenderer";
import { ChatComposer, MAX_CHAT_INPUT_LENGTH } from "@/components/chat/ChatComposer";
import { ollamaClient } from "@/lib/local-inference/ollama-client";

vi.mock("@/lib/local-inference/ollama-client", () => ({
  ollamaClient: {
    checkConnection: vi.fn(),
    listModels: vi.fn(),
  },
}));

describe("MarkdownMessageRenderer", () => {
  it("neutralizes script/html tags and unsafe javascript: links safely", () => {
    const content = [
      'Hello <script>alert("xss")</script> world.',
      "Click [evil](javascript:alert(1)) or [docs](https://example.edu/os).",
    ].join("\n\n");

    const { container } = render(<MarkdownMessageRenderer content={content} />);

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>alert\("xss"\)<\/script>/)).toBeInTheDocument();
    // Unsafe javascript: link rendered as plain span, safe https link as real anchor
    expect(screen.queryByRole("link", { name: "evil" })).not.toBeInTheDocument();
    const safeLink = screen.getByRole("link", { name: "docs" });
    expect(safeLink).toHaveAttribute("href", "https://example.edu/os");
  });

  it("renders headings, lists, code blocks, tables, and clickable [Sn] citation badges", () => {
    const onCitationClick = vi.fn();
    const markdown = [
      "# Memory Management",
      "- Paging eliminates external fragmentation [S1].",
      "- Segmentation uses variable regions [S2].",
      "```c\nint page_size = 4096;\n```",
      "| Concept | Fixed? |\n| --- | --- |\n| Paging | Yes [S1] |",
    ].join("\n\n");

    render(
      <MarkdownMessageRenderer
        content={markdown}
        onCitationClick={onCitationClick}
      />
    );

    expect(screen.getByRole("heading", { name: "Memory Management" })).toBeInTheDocument();
    expect(screen.getByText("int page_size = 4096;")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();

    const s1Chips = screen.getAllByRole("button", { name: "Inspect source S1" });
    expect(s1Chips.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(s1Chips[0]);
    expect(onCitationClick).toHaveBeenCalledWith("S1");
  });

  it("renders interactive [W1] web citation chips distinct from [S1] note badges", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownMessageRenderer
        content="Notes define paging [S1], while recent kernels refine it [W1]."
        onCitationClick={onCitationClick}
      />
    );

    const w1Btn = screen.getByRole("button", { name: "Inspect web source W1" });
    fireEvent.click(w1Btn);
    expect(onCitationClick).toHaveBeenCalledWith("W1");
  });
});

describe("ChatComposer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("submits on Enter, keeps newline on Shift+Enter, and validates empty and >32768 inputs", () => {
    const onSend = vi.fn();
    render(
      <ChatComposer
        conversationStatus="active"
        subjectName="Operating Systems"
        canGenerate={true}
        inferenceMode="cloud"
        onInferenceModeChange={vi.fn()}
        selectedLocalModel=""
        onLocalModelChange={vi.fn()}
        isGenerationActive={false}
        phase="idle"
        onSend={onSend}
        onCancel={vi.fn()}
      />
    );

    const textarea = screen.getByLabelText("Study question input");

    // Empty submit disabled
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

    // Shift+Enter does NOT submit
    fireEvent.change(textarea, { target: { value: "Line 1" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    // Over max length shows error without truncating draft
    const oversized = "a".repeat(MAX_CHAT_INPUT_LENGTH + 5);
    fireEvent.change(textarea, { target: { value: oversized } });
    expect(screen.getByRole("alert")).toHaveTextContent("32,768");
    expect((textarea as HTMLTextAreaElement).value.length).toBe(MAX_CHAT_INPUT_LENGTH + 5);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

    // Valid prompt + Enter submits cleanly
    fireEvent.change(textarea, { target: { value: "Explain virtual memory." } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("Explain virtual memory.");
  });

  it("renders Private Local Ollama loopback controls and Cancel button when generating", async () => {
    vi.mocked(ollamaClient.checkConnection).mockResolvedValueOnce({
      state: "connected",
      version: "0.5.1",
    });
    vi.mocked(ollamaClient.listModels).mockResolvedValueOnce([
      {
        name: "llama3.2:latest",
        model: "llama3.2:latest",
        modified_at: "2026-03-01T00:00:00Z",
        size: 1000,
        digest: "abc",
      },
    ]);

    const onLocalModelChange = vi.fn();
    const onCancel = vi.fn();

    render(
      <ChatComposer
        conversationStatus="active"
        subjectName="Operating Systems"
        canGenerate={true}
        inferenceMode="private_local"
        onInferenceModeChange={vi.fn()}
        selectedLocalModel="llama3.2:latest"
        onLocalModelChange={onLocalModelChange}
        isGenerationActive={true}
        phase="generating"
        onSend={vi.fn()}
        onCancel={onCancel}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Loopback only \(no cloud fallback\)/i)).toBeInTheDocument();
      expect(screen.getByLabelText("Local Ollama model")).toBeInTheDocument();
    });

    const stopBtn = screen.getByRole("button", { name: "Stop generation" });
    fireEvent.click(stopBtn);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("toggles inline Private Local Ollama setup guide without navigating away from /chat", async () => {
    vi.mocked(ollamaClient.checkConnection).mockResolvedValueOnce({
      state: "disconnected",
    });

    render(
      <ChatComposer
        conversationStatus="active"
        subjectName="Operating Systems"
        canGenerate={true}
        inferenceMode="private_local"
        onInferenceModeChange={vi.fn()}
        selectedLocalModel=""
        onLocalModelChange={vi.fn()}
        isGenerationActive={false}
        phase="idle"
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    const setupBtn = await screen.findByRole("button", { name: /Setup Guide/i });
    expect(screen.queryByRole("link", { name: /Setup/i })).not.toBeInTheDocument();
    fireEvent.click(setupBtn);

    expect(screen.getByText(/Local Ollama & CORS Setup \(127\.0\.0\.1:11434\)/i)).toBeInTheDocument();
    expect(screen.getByText(/OLLAMA_ORIGINS=\*/i)).toBeInTheDocument();
  });

  it("allows toggling between Notes first and strict Notes only modes", () => {
    const onPolicyChange = vi.fn();
    render(
      <ChatComposer
        conversationStatus="active"
        subjectName="Operating Systems"
        canGenerate={true}
        inferenceMode="cloud"
        onInferenceModeChange={vi.fn()}
        answerPolicy="note_first_adaptive"
        onAnswerPolicyChange={onPolicyChange}
        selectedLocalModel=""
        onLocalModelChange={vi.fn()}
        isGenerationActive={false}
        phase="idle"
        onSend={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    expect(screen.getByText(/Prioritizes/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Notes only" }));
    expect(onPolicyChange).toHaveBeenCalledWith("notes_only");
  });
});
