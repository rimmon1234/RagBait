import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { useChatSession } from "@/lib/chat/useChatSession";
import * as chatClient from "@/lib/chat-client";
import * as streamingClient from "@/lib/chat/streaming-client";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import type { ChatConversation, ChatMessage, ChatStreamEvent } from "@/types/chat";

vi.mock("@/lib/chat-client", async (importOriginal) => {
  const actual = await importOriginal<typeof chatClient>();
  return {
    ...actual,
    getConversationMessages: vi.fn(),
    prepareLocalTurn: vi.fn(),
    finalizeLocalTurn: vi.fn(),
    cancelGeneration: vi.fn(),
  };
});

vi.mock("@/lib/chat/streaming-client", async (importOriginal) => {
  const actual = await importOriginal<typeof streamingClient>();
  return {
    ...actual,
    streamConversationMessage: vi.fn(),
    abortAndCancelGeneration: vi.fn(),
  };
});

vi.mock("@/lib/local-inference/ollama-client", () => ({
  ollamaClient: {
    chatStream: vi.fn(),
    checkConnection: vi.fn(),
    listModels: vi.fn(),
  },
}));

const baseConversation: ChatConversation = {
  id: "conv-1",
  user_id: "user-1",
  subject_id: "sub-1",
  subject_name: "Operating Systems",
  subject_slug: "os",
  title: "Virtual Memory",
  status: "active",
  message_count: 0,
  last_message_at: null,
  archived_at: null,
  created_at: "2026-03-01T10:00:00Z",
  updated_at: "2026-03-01T10:00:00Z",
};

const emptyInitialMessages: ChatMessage[] = [];

async function* yieldEvents(events: ChatStreamEvent[]): AsyncGenerator<ChatStreamEvent> {
  for (const ev of events) {
    yield ev;
  }
}

describe("useChatSession hook", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("handles queue waiting, provisional text deltas, content_replaced replacement, and completed validation", async () => {
    const onUpdated = vi.fn();

    vi.mocked(streamingClient.streamConversationMessage).mockReturnValueOnce(
      yieldEvents([
        {
          id: "ev-1",
          event: "admission_waiting",
          data: {
            message_id: "asst-1",
            conversation_id: "conv-1",
            queue_position: 2,
            estimated_wait_seconds: 5,
            status: "queued",
          },
        },
        {
          id: "ev-2",
          event: "generation_started",
          data: {
            message_id: "asst-1",
            user_message_id: "user-msg-1",
            conversation_id: "conv-1",
            generation_request_id: "req-1",
            model_id: "openrouter/free-model",
            provider_id: "openrouter",
            resolved_model_id: "openrouter/free-model",
            streaming: true,
            started_at: "2026-03-01T10:00:01Z",
          },
        },
        {
          id: "ev-3",
          event: "sources_identified",
          data: {
            message_id: "asst-1",
            conversation_id: "conv-1",
            sources: [
              {
                citation_key: "S1",
                document_id: "doc-1",
                document_title: "OS Textbook.pdf",
                page_number_start: 14,
                page_number_end: 15,
                relevance_score: 0.91,
              },
            ],
            omitted_count: 0,
            retrieval_candidate_count: 1,
          },
        },
        {
          id: "ev-4",
          event: "text_delta",
          data: { message_id: "asst-1", delta: "Provisional [S99] answer." },
        },
        {
          id: "ev-5",
          event: "content_replaced",
          data: {
            message_id: "asst-1",
            content: "Validated paging explanation [S1].",
            reason: "citation_validation_repair",
          },
        },
        {
          id: "ev-6",
          event: "generation_completed",
          data: {
            message_id: "asst-1",
            user_message_id: "user-msg-1",
            conversation_id: "conv-1",
            content: "Validated paging explanation [S1].",
            finish_reason: "stop",
            usage: { completion_tokens: 12 },
            citation_validation_status: "repaired",
            valid_citations: ["S1"],
            repair_attempted: true,
            sources: [],
            user_message: {
              id: "user-msg-1",
              conversation_id: "conv-1",
              sequence_number: 1,
              role: "user",
              content: "Explain paging",
              status: "completed",
              created_at: "2026-03-01T10:00:00Z",
              updated_at: "2026-03-01T10:00:00Z",
            },
            assistant_message: {
              id: "asst-1",
              conversation_id: "conv-1",
              sequence_number: 2,
              role: "assistant",
              content: "Validated paging explanation [S1].",
              status: "completed",
              model_id: "openrouter/free-model",
              created_at: "2026-03-01T10:00:05Z",
              updated_at: "2026-03-01T10:00:05Z",
            },
            telemetry: {},
          },
        },
      ])
    );

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
        onConversationUpdated: onUpdated,
      })
    );

    await act(async () => {
      await result.current.submitCloudMessage("Explain paging");
    });

    expect(result.current.phase).toBe("completed");
    expect(result.current.isGenerationActive).toBe(false);
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].content).toBe("Validated paging explanation [S1].");
    expect(result.current.messages[1].content).not.toContain("[S99]");
    expect(result.current.wasContentReplaced("asst-1")).toBe(true);
    expect(result.current.isProvisionalMessage("asst-1")).toBe(false);
    expect(result.current.identifiedSourcesForMessage("asst-1")).toHaveLength(1);
    expect(onUpdated).toHaveBeenCalledTimes(1);
  });

  it("deduplicates replayed event IDs and supports idempotent retry reusing same idempotency_key", async () => {
    vi.mocked(streamingClient.streamConversationMessage)
      .mockReturnValueOnce(
        yieldEvents([
          {
            id: "dup-1",
            event: "generation_started",
            data: {
              message_id: "asst-r",
              user_message_id: "usr-r",
              conversation_id: "conv-1",
              generation_request_id: "req-r",
              model_id: "openrouter/m",
              provider_id: "openrouter",
              resolved_model_id: "openrouter/m",
              streaming: true,
              started_at: "2026-03-01T10:00:00Z",
            },
          },
          {
            id: "dup-2",
            event: "generation_failed",
            data: {
              message_id: "asst-r",
              code: "UPSTREAM_TIMEOUT",
              message: "Upstream timeout",
              retryable: true,
            },
          },
        ])
      )
      .mockReturnValueOnce(
        yieldEvents([
          {
            id: "rep-1",
            event: "text_delta",
            data: { message_id: "asst-r", delta: "Hello" },
          },
          {
            id: "rep-1",
            event: "text_delta",
            data: { message_id: "asst-r", delta: "Hello" },
          },
          {
            id: "rep-2",
            event: "generation_completed",
            data: {
              message_id: "asst-r",
              user_message_id: "usr-r",
              conversation_id: "conv-1",
              content: "Hello",
              finish_reason: "stop",
              usage: {},
              citation_validation_status: "no_citations",
              valid_citations: [],
              repair_attempted: false,
              sources: [],
              user_message: {
                id: "usr-r",
                conversation_id: "conv-1",
                sequence_number: 1,
                role: "user",
                content: "Retry question",
                status: "completed",
                created_at: "2026-03-01T10:00:00Z",
                updated_at: "2026-03-01T10:00:00Z",
              },
              assistant_message: {
                id: "asst-r",
                conversation_id: "conv-1",
                sequence_number: 2,
                role: "assistant",
                content: "Hello",
                status: "completed",
                created_at: "2026-03-01T10:00:03Z",
                updated_at: "2026-03-01T10:00:03Z",
              },
              telemetry: {},
              replayed: true,
            },
          },
        ])
      );

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
      })
    );

    await act(async () => {
      await result.current.submitCloudMessage("Retry question");
    });

    expect(result.current.phase).toBe("failed");
    expect(result.current.retryPayload).not.toBeNull();
    const firstKey = vi.mocked(streamingClient.streamConversationMessage).mock.calls[0][1].idempotency_key;

    await act(async () => {
      await result.current.retryFailedSubmission();
    });

    const secondKey = vi.mocked(streamingClient.streamConversationMessage).mock.calls[1][1].idempotency_key;
    expect(secondKey).toBe(firstKey);
    expect(result.current.phase).toBe("completed");
    expect(result.current.messages.find((m) => m.id === "asst-r")?.content).toBe("Hello");
  });

  it("reconciles server completion on unexpected SSE EOF without terminal event", async () => {
    vi.mocked(streamingClient.streamConversationMessage).mockReturnValueOnce(
      yieldEvents([
        {
          id: "eof-1",
          event: "generation_started",
          data: {
            message_id: "asst-eof",
            user_message_id: "usr-eof",
            conversation_id: "conv-1",
            generation_request_id: "req-eof",
            model_id: "m",
            provider_id: "openrouter",
            resolved_model_id: "m",
            streaming: true,
            started_at: "2026-03-01T10:00:00Z",
          },
        },
        {
          id: "eof-2",
          event: "text_delta",
          data: { message_id: "asst-eof", delta: "Partial " },
        },
      ])
    );

    vi.mocked(chatClient.getConversationMessages).mockResolvedValueOnce({
      items: [
        {
          id: "usr-eof",
          conversation_id: "conv-1",
          sequence_number: 1,
          role: "user",
          content: "EOF prompt",
          status: "completed",
          created_at: "2026-03-01T10:00:00Z",
          updated_at: "2026-03-01T10:00:00Z",
        },
        {
          id: "asst-eof",
          conversation_id: "conv-1",
          sequence_number: 2,
          role: "assistant",
          content: "Final server content [S1].",
          status: "completed",
          created_at: "2026-03-01T10:00:04Z",
          updated_at: "2026-03-01T10:00:04Z",
        },
      ],
      has_more: false,
      next_cursor: null,
    });

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
      })
    );

    await act(async () => {
      await result.current.submitCloudMessage("EOF prompt");
    });

    expect(chatClient.getConversationMessages).toHaveBeenCalledWith("conv-1", { limit: 20 });
    expect(result.current.phase).toBe("completed");
    expect(result.current.messages[1].content).toBe("Final server content [S1].");
  });

  it("handles 409 cancel race by reconciling completed server state instead of falsely cancelling", async () => {
    let releaseStream: (() => void) | null = null;
    vi.mocked(streamingClient.streamConversationMessage).mockImplementationOnce(async function* () {
      yield {
        id: "c-1",
        event: "generation_started",
        data: {
          message_id: "asst-race",
          user_message_id: "usr-race",
          conversation_id: "conv-1",
          generation_request_id: "req-race",
          model_id: "m",
          provider_id: "openrouter",
          resolved_model_id: "m",
          streaming: true,
          started_at: "2026-03-01T10:00:00Z",
        },
      };
      await new Promise<void>((resolve) => {
        releaseStream = resolve;
      });
    });

    vi.mocked(streamingClient.abortAndCancelGeneration).mockRejectedValueOnce(
      new ApiError("GENERATION_ALREADY_FINISHED", "Already completed", 409)
    );
    vi.mocked(chatClient.getConversationMessages).mockResolvedValueOnce({
      items: [
        {
          id: "usr-race",
          conversation_id: "conv-1",
          sequence_number: 1,
          role: "user",
          content: "Race question",
          status: "completed",
          created_at: "2026-03-01T10:00:00Z",
          updated_at: "2026-03-01T10:00:00Z",
        },
        {
          id: "asst-race",
          conversation_id: "conv-1",
          sequence_number: 2,
          role: "assistant",
          content: "Finished right before cancel.",
          status: "completed",
          created_at: "2026-03-01T10:00:02Z",
          updated_at: "2026-03-01T10:00:02Z",
        },
      ],
      has_more: false,
      next_cursor: null,
    });

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
      })
    );

    let submitPromise!: Promise<void>;
    await act(async () => {
      submitPromise = result.current.submitCloudMessage("Race question");
      await Promise.resolve();
    });

    await act(async () => {
      await result.current.cancelActiveGeneration();
      releaseStream?.();
      await submitPromise;
    });

    expect(result.current.phase).toBe("completed");
    expect(result.current.messages[1].content).toBe("Finished right before cancel.");
  });

  it("executes isolated Private Local Ollama flow (prepare -> loopback Ollama -> finalize) with zero cloud fallback", async () => {
    vi.mocked(chatClient.prepareLocalTurn).mockResolvedValueOnce({
      message_id: "asst-loc-1",
      user_message_id: "usr-loc-1",
      prepared_request_id: "prep-1",
      conversation_id: "conv-1",
      model_id: "llama3.2",
      prompt: "Prompt text",
      messages: [{ role: "user", content: "Local question" }],
      sources: [
        {
          citation_key: "S1",
          document_id: "doc-1",
          document_title: "LocalNotes.pdf",
          page_number_start: 3,
          page_number_end: 3,
          relevance_score: 0.88,
        },
      ],
      accounting: {
        model_effective_ceiling: 8192,
        reserved_output_tokens: 1024,
        system_prompt_tokens: 200,
        guardrail_overhead_tokens: 100,
        safety_margin_tokens: 100,
        available_working_budget: 6768,
        current_question_tokens: 10,
        evidence_budget_ceiling: 4000,
        evidence_token_count: 500,
        summary_token_count: 0,
        recent_history_token_count: 0,
        total_input_token_count: 810,
        total_reserved_tokens: 1834,
        utilization_ratio: 0.22,
      },
      evidence_omitted_count: 0,
      expires_at: "2026-03-01T10:10:00Z",
    });

    vi.mocked(ollamaClient.chatStream).mockImplementationOnce(async (_req, callbacks) => {
      callbacks.onDelta?.("Local answer ");
      callbacks.onDelta?.("[S1].");
      callbacks.onStats?.({
        promptEvalCount: 10,
        evalCount: 5,
        totalDurationMs: 120,
        loadDurationMs: 10,
        promptEvalDurationMs: 40,
        evalDurationMs: 70,
        tokensPerSecond: 71.4,
      });
    });

    vi.mocked(chatClient.finalizeLocalTurn).mockResolvedValueOnce({
      message_id: "asst-loc-1",
      status: "completed",
      citations_reconciled: 1,
      valid_citations: ["S1"],
      invalid_citations_stripped: [],
      citation_validation_status: "valid",
      sources: [],
    });

    vi.mocked(chatClient.getConversationMessages).mockResolvedValueOnce({
      items: [
        {
          id: "usr-loc-1",
          conversation_id: "conv-1",
          sequence_number: 1,
          role: "user",
          content: "Local question",
          status: "completed",
          created_at: "2026-03-01T10:00:00Z",
          updated_at: "2026-03-01T10:00:00Z",
        },
        {
          id: "asst-loc-1",
          conversation_id: "conv-1",
          sequence_number: 2,
          role: "assistant",
          content: "Local answer [S1].",
          status: "completed",
          model_id: "llama3.2",
          provider_id: "ollama",
          created_at: "2026-03-01T10:00:05Z",
          updated_at: "2026-03-01T10:00:05Z",
        },
      ],
      has_more: false,
      next_cursor: null,
    });

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
      })
    );

    await act(async () => {
      await result.current.submitPrivateLocalMessage("Local question", "llama3.2");
    });

    expect(streamingClient.streamConversationMessage).not.toHaveBeenCalled();
    expect(chatClient.prepareLocalTurn).toHaveBeenCalledTimes(1);
    expect(ollamaClient.chatStream).toHaveBeenCalledTimes(1);
    expect(chatClient.finalizeLocalTurn).toHaveBeenCalledTimes(1);
    expect(result.current.phase).toBe("completed");
    expect(result.current.messages[1].content).toBe("Local answer [S1].");
  });

  it("never falls back to cloud streaming when local Ollama daemon fails", async () => {
    vi.mocked(chatClient.prepareLocalTurn).mockResolvedValueOnce({
      message_id: "asst-loc-fail",
      user_message_id: "usr-loc-fail",
      prepared_request_id: "prep-fail",
      conversation_id: "conv-1",
      model_id: "llama3.2",
      prompt: "System",
      messages: [{ role: "user", content: "Offline test" }],
      sources: [],
      accounting: {
        model_effective_ceiling: 8192,
        reserved_output_tokens: 1024,
        system_prompt_tokens: 200,
        guardrail_overhead_tokens: 100,
        safety_margin_tokens: 100,
        available_working_budget: 6768,
        current_question_tokens: 10,
        evidence_budget_ceiling: 4000,
        evidence_token_count: 0,
        summary_token_count: 0,
        recent_history_token_count: 0,
        total_input_token_count: 310,
        total_reserved_tokens: 1334,
        utilization_ratio: 0.16,
      },
      evidence_omitted_count: 0,
      expires_at: "2026-03-01T10:10:00Z",
    });

    vi.mocked(ollamaClient.chatStream).mockRejectedValueOnce(new Error("Connection refused"));
    vi.mocked(chatClient.cancelGeneration).mockResolvedValueOnce({
      message_id: "asst-loc-fail",
      conversation_id: "conv-1",
      status: "cancelled",
      already_cancelled: false,
      retained_length: 0,
      reason: "local_error",
    });

    const { result } = renderHook(() =>
      useChatSession({
        conversationId: "conv-1",
        conversation: baseConversation,
        initialMessages: emptyInitialMessages,
        initialOlderCursor: null,
        initialHasMoreOlder: false,
      })
    );

    await act(async () => {
      await result.current.submitPrivateLocalMessage("Offline test", "llama3.2");
    });

    expect(streamingClient.streamConversationMessage).not.toHaveBeenCalled();
    expect(result.current.phase).toBe("failed");
    expect(result.current.generationError?.message).toContain("Connection refused");
  });
});
