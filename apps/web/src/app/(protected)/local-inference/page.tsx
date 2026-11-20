"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cpu,
  HelpCircle,
  Loader2,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import { isAbortError } from "@/lib/local-inference/ollama-errors";
import type {
  OllamaChatMessage,
  OllamaConnectionState,
  OllamaTagModel,
  OllamaUsageStats,
} from "@/lib/local-inference/ollama-types";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
}

export default function LocalInferencePage() {
  const router = useRouter();
  const [authLoading, setAuthLoading] = useState(true);

  // Connection and model state
  const [connectionState, setConnectionState] = useState<OllamaConnectionState>("checking");
  const [ollamaVersion, setOllamaVersion] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [models, setModels] = useState<OllamaTagModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Playground state
  const [messages, setMessages] = useState<Message[]>([]);
  const [promptInput, setPromptInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [usageStats, setUsageStats] = useState<OllamaUsageStats | null>(null);

  // Configuration drawer state
  const [showConfig, setShowConfig] = useState(false);
  const [showSetupGuide, setShowSetupGuide] = useState(false);
  const [showThinking, setShowThinking] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a helpful, precise academic and coding assistant running locally on the student's machine."
  );
  const [temperature, setTemperature] = useState(0.7);

  const abortControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Check Supabase authentication and user approval status
  useEffect(() => {
    const supabase = createClient();

    const checkAuth = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!session) {
        router.push("/login");
        return;
      }

      try {
        const res = await fetch("/api/v1/me", {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        });

        if (res.ok) {
          const profile = await res.json();
          if (profile.approval_status === "pending") {
            router.push("/access/pending");
            return;
          }
          if (profile.approval_status === "rejected") {
            router.push("/access/rejected");
            return;
          }
        }
      } catch {
        // Continue if profile check fails temporarily
      } finally {
        setAuthLoading(false);
      }
    };

    checkAuth();
  }, [router]);

  // Check Ollama connection and load models
  const refreshOllama = useCallback(async () => {
    setIsRefreshing(true);
    setConnectionState("checking");
    setConnectionError(null);

    const probe = await ollamaClient.checkConnection();
    setConnectionState(probe.state);
    setOllamaVersion(probe.version || null);
    setConnectionError(probe.error || null);

    if (probe.state === "connected") {
      try {
        const availableModels = await ollamaClient.listModels();
        setModels(availableModels);
        setSelectedModel((prev) => prev || (availableModels.length > 0 ? availableModels[0].name : ""));
      } catch (err) {
        console.error("Failed to load Ollama models", err);
      }
    } else {
      setModels([]);
    }

    setIsRefreshing(false);
  }, []);

  useEffect(() => {
    if (!authLoading) {
      refreshOllama();
    }
  }, [authLoading, refreshOllama]);

  // Scroll to bottom when messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  // Handle generation submission
  const handleSend = async () => {
    const trimmed = promptInput.trim();
    if (!trimmed || isStreaming || !selectedModel || connectionState !== "connected") {
      return;
    }

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
    };

    const assistantMessageId = `asst-${Date.now()}`;
    const initialAssistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      thinking: "",
    };

    const updatedMessages = [...messages, userMessage];
    setMessages([...updatedMessages, initialAssistantMessage]);
    setPromptInput("");
    setIsStreaming(true);
    setUsageStats(null);

    // Build chat payload
    const ollamaMessages: OllamaChatMessage[] = [];
    if (systemPrompt.trim()) {
      ollamaMessages.push({ role: "system", content: systemPrompt.trim() });
    }
    for (const msg of updatedMessages) {
      ollamaMessages.push({ role: msg.role, content: msg.content });
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await ollamaClient.chatStream(
        {
          model: selectedModel,
          messages: ollamaMessages,
          options: {
            temperature,
          },
        },
        {
          onDelta: (delta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId ? { ...m, content: m.content + delta } : m
              )
            );
          },
          onThinkingDelta: (thinkingDelta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? { ...m, thinking: (m.thinking || "") + thinkingDelta }
                  : m
              )
            );
          },
          onStats: (stats) => {
            setUsageStats(stats);
          },
        },
        controller.signal
      );
    } catch (err) {
      if (isAbortError(err)) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId
              ? {
                  ...m,
                  content: m.content + (m.content ? "\n\n*(Generation stopped by user)*" : "*(Generation stopped)*"),
                }
              : m
          )
        );
      } else {
        const errorMsg = err instanceof Error ? err.message : "Inference error";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId
              ? { ...m, content: `⚠️ Error during local inference: ${errorMsg}` }
              : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  const handleClear = () => {
    handleStop();
    setMessages([]);
    setUsageStats(null);
  };

  if (authLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Page Header */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">Private Local Inference</h1>
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
              100% Private Loopback
            </span>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            Execute open-source models on your local hardware via Ollama. Zero server routing,
            zero latency cost, and zero data leaves your device.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSetupGuide(!showSetupGuide)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
          >
            <HelpCircle className="h-4 w-4 text-gray-500" />
            Setup & CORS Guide
          </button>
          <button
            onClick={refreshOllama}
            disabled={isRefreshing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 text-gray-500 ${isRefreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Connection Status Banner */}
      {connectionState === "connected" ? (
        <div className="mb-6 flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm text-emerald-800">
          <div className="flex items-center gap-2.5">
            <div className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
            </div>
            <span className="font-medium">Ollama Connected</span>
            <span className="text-emerald-600 text-xs font-mono bg-emerald-100/80 px-2 py-0.5 rounded">
              v{ollamaVersion || "unknown"} (127.0.0.1:11434)
            </span>
          </div>
          <span className="text-xs text-emerald-700 font-medium hidden sm:inline">
            Direct browser-to-local loopback active
          </span>
        </div>
      ) : connectionState === "cors_blocked" ? (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-semibold text-amber-900">
                Ollama Reachable but Blocked by CORS (`OLLAMA_ORIGINS`)
              </p>
              <p className="mt-1 text-xs text-amber-800 leading-relaxed">
                Your browser prevented connecting to Ollama at <code className="bg-amber-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code> because cross-origin requests from this web app are not permitted by your Ollama configuration.
              </p>
              <div className="mt-2.5 flex items-center gap-3">
                <button
                  onClick={() => setShowSetupGuide(true)}
                  className="rounded-md bg-amber-700 px-3 py-1 text-xs font-medium text-white hover:bg-amber-800"
                >
                  Configure OLLAMA_ORIGINS
                </button>
                <button
                  onClick={refreshOllama}
                  disabled={isRefreshing}
                  className="text-xs font-medium text-amber-900 underline hover:no-underline"
                >
                  Retry Connection
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : connectionState === "disconnected" ? (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-rose-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-semibold text-rose-900">Ollama Daemon Offline</p>
              <p className="mt-1 text-xs text-rose-800 leading-relaxed">
                Cannot reach Ollama at <code className="bg-rose-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>. Please ensure Ollama is installed and running on your computer.
              </p>
              <div className="mt-2.5 flex items-center gap-3">
                <button
                  onClick={() => setShowSetupGuide(true)}
                  className="rounded-md bg-rose-700 px-3 py-1 text-xs font-medium text-white hover:bg-rose-800"
                >
                  View Setup Guide
                </button>
                <button
                  onClick={refreshOllama}
                  disabled={isRefreshing}
                  className="text-xs font-medium text-rose-900 underline hover:no-underline"
                >
                  Retry Connection
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
          <span>Probing Ollama loopback status at http://127.0.0.1:11434...</span>
        </div>
      )}

      {/* Setup Guide Collapsible Accordion */}
      {showSetupGuide && (
        <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50/50 p-5 shadow-sm">
          <div className="flex items-center justify-between pb-3 border-b border-blue-200">
            <div className="flex items-center gap-2">
              <Terminal className="h-5 w-5 text-blue-700" />
              <h2 className="text-base font-semibold text-blue-950">
                Ollama Local Setup & CORS Configuration Guide
              </h2>
            </div>
            <button
              onClick={() => setShowSetupGuide(false)}
              className="text-xs font-medium text-blue-700 hover:text-blue-900"
            >
              Close
            </button>
          </div>

          <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
            {/* Windows */}
            <div className="rounded-lg bg-white p-4 border border-blue-100 shadow-xs">
              <p className="font-semibold text-gray-900 mb-1.5">Windows Setup</p>
              <p className="text-gray-600 mb-2 leading-relaxed">
                Run PowerShell as Administrator to allow this web origin:
              </p>
              <pre className="overflow-x-auto rounded bg-gray-900 p-2 text-emerald-400 font-mono text-[11px] select-all">
                {`[System.Environment]::SetEnvironmentVariable(
  "OLLAMA_ORIGINS",
  "http://localhost:3000,http://127.0.0.1:3000",
  "User"
)`}
              </pre>
              <p className="mt-2 text-gray-500">
                Then quit Ollama from the system tray and restart it.
              </p>
            </div>

            {/* macOS */}
            <div className="rounded-lg bg-white p-4 border border-blue-100 shadow-xs">
              <p className="font-semibold text-gray-900 mb-1.5">macOS Setup</p>
              <p className="text-gray-600 mb-2 leading-relaxed">
                Set environment variable via terminal:
              </p>
              <pre className="overflow-x-auto rounded bg-gray-900 p-2 text-emerald-400 font-mono text-[11px] select-all">
                {`launchctl setenv OLLAMA_ORIGINS "http://localhost:3000,http://127.0.0.1:3000"`}
              </pre>
              <p className="mt-2 text-gray-500">
                Then restart the Ollama application.
              </p>
            </div>

            {/* Linux */}
            <div className="rounded-lg bg-white p-4 border border-blue-100 shadow-xs">
              <p className="font-semibold text-gray-900 mb-1.5">Linux (systemd)</p>
              <p className="text-gray-600 mb-2 leading-relaxed">
                Edit the systemd service configuration:
              </p>
              <pre className="overflow-x-auto rounded bg-gray-900 p-2 text-emerald-400 font-mono text-[11px] select-all">
                {`sudo systemctl edit ollama.service
# Add:
[Service]
Environment="OLLAMA_ORIGINS=http://localhost:3000,http://127.0.0.1:3000"`}
              </pre>
              <p className="mt-2 text-gray-500">
                Reload: <code className="text-blue-700">sudo systemctl restart ollama</code>
              </p>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-blue-200 flex items-center justify-between text-xs text-blue-900">
            <span>
              💡 Download recommended lightweight models in terminal:{" "}
              <code className="bg-blue-100 font-mono px-1.5 py-0.5 rounded text-blue-950 font-semibold">
                ollama pull llama3.2
              </code>{" "}
              or{" "}
              <code className="bg-blue-100 font-mono px-1.5 py-0.5 rounded text-blue-950 font-semibold">
                ollama pull qwen2.5-coder:7b
              </code>
            </span>
          </div>
        </div>
      )}

      {/* Main Grid: Playground & Local Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Column: Playground (3 cols) */}
        <div className="lg:col-span-3 flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          {/* Controls Bar */}
          <div className="flex flex-wrap items-center justify-between border-b border-gray-200 bg-gray-50/80 px-4 py-3 gap-3">
            <div className="flex items-center gap-3">
              <label htmlFor="model-select" className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                Local Model:
              </label>
              {models.length > 0 ? (
                <select
                  id="model-select"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={isStreaming}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 shadow-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                >
                  {models.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.name} {m.details?.parameter_size ? `(${m.details.parameter_size})` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded border border-amber-200">
                  {connectionState === "connected"
                    ? "No local models found (run `ollama pull llama3.2`)"
                    : "Connect to Ollama to view models"}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowConfig(!showConfig)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  showConfig
                    ? "border-blue-300 bg-blue-50 text-blue-700"
                    : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                <Settings2 className="h-3.5 w-3.5" />
                Parameters
              </button>

              {messages.length > 0 && (
                <button
                  onClick={handleClear}
                  disabled={isStreaming}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  title="Clear conversation"
                >
                  <Trash2 className="h-3.5 w-3.5 text-gray-500" />
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* Collapsible Parameters Drawer */}
          {showConfig && (
            <div className="border-b border-gray-200 bg-gray-50 p-4 text-xs space-y-4">
              <div>
                <label className="block font-medium text-gray-700 mb-1">
                  System Prompt (Prompt Engineering)
                </label>
                <textarea
                  rows={2}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  disabled={isStreaming}
                  className="w-full rounded-md border border-gray-300 bg-white p-2 text-xs text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="Instructions for the local model..."
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="font-medium text-gray-700">Temperature</label>
                  <span className="font-mono text-gray-600">{temperature.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0.0"
                  max="1.5"
                  step="0.05"
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value))}
                  disabled={isStreaming}
                  className="w-full accent-blue-600 cursor-pointer"
                />
                <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
                  <span>Deterministic (0.0)</span>
                  <span>Balanced (0.7)</span>
                  <span>Creative (1.5)</span>
                </div>
              </div>
            </div>
          )}

          {/* Conversation Messages Display */}
          <div className="flex-1 p-4 min-h-[380px] max-h-[550px] overflow-y-auto space-y-4">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center text-gray-400">
                <Cpu className="h-12 w-12 stroke-[1.25] text-gray-300 mb-3" />
                <p className="text-sm font-medium text-gray-600">Local Hardware Playground</p>
                <p className="mt-1 text-xs text-gray-400 max-w-sm">
                  Type a prompt below to interact with your locally installed Ollama models.
                  All operations remain strictly offline on your computer.
                </p>
              </div>
            ) : (
              messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex flex-col ${
                    msg.role === "user" ? "items-end" : "items-start"
                  }`}
                >
                  <div className="text-[11px] font-semibold text-gray-400 mb-1 px-1 uppercase tracking-wider">
                    {msg.role === "user" ? "You" : selectedModel || "Assistant"}
                  </div>

                  <div
                    className={`rounded-2xl px-4 py-3 text-sm max-w-[85%] leading-relaxed ${
                      msg.role === "user"
                        ? "bg-blue-600 text-white rounded-tr-xs"
                        : "bg-gray-100 text-gray-800 rounded-tl-xs border border-gray-200/80"
                    }`}
                  >
                    {/* Expandable Thinking Process (for reasoning models like Qwen 3 / DeepSeek R1) */}
                    {msg.thinking && (
                      <div className="mb-2.5 rounded-lg border border-purple-200 bg-purple-50/70 p-2.5 text-xs text-purple-900">
                        <button
                          type="button"
                          onClick={() => setShowThinking(!showThinking)}
                          className="flex items-center gap-1 font-semibold text-purple-800 hover:text-purple-950"
                        >
                          {showThinking ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                          <span>Reasoning Process ({msg.thinking.length} chars)</span>
                        </button>
                        {showThinking && (
                          <pre className="mt-2 whitespace-pre-wrap font-sans text-xs text-purple-950 leading-relaxed border-t border-purple-200/60 pt-2">
                            {msg.thinking}
                          </pre>
                        )}
                      </div>
                    )}

                    <div className="whitespace-pre-wrap font-sans">
                      {msg.content || (isStreaming && msg.role === "assistant" ? "..." : "")}
                    </div>
                  </div>
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div className="border-t border-gray-200 bg-white p-3 sm:p-4">
            <div className="relative flex items-end gap-2">
              <textarea
                rows={2}
                value={promptInput}
                onChange={(e) => setPromptInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                disabled={isStreaming || connectionState !== "connected" || models.length === 0}
                placeholder={
                  connectionState === "connected" && models.length > 0
                    ? `Ask ${selectedModel}... (Enter to send, Shift+Enter for newline)`
                    : "Connect to Ollama to begin inference..."
                }
                className="w-full resize-none rounded-xl border border-gray-300 bg-gray-50/50 p-3 pr-20 text-sm text-gray-900 shadow-inner placeholder:text-gray-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              />

              <div className="absolute right-2.5 bottom-2.5 flex items-center gap-1.5">
                {isStreaming ? (
                  <button
                    type="button"
                    onClick={handleStop}
                    className="inline-flex items-center gap-1 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-rose-700"
                  >
                    <Square className="h-3.5 w-3.5 fill-current" />
                    Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={
                      !promptInput.trim() ||
                      connectionState !== "connected" ||
                      models.length === 0
                    }
                    className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-blue-700 disabled:opacity-40 disabled:hover:bg-blue-600"
                  >
                    <Send className="h-3.5 w-3.5" />
                    Send
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Local Usage Telemetry & Hardware Info (1 col) */}
        <div className="flex flex-col gap-5">
          {/* Telemetry Card */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2 pb-3 border-b border-gray-100">
              <Zap className="h-4 w-4 text-amber-500" />
              <h3 className="text-sm font-semibold text-gray-900">Device Telemetry</h3>
            </div>

            <div className="mt-3">
              <span className="inline-block rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 border border-emerald-200 mb-3">
                Non-Billable / Device Native
              </span>

              {usageStats ? (
                <dl className="grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded-lg bg-gray-50 p-2.5">
                    <dt className="text-gray-500 font-medium">Prompt Tokens</dt>
                    <dd className="mt-1 text-base font-semibold text-gray-900">
                      {usageStats.promptEvalCount}
                    </dd>
                    <span className="text-[10px] text-gray-400">
                      {usageStats.promptEvalDurationMs}ms
                    </span>
                  </div>

                  <div className="rounded-lg bg-gray-50 p-2.5">
                    <dt className="text-gray-500 font-medium">Output Tokens</dt>
                    <dd className="mt-1 text-base font-semibold text-gray-900">
                      {usageStats.evalCount}
                    </dd>
                    <span className="text-[10px] text-gray-400">
                      {usageStats.evalDurationMs}ms
                    </span>
                  </div>

                  <div className="rounded-lg bg-gray-50 p-2.5">
                    <dt className="text-gray-500 font-medium">Speed</dt>
                    <dd className="mt-1 text-base font-semibold text-blue-600">
                      {usageStats.tokensPerSecond}{" "}
                      <span className="text-xs font-normal text-gray-500">tok/s</span>
                    </dd>
                  </div>

                  <div className="rounded-lg bg-gray-50 p-2.5">
                    <dt className="text-gray-500 font-medium">Total Latency</dt>
                    <dd className="mt-1 text-base font-semibold text-gray-900">
                      {(usageStats.totalDurationMs / 1000).toFixed(2)}s
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="text-xs text-gray-400 italic">
                  Run a prompt to view on-device token metrics and generation throughput.
                </p>
              )}
            </div>
          </div>

          {/* Privacy Guarantee Card */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm text-xs">
            <div className="flex items-center gap-2 pb-2 border-b border-gray-100">
              <ShieldCheck className="h-4 w-4 text-emerald-600" />
              <h3 className="text-sm font-semibold text-gray-900">Privacy Guarantees</h3>
            </div>

            <ul className="mt-3 space-y-2 text-gray-600 leading-relaxed">
              <li className="flex items-start gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                <span>Zero prompt or output data sent to RagBait servers.</span>
              </li>
              <li className="flex items-start gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                <span>Hardcoded loopback fetch (<code className="text-[10px]">127.0.0.1:11434</code>).</span>
              </li>
              <li className="flex items-start gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                <span>No user cookies or JWTs transmitted to Ollama.</span>
              </li>
              <li className="flex items-start gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                <span>Zero cloud budget consumption.</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
