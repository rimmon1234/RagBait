"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArchiveRestore,
  Cloud,
  HelpCircle,
  Laptop,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import type { OllamaConnectionInfo, OllamaTagModel } from "@/lib/local-inference/ollama-types";
import type { ChatConversationStatus, GenerationPhase, InferenceExecutionMode } from "@/types/chat";

export const MAX_CHAT_INPUT_LENGTH = 32768;

export interface ChatComposerProps {
  conversationStatus: ChatConversationStatus;
  subjectName: string;
  canGenerate: boolean;
  contributionReason?: string;
  inferenceMode: InferenceExecutionMode;
  onInferenceModeChange: (mode: InferenceExecutionMode) => void;
  selectedLocalModel: string;
  onLocalModelChange: (modelId: string) => void;
  isGenerationActive: boolean;
  phase: GenerationPhase;
  draftOverride?: string | null;
  onClearDraftOverride?: () => void;
  onSend: (content: string) => Promise<void> | void;
  onCancel: () => void;
}

export function ChatComposer({
  conversationStatus,
  subjectName,
  canGenerate,
  contributionReason,
  inferenceMode,
  onInferenceModeChange,
  selectedLocalModel,
  onLocalModelChange,
  isGenerationActive,
  phase,
  draftOverride,
  onClearDraftOverride,
  onSend,
  onCancel,
}: ChatComposerProps) {
  const [draft, setDraft] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Isolated Local Ollama probe state (strictly 127.0.0.1:11434)
  const [localStatus, setLocalStatus] = useState<OllamaConnectionInfo | null>(null);
  const [localModels, setLocalModels] = useState<OllamaTagModel[]>([]);
  const [checkingLocal, setCheckingLocal] = useState(false);
  const [showLocalSetupGuide, setShowLocalSetupGuide] = useState(false);

  // Sync draft override (e.g. after pre-stream failure) without stripping user input
  useEffect(() => {
    if (typeof draftOverride === "string" && draftOverride.length > 0) {
      setDraft(draftOverride);
      onClearDraftOverride?.();
    }
  }, [draftOverride, onClearDraftOverride]);

  // Auto-resize textarea cleanly
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [draft]);

  const probeLocalOllama = useCallback(async () => {
    setCheckingLocal(true);
    try {
      const conn = await ollamaClient.checkConnection();
      setLocalStatus(conn);
      if (conn.state === "connected") {
        const models = await ollamaClient.listModels();
        setLocalModels(models);
        if (models.length > 0 && (!selectedLocalModel || !models.some((m) => m.name === selectedLocalModel))) {
          onLocalModelChange(models[0].name);
        }
      } else {
        setLocalModels([]);
      }
    } catch {
      setLocalStatus({
        state: "connection_failed",
        error: "Unable to contact local Ollama daemon at http://127.0.0.1:11434.",
      });
      setLocalModels([]);
    } finally {
      setCheckingLocal(false);
    }
  }, [selectedLocalModel, onLocalModelChange]);

  useEffect(() => {
    if (inferenceMode === "private_local") {
      void probeLocalOllama();
    }
  }, [inferenceMode, probeLocalOllama]);

  const isArchived = conversationStatus !== "active";
  const tooLong = draft.length > MAX_CHAT_INPUT_LENGTH;
  const trimmedEmpty = draft.trim().length === 0;
  const localReady =
    inferenceMode !== "private_local" ||
    (localStatus?.state === "connected" && Boolean(selectedLocalModel));

  const submitDisabled =
    isArchived || !canGenerate || isGenerationActive || trimmedEmpty || tooLong || !localReady;

  const handleSubmit = async () => {
    if (isArchived || !canGenerate || isGenerationActive) return;

    if (trimmedEmpty) {
      setValidationError("Please enter a study question before sending.");
      return;
    }
    if (tooLong) {
      setValidationError(
        `Message exceeds maximum length of ${MAX_CHAT_INPUT_LENGTH.toLocaleString()} characters.`
      );
      return;
    }
    if (inferenceMode === "private_local" && !localReady) {
      setValidationError("Connect local Ollama and select a model before sending in Private Local mode.");
      return;
    }

    const toSend = draft;
    setValidationError(null);
    setDraft("");
    await onSend(toSend);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!submitDisabled) {
        void handleSubmit();
      } else if (tooLong) {
        setValidationError(
          `Message exceeds maximum length of ${MAX_CHAT_INPUT_LENGTH.toLocaleString()} characters.`
        );
      }
    }
  };

  return (
    <footer className="border-t border-gray-200 bg-gray-50/90 px-4 py-3 md:px-6">
      <div className="mx-auto max-w-4xl space-y-2">
        {/* Mode Switcher & Private Local Controls */}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="inline-flex rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
            <button
              type="button"
              disabled={isGenerationActive}
              onClick={() => onInferenceModeChange("cloud")}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors disabled:opacity-60 ${
                inferenceMode === "cloud"
                  ? "bg-blue-600 text-white"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              <Cloud className="h-3.5 w-3.5" />
              <span>Cloud AI</span>
            </button>
            <button
              type="button"
              disabled={isGenerationActive}
              onClick={() => onInferenceModeChange("private_local")}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors disabled:opacity-60 ${
                inferenceMode === "private_local"
                  ? "bg-emerald-700 text-white"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              <Laptop className="h-3.5 w-3.5" />
              <span>Private Local</span>
            </button>
          </div>

          {inferenceMode === "private_local" ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-800 border border-emerald-200">
                <ShieldCheck className="h-3 w-3" />
                Loopback only (no cloud fallback)
              </span>

              {checkingLocal ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-gray-500">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Checking Ollama...
                </span>
              ) : localStatus?.state === "connected" ? (
                <select
                  aria-label="Local Ollama model"
                  value={selectedLocalModel}
                  disabled={isGenerationActive || localModels.length === 0}
                  onChange={(e) => onLocalModelChange(e.target.value)}
                  className="rounded-lg border border-emerald-300 bg-white px-2 py-1 text-xs font-medium text-gray-800 focus:border-emerald-500 focus:outline-none"
                >
                  {localModels.length === 0 ? (
                    <option value="">No local models pulled</option>
                  ) : (
                    localModels.map((m) => (
                      <option key={m.name} value={m.name}>
                        {m.name}
                      </option>
                    ))
                  )}
                </select>
              ) : (
                <div className="flex items-center gap-2 text-[11px] text-amber-800">
                  <span>{localStatus?.error || "Ollama offline"}</span>
                  <button
                    type="button"
                    onClick={() => void probeLocalOllama()}
                    className="inline-flex items-center gap-1 rounded border border-amber-300 bg-white px-1.5 py-0.5 font-medium hover:bg-amber-50"
                  >
                    <RefreshCw className="h-2.5 w-2.5" />
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowLocalSetupGuide((prev) => !prev)}
                    className="inline-flex items-center gap-1 underline text-blue-700 hover:text-blue-900"
                  >
                    <HelpCircle className="h-3 w-3" />
                    <span>{showLocalSetupGuide ? "Hide Setup" : "Setup Guide"}</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <span className="text-[11px] text-gray-500">
              Grounded answers cite documents strictly from <strong className="text-gray-700">{subjectName}</strong>.
            </span>
          )}
        </div>

        {/* Inline Private Local Ollama Setup & CORS Guide (no external playground redirect) */}
        {inferenceMode === "private_local" && showLocalSetupGuide && (
          <div className="rounded-xl border border-blue-200 bg-blue-50/80 p-3 text-xs text-blue-950">
            <div className="flex items-center justify-between">
              <span className="font-semibold">Local Ollama &amp; CORS Setup (127.0.0.1:11434)</span>
              <button
                type="button"
                aria-label="Close setup guide"
                onClick={() => setShowLocalSetupGuide(false)}
                className="rounded p-0.5 text-blue-700 hover:bg-blue-100"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="mt-1 text-[11px] text-blue-900 leading-relaxed">
              Private Local streams directly from your browser to{" "}
              <code className="rounded bg-blue-100 px-1 py-0.5">http://127.0.0.1:11434</code> with
              zero cloud fallback. Allow Local Network access if prompted by Chrome, and set{" "}
              <code className="rounded bg-blue-100 px-1 py-0.5">OLLAMA_ORIGINS=*</code> when
              running Ollama so browser requests succeed.
            </p>
          </div>
        )}

        {/* Status / Validation Notice */}
        {isArchived && (
          <div className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700">
            <ArchiveRestore className="h-4 w-4 shrink-0 text-gray-500" />
            <span>This conversation is archived and read-only. Restore it to send new questions.</span>
          </div>
        )}

        {!isArchived && !canGenerate && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <span>
              {contributionReason ||
                "An active OpenRouter contribution key is required to send new messages."}
            </span>
            <Link href="/credentials" className="font-semibold text-blue-700 underline">
              Connect OpenRouter Key
            </Link>
          </div>
        )}

        {(validationError || tooLong) && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {tooLong
                ? `Message is ${draft.length.toLocaleString()} / ${MAX_CHAT_INPUT_LENGTH.toLocaleString()} characters. Shorten your prompt without losing text.`
                : validationError}
            </span>
          </div>
        )}

        {/* Composer Form Box */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
          className={`flex items-end gap-2 rounded-2xl border bg-white p-2 shadow-sm transition-colors ${
            tooLong
              ? "border-red-400 focus-within:border-red-500"
              : "border-gray-300 focus-within:border-blue-500"
          }`}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            aria-label="Study question input"
            value={draft}
            disabled={isArchived || !canGenerate}
            onChange={(e) => {
              setDraft(e.target.value);
              if (validationError) setValidationError(null);
            }}
            onKeyDown={handleKeyDown}
            placeholder={
              isArchived
                ? "Conversation is archived..."
                : !canGenerate
                ? "Connect an OpenRouter credential to send messages..."
                : inferenceMode === "private_local"
                ? `Ask a Private Local question in ${subjectName}...`
                : `Ask a study question about ${subjectName}... (Enter to send, Shift+Enter for new line)`
            }
            className="max-h-44 min-h-[38px] flex-1 resize-none bg-transparent px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none disabled:cursor-not-allowed disabled:text-gray-400"
          />

          <div className="flex items-center gap-2 shrink-0 pb-0.5">
            {(draft.length >= 28000 || tooLong) && (
              <span
                className={`text-[11px] font-mono ${
                  tooLong ? "font-semibold text-red-600" : "text-gray-400"
                }`}
              >
                {draft.length.toLocaleString()}/{MAX_CHAT_INPUT_LENGTH.toLocaleString()}
              </span>
            )}

            {isGenerationActive ? (
              <button
                type="button"
                onClick={onCancel}
                disabled={phase === "cancelling"}
                aria-label="Stop generation"
                className="inline-flex items-center gap-1.5 rounded-xl bg-red-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-60"
              >
                {phase === "cancelling" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Square className="h-3.5 w-3.5 fill-current" />
                )}
                <span>{phase === "cancelling" ? "Stopping..." : "Stop"}</span>
              </button>
            ) : (
              <button
                type="submit"
                disabled={submitDisabled}
                aria-label="Send message"
                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400"
              >
                <Send className="h-3.5 w-3.5" />
                <span>Send</span>
              </button>
            )}
          </div>
        </form>
      </div>
    </footer>
  );
}
