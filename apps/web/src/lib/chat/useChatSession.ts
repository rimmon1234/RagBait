"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  cancelGeneration,
  finalizeLocalTurn,
  getConversationMessages,
  prepareLocalTurn,
} from "@/lib/chat-client";
import {
  abortAndCancelGeneration,
  streamConversationMessage,
} from "@/lib/chat/streaming-client";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import { isAbortError } from "@/lib/local-inference/ollama-errors";
import type {
  AnswerPolicy,
  ChatConversation,
  ChatMessage,
  GenerationPhase,
  InferenceExecutionMode,
  StreamSourceSummary,
} from "@/types/chat";

function createSubmissionIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `msg-${crypto.randomUUID()}`;
  }
  return `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface StreamErrorState {
  code: string;
  message: string;
  retryable: boolean;
}

export interface RetrySubmissionSnapshot {
  content: string;
  idempotencyKey: string;
  mode: InferenceExecutionMode;
  localModelId?: string;
  answerPolicy?: AnswerPolicy;
}

export interface QueueWaitInfo {
  position: number;
  estimatedWaitSeconds: number;
}

export interface UseChatSessionOptions {
  conversationId: string;
  conversation: ChatConversation | null;
  initialMessages: ChatMessage[];
  initialOlderCursor: string | null;
  initialHasMoreOlder: boolean;
  onConversationUpdated?: () => void;
  onContributionRequired?: (message?: string) => void;
  onAuthExpired?: () => void;
}

export interface UseChatSessionResult {
  messages: ChatMessage[];
  loadingOlder: boolean;
  hasMoreOlder: boolean;
  phase: GenerationPhase;
  activeAssistantMessageId: string | null;
  isGenerationActive: boolean;
  isProvisionalMessage: (messageId: string) => boolean;
  wasContentReplaced: (messageId: string) => boolean;
  identifiedSourcesForMessage: (messageId: string) => StreamSourceSummary[] | undefined;
  queueInfo: QueueWaitInfo | null;
  generationError: StreamErrorState | null;
  retryPayload: RetrySubmissionSnapshot | null;
  draftOverride: string | null;
  clearDraftOverride: () => void;
  loadOlderMessages: () => Promise<void>;
  submitCloudMessage: (
    content: string,
    existingIdempotencyKey?: string,
    answerPolicy?: AnswerPolicy
  ) => Promise<void>;
  submitPrivateLocalMessage: (
    content: string,
    localModelId: string,
    existingIdempotencyKey?: string,
    answerPolicy?: AnswerPolicy
  ) => Promise<void>;
  cancelActiveGeneration: () => Promise<void>;
  retryFailedSubmission: () => Promise<void>;
}

/**
 * Manages conversation message timeline, SSE generation lifecycle, idempotent retries,
 * server reconciliation after ambiguous disconnects/races, and isolated Private Local turns.
 */
export function useChatSession({
  conversationId,
  conversation,
  initialMessages,
  initialOlderCursor,
  initialHasMoreOlder,
  onConversationUpdated,
  onContributionRequired,
  onAuthExpired,
}: UseChatSessionOptions): UseChatSessionResult {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [olderCursor, setOlderCursor] = useState<string | null>(initialOlderCursor);
  const [hasMoreOlder, setHasMoreOlder] = useState(initialHasMoreOlder);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const [phase, setPhase] = useState<GenerationPhase>("idle");
  const [activeAssistantMessageId, setActiveAssistantMessageId] = useState<string | null>(null);
  const [queueInfo, setQueueInfo] = useState<QueueWaitInfo | null>(null);
  const [generationError, setGenerationError] = useState<StreamErrorState | null>(null);
  const [retryPayload, setRetryPayload] = useState<RetrySubmissionSnapshot | null>(null);
  const [draftOverride, setDraftOverride] = useState<string | null>(null);

  const [replacedMap, setReplacedMap] = useState<Record<string, boolean>>({});
  const [identifiedSourcesMap, setIdentifiedSourcesMap] = useState<
    Record<string, StreamSourceSummary[]>
  >({});

  const abortControllerRef = useRef<AbortController | null>(null);
  const activeModeRef = useRef<InferenceExecutionMode>("cloud");
  const activeConvRef = useRef<string>(conversationId);
  const activeServerMsgIdRef = useRef<string | null>(null);
  const userCancelledRef = useRef<boolean>(false);
  const phaseRef = useRef<GenerationPhase>("idle");

  phaseRef.current = phase;

  const resetActiveStreamState = useCallback(() => {
    setPhase("idle");
    setActiveAssistantMessageId(null);
    activeServerMsgIdRef.current = null;
    setQueueInfo(null);
    setGenerationError(null);
    setRetryPayload(null);
    setDraftOverride(null);
    userCancelledRef.current = false;
  }, []);

  // Sync loaded messages when switching conversations or reloading conversation data
  useEffect(() => {
    activeConvRef.current = conversationId;
    setMessages(initialMessages);
    setOlderCursor(initialOlderCursor);
    setHasMoreOlder(initialHasMoreOlder);
    setReplacedMap({});
    setIdentifiedSourcesMap({});
    resetActiveStreamState();
  }, [conversationId, initialMessages, initialOlderCursor, initialHasMoreOlder, resetActiveStreamState]);

  // Ensure unmount or conversation navigation cancels unmanaged active server streams safely
  useEffect(() => {
    const convForEffect = conversationId;
    return () => {
      const controller = abortControllerRef.current;
      const serverMsgId = activeServerMsgIdRef.current;
      const currentPhase = phaseRef.current;
      if (
        controller &&
        (currentPhase === "submitting" ||
          currentPhase === "admission_waiting" ||
          currentPhase === "generating")
      ) {
        if (activeModeRef.current === "cloud") {
          void abortAndCancelGeneration(controller, serverMsgId, convForEffect).catch(() => {});
        } else {
          controller.abort();
          if (serverMsgId) {
            void cancelGeneration(serverMsgId, convForEffect).catch(() => {});
          }
        }
      } else if (controller) {
        controller.abort();
      }
      abortControllerRef.current = null;
    };
  }, [conversationId]);

  const loadOlderMessages = useCallback(async () => {
    if (!conversationId || !olderCursor || loadingOlder) return;
    try {
      setLoadingOlder(true);
      const res = await getConversationMessages(conversationId, {
        cursor: olderCursor,
        direction: "before",
        limit: 50,
      });
      if (activeConvRef.current !== conversationId) return;
      setMessages((prev) => {
        const existingIds = new Set(prev.map((m) => m.id));
        const prepended = res.items.filter((m) => !existingIds.has(m.id));
        return [...prepended, ...prev];
      });
      setOlderCursor(res.next_cursor);
      setHasMoreOlder(res.has_more);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthExpired?.();
      }
    } finally {
      setLoadingOlder(false);
    }
  }, [conversationId, olderCursor, loadingOlder, onAuthExpired]);

  const reconcileWithServerMessages = useCallback(
    async (targetConversationId: string, fallbackMessageId: string | null) => {
      try {
        const res = await getConversationMessages(targetConversationId, { limit: 20 });
        if (activeConvRef.current !== targetConversationId) return null;
        setMessages((prev) => {
          const withoutTemps = prev.filter((m) => !m.id.startsWith("temp-"));
          const byId = new Map<string, ChatMessage>();
          for (const item of withoutTemps) {
            byId.set(item.id, item);
          }
          for (const item of res.items) {
            byId.set(item.id, item);
          }
          return Array.from(byId.values()).sort((a, b) => a.sequence_number - b.sequence_number);
        });

        const reconciled =
          (fallbackMessageId && res.items.find((m) => m.id === fallbackMessageId)) ||
          [...res.items].reverse().find((m) => m.role === "assistant") ||
          null;
        return reconciled;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onAuthExpired?.();
        }
        return null;
      }
    },
    [onAuthExpired]
  );

  const submitCloudMessage = useCallback(
    async (
      rawContent: string,
      existingIdempotencyKey?: string,
      answerPolicy: AnswerPolicy = "note_first_adaptive"
    ) => {
      const trimmed = rawContent.trim();
      if (!trimmed || !conversation || conversation.status !== "active") return;
      if (
        phaseRef.current === "submitting" ||
        phaseRef.current === "admission_waiting" ||
        phaseRef.current === "generating" ||
        phaseRef.current === "cancelling"
      ) {
        return;
      }

      const submissionKey = existingIdempotencyKey || createSubmissionIdempotencyKey();
      const tempUserId = `temp-user-${submissionKey}`;
      const tempAsstId = `temp-asst-${submissionKey}`;
      const nowIso = new Date().toISOString();

      let currentUserMsgId = tempUserId;
      let currentAsstMsgId = tempAsstId;
      let sawTerminalEvent = false;
      let lastAccumulatedText = "";
      let isReplayStream = false;
      const seenEventIds = new Set<string>();

      activeModeRef.current = "cloud";
      userCancelledRef.current = false;
      activeServerMsgIdRef.current = null;
      setGenerationError(null);
      setRetryPayload(null);
      setQueueInfo(null);
      setPhase("submitting");
      setActiveAssistantMessageId(tempAsstId);

      setMessages((prev) => {
        const cleaned = existingIdempotencyKey
          ? prev.filter(
              (m) =>
                m.id !== tempUserId &&
                m.id !== tempAsstId &&
                !(m.status === "failed" && m.role === "assistant" && m.id === activeAssistantMessageId)
            )
          : prev;

        const hasUserTurn = existingIdempotencyKey && cleaned.some((m) => m.id === tempUserId);
        const optimisticEntries: ChatMessage[] = [];
        if (!hasUserTurn) {
          optimisticEntries.push({
            id: tempUserId,
            conversation_id: conversationId,
            sequence_number: (cleaned[cleaned.length - 1]?.sequence_number || 0) + 1,
            role: "user",
            content: trimmed,
            status: "completed",
            created_at: nowIso,
            updated_at: nowIso,
          });
        }
        optimisticEntries.push({
          id: tempAsstId,
          conversation_id: conversationId,
          sequence_number:
            (cleaned[cleaned.length - 1]?.sequence_number || 0) + (hasUserTurn ? 1 : 2),
          role: "assistant",
          content: "",
          status: "pending",
          created_at: nowIso,
          updated_at: nowIso,
        });
        return [...cleaned, ...optimisticEntries];
      });

      const controller = new AbortController();
      abortControllerRef.current = controller;

      const replaceAssistantIdAndUpdate = (
        nextId: string,
        updater: (prev: ChatMessage) => ChatMessage
      ) => {
        const prevId = currentAsstMsgId;
        currentAsstMsgId = nextId;
        activeServerMsgIdRef.current = nextId;
        setActiveAssistantMessageId(nextId);
        setMessages((list) => {
          let found = false;
          const mapped = list.map((msg) => {
            if (msg.id === prevId || msg.id === nextId) {
              found = true;
              return updater({ ...msg, id: nextId });
            }
            return msg;
          });
          return found ? mapped : list;
        });
      };

      try {
        for await (const ev of streamConversationMessage(
          conversationId,
          {
            content: trimmed,
            idempotency_key: submissionKey,
            routing_mode: "auto",
            answer_policy: answerPolicy,
          },
          { signal: controller.signal }
        )) {
          if (activeConvRef.current !== conversationId) {
            return;
          }
          if (ev.id) {
            if (seenEventIds.has(ev.id)) {
              continue;
            }
            seenEventIds.add(ev.id);
          }

          switch (ev.event) {
            case "admission_waiting": {
              setPhase("admission_waiting");
              setQueueInfo({
                position: ev.data.queue_position,
                estimatedWaitSeconds: ev.data.estimated_wait_seconds,
              });
              replaceAssistantIdAndUpdate(ev.data.message_id, (m) => ({
                ...m,
                status: "pending",
              }));
              break;
            }

            case "generation_started": {
              isReplayStream = Boolean(ev.data.replayed);
              if (isReplayStream) {
                lastAccumulatedText = "";
              }
              setQueueInfo(null);
              if ((phaseRef.current as GenerationPhase) !== "cancelling") {
                setPhase("generating");
              }
              const realUserId = ev.data.user_message_id;
              if (realUserId && currentUserMsgId !== realUserId) {
                const oldUid = currentUserMsgId;
                currentUserMsgId = realUserId;
                setMessages((list) =>
                  list.map((m) => (m.id === oldUid ? { ...m, id: realUserId } : m))
                );
              }
              replaceAssistantIdAndUpdate(ev.data.message_id, (m) => ({
                ...m,
                status: "streaming",
                content: isReplayStream ? "" : m.content,
                model_id: ev.data.resolved_model_id || ev.data.model_id || m.model_id,
                provider_id: ev.data.provider_id || m.provider_id,
              }));
              break;
            }

            case "sources_identified": {
              const targetId = ev.data.message_id || currentAsstMsgId;
              setIdentifiedSourcesMap((prev) => ({
                ...prev,
                [targetId]: ev.data.sources,
              }));
              break;
            }

            case "text_delta": {
              const targetId = ev.data.message_id || currentAsstMsgId;
              if ((phaseRef.current as GenerationPhase) !== "cancelling") {
                setPhase("generating");
              }
              lastAccumulatedText = isReplayStream
                ? ev.data.delta
                : lastAccumulatedText + ev.data.delta;
              isReplayStream = false;
              const updatedContent = lastAccumulatedText;
              replaceAssistantIdAndUpdate(targetId, (m) => ({
                ...m,
                status: "streaming",
                content: updatedContent,
              }));
              break;
            }

            case "content_replaced": {
              const targetId = ev.data.message_id || currentAsstMsgId;
              lastAccumulatedText = ev.data.content;
              setReplacedMap((prev) => ({ ...prev, [targetId]: true }));
              replaceAssistantIdAndUpdate(targetId, (m) => ({
                ...m,
                content: ev.data.content,
              }));
              break;
            }

            case "generation_completed": {
              sawTerminalEvent = true;
              setQueueInfo(null);
              setPhase("completed");
              setRetryPayload(null);
              const targetId = ev.data.message_id;
              activeServerMsgIdRef.current = null;

              const finalUser = ev.data.user_message;
              const finalAssistant = ev.data.assistant_message;
              setMessages((list) => {
                const next = list.map((msg) => {
                  if (msg.id === currentUserMsgId || msg.id === finalUser.id) {
                    return finalUser;
                  }
                  if (msg.id === currentAsstMsgId || msg.id === targetId) {
                    return finalAssistant;
                  }
                  return msg;
                });
                return next;
              });
              setActiveAssistantMessageId(targetId);
              onConversationUpdated?.();
              break;
            }

            case "generation_failed": {
              sawTerminalEvent = true;
              setQueueInfo(null);
              setPhase("failed");
              activeServerMsgIdRef.current = null;
              setGenerationError({
                code: ev.data.code,
                message: ev.data.message,
                retryable: ev.data.retryable,
              });
              replaceAssistantIdAndUpdate(ev.data.message_id, (m) => ({
                ...m,
                status: "failed",
                error_message: ev.data.message,
              }));
              if (ev.data.retryable) {
                setRetryPayload({
                  content: trimmed,
                  idempotencyKey: submissionKey,
                  mode: "cloud",
                  answerPolicy,
                });
              }
              break;
            }

            case "generation_cancelled": {
              sawTerminalEvent = true;
              setQueueInfo(null);
              setPhase("cancelled");
              activeServerMsgIdRef.current = null;
              const partial = ev.data.partial_content ?? lastAccumulatedText;
              replaceAssistantIdAndUpdate(ev.data.message_id, (m) => ({
                ...m,
                status: "cancelled",
                content: partial,
                finish_reason: "cancelled",
              }));
              onConversationUpdated?.();
              break;
            }

            default:
              // Unknown SSE frame ignored safely per M4.6/M4.7 contract
              break;
          }
        }

        // Unexpected SSE EOF without terminal event -> reconcile server truth
        if (!sawTerminalEvent && activeConvRef.current === conversationId) {
          const reconciled = await reconcileWithServerMessages(
            conversationId,
            activeServerMsgIdRef.current
          );
          if (reconciled?.status === "completed") {
            setPhase("completed");
            setRetryPayload(null);
            onConversationUpdated?.();
          } else if (reconciled?.status === "cancelled" || userCancelledRef.current) {
            setPhase("cancelled");
            onConversationUpdated?.();
          } else {
            setPhase("failed");
            setGenerationError({
              code: "STREAM_INTERRUPTED",
              message:
                "Connection interrupted before completion was confirmed. Retry safely without duplicating your prompt.",
              retryable: true,
            });
            setRetryPayload({
              content: trimmed,
              idempotencyKey: submissionKey,
              mode: "cloud",
            });
          }
        }
      } catch (err) {
        if (activeConvRef.current !== conversationId) {
          return;
        }

        const isAbort = err instanceof Error && err.name === "AbortError";
        if (isAbort && userCancelledRef.current) {
          // User cancellation path handles terminal reconciliation cleanly
          return;
        }

        if (err instanceof ApiError) {
          if (err.status === 401) {
            setMessages((prev) =>
              prev.filter((m) => m.id !== tempUserId && m.id !== tempAsstId)
            );
            setPhase("idle");
            setDraftOverride(trimmed);
            onAuthExpired?.();
            return;
          }

          if (err.status === 403 && err.code === "CHAT_CONTRIBUTION_REQUIRED") {
            setMessages((prev) =>
              prev.filter((m) => m.id !== tempUserId && m.id !== tempAsstId)
            );
            setPhase("idle");
            setDraftOverride(trimmed);
            onContributionRequired?.(err.message);
            return;
          }

          setPhase("failed");
          setGenerationError({
            code: err.code,
            message: err.message,
            retryable: err.status >= 429 || err.status === 408,
          });
          setMessages((prev) =>
            prev.map((m) =>
              m.id === currentAsstMsgId
                ? { ...m, status: "failed", error_message: err.message }
                : m
            )
          );
          if (err.status >= 429 || err.status === 408 || err.status === 503) {
            setRetryPayload({
              content: trimmed,
              idempotencyKey: submissionKey,
              mode: "cloud",
            });
          } else if (!activeServerMsgIdRef.current) {
            setDraftOverride(trimmed);
          }
          return;
        }

        // Network interruption or unexpected EOF during stream read -> reconcile before allowing retry
        const reconciled = await reconcileWithServerMessages(
          conversationId,
          activeServerMsgIdRef.current
        );
        if (reconciled?.status === "completed") {
          setPhase("completed");
          setRetryPayload(null);
          onConversationUpdated?.();
          return;
        }
        if (reconciled?.status === "cancelled") {
          setPhase("cancelled");
          onConversationUpdated?.();
          return;
        }

        setPhase("failed");
        setGenerationError({
          code: "NETWORK_INTERRUPTED",
          message: "Stream interrupted due to a network issue. Retry safely with the same prompt.",
          retryable: true,
        });
        setMessages((prev) =>
          prev.map((m) =>
            m.id === currentAsstMsgId
              ? {
                  ...m,
                  status: "failed",
                  error_message: "Stream interrupted due to a network issue.",
                }
              : m
          )
        );
        setRetryPayload({
          content: trimmed,
          idempotencyKey: submissionKey,
          mode: "cloud",
        });
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
      }
    },
    [
      activeAssistantMessageId,
      conversation,
      conversationId,
      onAuthExpired,
      onContributionRequired,
      onConversationUpdated,
      reconcileWithServerMessages,
    ]
  );

  const submitPrivateLocalMessage = useCallback(
    async (
      rawContent: string,
      localModelId: string,
      existingIdempotencyKey?: string,
      answerPolicy: AnswerPolicy = "note_first_adaptive"
    ) => {
      const trimmed = rawContent.trim();
      if (!trimmed || !conversation || conversation.status !== "active") return;
      if (
        phaseRef.current === "submitting" ||
        phaseRef.current === "admission_waiting" ||
        phaseRef.current === "generating" ||
        phaseRef.current === "cancelling"
      ) {
        return;
      }

      const submissionKey = existingIdempotencyKey || createSubmissionIdempotencyKey();
      const tempUserId = `temp-local-user-${submissionKey}`;
      const tempAsstId = `temp-local-asst-${submissionKey}`;
      const nowIso = new Date().toISOString();

      activeModeRef.current = "private_local";
      userCancelledRef.current = false;
      activeServerMsgIdRef.current = null;
      setGenerationError(null);
      setRetryPayload(null);
      setQueueInfo(null);
      setPhase("submitting");
      setActiveAssistantMessageId(tempAsstId);

      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tempUserId && m.id !== tempAsstId),
        {
          id: tempUserId,
          conversation_id: conversationId,
          sequence_number: (prev[prev.length - 1]?.sequence_number || 0) + 1,
          role: "user",
          content: trimmed,
          status: "completed",
          created_at: nowIso,
          updated_at: nowIso,
        },
        {
          id: tempAsstId,
          conversation_id: conversationId,
          sequence_number: (prev[prev.length - 1]?.sequence_number || 0) + 2,
          role: "assistant",
          content: "",
          status: "pending",
          model_id: localModelId,
          provider_id: "ollama",
          created_at: nowIso,
          updated_at: nowIso,
        },
      ]);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      let preparedMsgId: string | null = null;
      let preparedRequestId: string | null = null;
      let accumulatedLocalText = "";
      let usageStatsPayload: Record<string, unknown> = {};

      try {
        // 1. Server prepares grounded prompt + Stage-3 evidence (ZERO cloud inference or Redis cloud semaphores)
        const prepared = await prepareLocalTurn(conversationId, {
          content: trimmed,
          model_id: localModelId,
          idempotency_key: submissionKey,
          answer_policy: answerPolicy,
        });
        if (activeConvRef.current !== conversationId) return;

        preparedMsgId = prepared.message_id;
        preparedRequestId = prepared.prepared_request_id;
        activeServerMsgIdRef.current = prepared.message_id;
        setActiveAssistantMessageId(prepared.message_id);
        setIdentifiedSourcesMap((prev) => ({
          ...prev,
          [prepared.message_id]: prepared.sources,
        }));
        setPhase("generating");

        setMessages((prev) =>
          prev.map((m) => {
            if (m.id === tempUserId) {
              return { ...m, id: prepared.user_message_id };
            }
            if (m.id === tempAsstId) {
              return {
                ...m,
                id: prepared.message_id,
                status: "streaming",
                model_id: prepared.model_id,
                provider_id: "ollama",
              };
            }
            return m;
          })
        );

        // 2. Browser streams directly from localhost Ollama (127.0.0.1:11434)
        await ollamaClient.chatStream(
          {
            model: prepared.model_id,
            messages: prepared.messages,
          },
          {
            onDelta: (delta) => {
              accumulatedLocalText += delta;
              const snapshot = accumulatedLocalText;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === prepared.message_id ? { ...m, content: snapshot, status: "streaming" } : m
                )
              );
            },
            onStats: (stats) => {
              usageStatsPayload = {
                input_tokens: stats.promptEvalCount,
                output_tokens: stats.evalCount,
                total_tokens: stats.promptEvalCount + stats.evalCount,
                usage_source: "device_reported",
              };
            },
          },
          controller.signal
        );

        if (activeConvRef.current !== conversationId) return;

        // 3. Authoritative server-side citation reconciliation and DB persistence
        const finalized = await finalizeLocalTurn(conversationId, {
          message_id: prepared.message_id,
          prepared_request_id: prepared.prepared_request_id,
          completion_text: accumulatedLocalText.trim() || "No response text produced locally.",
          finish_reason: "stop",
          token_usage: usageStatsPayload,
        });

        setPhase("completed");
        activeServerMsgIdRef.current = null;
        await reconcileWithServerMessages(conversationId, finalized.message_id);
        onConversationUpdated?.();
      } catch (err) {
        if (activeConvRef.current !== conversationId) return;

        if (isAbortError(err) || userCancelledRef.current) {
          if (preparedMsgId && preparedRequestId) {
            await cancelGeneration(preparedMsgId, conversationId).catch(() => {});
          }
          setPhase("cancelled");
          activeServerMsgIdRef.current = null;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === (preparedMsgId || tempAsstId)
                ? {
                    ...m,
                    status: "cancelled",
                    content: accumulatedLocalText,
                    finish_reason: "cancelled",
                  }
                : m
            )
          );
          onConversationUpdated?.();
          return;
        }

        if (err instanceof ApiError) {
          if (err.status === 401) {
            setMessages((prev) =>
              prev.filter((m) => m.id !== tempUserId && m.id !== tempAsstId)
            );
            setPhase("idle");
            setDraftOverride(trimmed);
            onAuthExpired?.();
            return;
          }
          if (err.status === 403 && err.code === "CHAT_CONTRIBUTION_REQUIRED") {
            setMessages((prev) =>
              prev.filter((m) => m.id !== tempUserId && m.id !== tempAsstId)
            );
            setPhase("idle");
            setDraftOverride(trimmed);
            onContributionRequired?.(err.message);
            return;
          }
        }

        const safeLocalErr =
          err instanceof Error
            ? err.message
            : "Local Ollama inference failed. Ensure Ollama is running locally.";

        // Strictly NO fallback to cloud; mark failed locally and cancel prepared placeholder if present
        if (preparedMsgId) {
          await cancelGeneration(preparedMsgId, conversationId).catch(() => {});
        }
        setPhase("failed");
        activeServerMsgIdRef.current = null;
        setGenerationError({
          code: "LOCAL_INFERENCE_FAILED",
          message: safeLocalErr,
          retryable: true,
        });
        setMessages((prev) =>
          prev.map((m) =>
            m.id === (preparedMsgId || tempAsstId)
              ? { ...m, status: "failed", error_message: safeLocalErr }
              : m
          )
        );
        setRetryPayload({
          content: trimmed,
          idempotencyKey: submissionKey,
          mode: "private_local",
          localModelId,
          answerPolicy,
        });
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
      }
    },
    [
      conversation,
      conversationId,
      onAuthExpired,
      onContributionRequired,
      onConversationUpdated,
      reconcileWithServerMessages,
    ]
  );

  const cancelActiveGeneration = useCallback(async () => {
    const controller = abortControllerRef.current;
    const serverMsgId = activeServerMsgIdRef.current;
    if (!controller || phaseRef.current === "cancelling") {
      return;
    }

    userCancelledRef.current = true;
    setPhase("cancelling");

    if (activeModeRef.current === "private_local") {
      controller.abort();
      if (serverMsgId) {
        await cancelGeneration(serverMsgId, conversationId).catch(() => {});
      }
      return;
    }

    try {
      const cancelResult = await abortAndCancelGeneration(controller, serverMsgId, conversationId);
      if (activeConvRef.current !== conversationId) return;

      if (!cancelResult) {
        // Aborted before server returned message_id -> reconcile to be sure
        const reconciled = await reconcileWithServerMessages(conversationId, null);
        if (reconciled?.status === "completed") {
          setPhase("completed");
        } else {
          setPhase("cancelled");
        }
        activeServerMsgIdRef.current = null;
        return;
      }

      if (cancelResult.status === "cancelled") {
        setPhase("cancelled");
        activeServerMsgIdRef.current = null;
        await reconcileWithServerMessages(conversationId, cancelResult.message_id);
        onConversationUpdated?.();
      }
    } catch (err) {
      if (activeConvRef.current !== conversationId) return;
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        (err.code === "GENERATION_ALREADY_COMPLETED" || err.code === "GENERATION_ALREADY_FAILED")
      ) {
        // Server finished milliseconds before cancel landed -> reflect authoritative terminal state!
        const reconciled = await reconcileWithServerMessages(conversationId, serverMsgId);
        if (reconciled?.status === "completed") {
          setPhase("completed");
          onConversationUpdated?.();
        } else {
          setPhase("failed");
          setGenerationError({
            code: err.code,
            message: err.message,
            retryable: false,
          });
        }
        activeServerMsgIdRef.current = null;
        return;
      }

      setPhase("failed");
      setGenerationError({
        code: err instanceof ApiError ? err.code : "CANCEL_FAILED",
        message:
          err instanceof Error
            ? err.message
            : "Unable to confirm server cancellation. Please refresh or inspect message history.",
        retryable: false,
      });
    }
  }, [conversationId, onConversationUpdated, reconcileWithServerMessages]);

  const retryFailedSubmission = useCallback(async () => {
    if (!retryPayload) return;
    const payload = retryPayload;
    setRetryPayload(null);
    if (payload.mode === "private_local" && payload.localModelId) {
      await submitPrivateLocalMessage(
        payload.content,
        payload.localModelId,
        payload.idempotencyKey,
        payload.answerPolicy
      );
    } else {
      await submitCloudMessage(payload.content, payload.idempotencyKey, payload.answerPolicy);
    }
  }, [retryPayload, submitCloudMessage, submitPrivateLocalMessage]);

  const isGenerationActive =
    phase === "submitting" ||
    phase === "admission_waiting" ||
    phase === "generating" ||
    phase === "cancelling";

  const isProvisionalMessage = useCallback(
    (messageId: string) =>
      messageId === activeAssistantMessageId &&
      (phase === "submitting" ||
        phase === "admission_waiting" ||
        phase === "generating" ||
        phase === "cancelling"),
    [activeAssistantMessageId, phase]
  );

  const wasContentReplaced = useCallback(
    (messageId: string) => Boolean(replacedMap[messageId]),
    [replacedMap]
  );

  const identifiedSourcesForMessage = useCallback(
    (messageId: string) => identifiedSourcesMap[messageId],
    [identifiedSourcesMap]
  );

  const clearDraftOverride = useCallback(() => {
    setDraftOverride(null);
  }, []);

  return {
    messages,
    loadingOlder,
    hasMoreOlder,
    phase,
    activeAssistantMessageId,
    isGenerationActive,
    isProvisionalMessage,
    wasContentReplaced,
    identifiedSourcesForMessage,
    queueInfo,
    generationError,
    retryPayload,
    draftOverride,
    clearDraftOverride,
    loadOlderMessages,
    submitCloudMessage,
    submitPrivateLocalMessage,
    cancelActiveGeneration,
    retryFailedSubmission,
  };
}
