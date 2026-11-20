"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Cpu,
  HelpCircle,
  Key,
  Loader2,
  Lock,
  RefreshCw,
  Send,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  isSecureLoopbackContext,
  queryLoopbackPermission,
} from "@/lib/local-inference/lna-permissions";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import { isAbortError } from "@/lib/local-inference/ollama-errors";
import * as routingClient from "@/lib/routing-client";
import type { RouteHeaders } from "@/lib/routing-client";
import type {
  LoopbackPermissionState,
  OllamaChatMessage,
  OllamaConnectionState,
  OllamaTagModel,
  OllamaUsageStats,
} from "@/lib/local-inference/ollama-types";
import type { ChatEntitlementResponse } from "@/types";

export type ExecutionPreference = "auto" | "owner_only" | "private";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function formatModelSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1000 * 1000 * 1000) {
    return `${(bytes / (1000 * 1000)).toFixed(0)} MB`;
  }
  return `${(bytes / (1000 * 1000 * 1000)).toFixed(1)} GB`;
}

function isHeavyModel(model?: OllamaTagModel): boolean {
  if (!model) return false;
  if (model.size && model.size >= 5 * 1024 * 1024 * 1024) {
    return true;
  }
  const pSize = model.details?.parameter_size?.toLowerCase() || "";
  if (
    pSize.includes("14b") ||
    pSize.includes("13b") ||
    pSize.includes("32b") ||
    pSize.includes("70b")
  ) {
    return true;
  }
  return false;
}

export interface LocalInferenceWorkspaceProps {
  defaultMode?: ExecutionPreference;
}

export function LocalInferenceWorkspace({
  defaultMode = "auto",
}: LocalInferenceWorkspaceProps = {}) {
  const router = useRouter();
  const [authLoading, setAuthLoading] = useState(true);

  // Chat contribution entitlement state
  const [entitlement, setEntitlement] = useState<ChatEntitlementResponse | null>(null);
  const [checkingEntitlement, setCheckingEntitlement] = useState(true);

  // Unified execution mode preference: "auto" | "owner_only" | "private"
  const [executionMode, setExecutionMode] = useState<ExecutionPreference>(defaultMode);

  // Connection and model state (for Private Local mode)
  const [connectionState, setConnectionState] = useState<OllamaConnectionState>("checking");
  const [ollamaVersion, setOllamaVersion] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [models, setModels] = useState<OllamaTagModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Cloud model selection (for Automatic and My API Keys Only modes)
  const [selectedCloudModel, setSelectedCloudModel] = useState<string>("");
  const [lastRouteHeaders, setLastRouteHeaders] = useState<RouteHeaders | null>(null);

  // Unified conversation playground state
  const [messages, setMessages] = useState<Message[]>([]);
  const [promptInput, setPromptInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [usageStats, setUsageStats] = useState<OllamaUsageStats | null>(null);

  // Configuration drawer state
  const [showConfig, setShowConfig] = useState(false);
  const [showSetupGuide, setShowSetupGuide] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a helpful, precise academic and coding assistant running locally on the student's machine."
  );
  const [temperature, setTemperature] = useState(0.7);

  const abortControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Check Supabase authentication, user approval status, and chat contribution entitlement
  const checkEntitlement = useCallback(async (token: string) => {
    setCheckingEntitlement(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
      const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me/chat-entitlement`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.ok) {
        const data: ChatEntitlementResponse = await res.json();
        setEntitlement(data);
      }
    } catch {
      // ignore
    } finally {
      setCheckingEntitlement(false);
    }
  }, []);

  useEffect(() => {
    const supabase = createClient();

    const checkAuthAndAccess = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!session) {
        router.push("/login");
        return;
      }

      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me`, {
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

        // Fetch authoritative chat contribution entitlement
        await checkEntitlement(session.access_token);
      } catch {
        // Continue if profile check fails temporarily
      } finally {
        setAuthLoading(false);
      }
    };

    checkAuthAndAccess();
  }, [router, checkEntitlement]);

  // Connect to local Ollama daemon and discover models
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

  // Initialize permission state and probe loopback when in private mode or on initial load
  useEffect(() => {
    if (authLoading) return;

    let permissionStatusRef: PermissionStatus | null = null;
    let isMounted = true;

    const initConnection = async () => {
      if (!isSecureLoopbackContext()) {
        if (isMounted) setConnectionState("insecure_context");
        return;
      }

      let permResult: { state: LoopbackPermissionState; permissionStatus?: PermissionStatus };
      try {
        permResult = await queryLoopbackPermission();
      } catch {
        permResult = { state: "unsupported" };
      }
      if (!isMounted) return;

      if (permResult.permissionStatus) {
        permissionStatusRef = permResult.permissionStatus;
        permissionStatusRef.addEventListener("change", () => {
          if (!isMounted) return;
          const updatedState = permissionStatusRef?.state;
          if (updatedState === "granted") {
            refreshOllama();
          } else if (updatedState === "denied") {
            setConnectionState("loopback_permission_denied");
          } else {
            setConnectionState("loopback_permission_required");
          }
        });
      }

      if (permResult.state === "granted") {
        refreshOllama();
      } else if (permResult.state === "denied") {
        setConnectionState("loopback_permission_denied");
      } else {
        setConnectionState("loopback_permission_required");
      }
    };

    initConnection();

    return () => {
      isMounted = false;
      if (permissionStatusRef) {
        permissionStatusRef.onchange = null;
      }
    };
  }, [authLoading, refreshOllama]);

  // Scroll to bottom when messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  // Cloud inference execution for Automatic and My API Keys Only modes
  const executeCloudInference = async (
    promptText: string,
    scope: "eligible_pool" | "owner_only"
  ) => {
    if (!promptText.trim() || isStreaming) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: promptText.trim(),
    };

    const assistantMessageId = `asst-${Date.now()}`;
    const initialAssistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
    };

    const updatedMessages = [...messages, userMessage];
    setMessages([...updatedMessages, initialAssistantMessage]);
    setPromptInput("");
    setIsStreaming(true);
    setUsageStats(null);
    setLastRouteHeaders(null);

    const chatMessages: { role: string; content: string }[] = [];
    if (systemPrompt.trim()) {
      chatMessages.push({ role: "system", content: systemPrompt.trim() });
    }
    for (const msg of updatedMessages) {
      chatMessages.push({ role: msg.role, content: msg.content });
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await routingClient.streamChatCompletion(
        {
          messages: chatMessages,
          routing_mode: "auto",
          credential_scope: scope,
          model: selectedCloudModel || undefined,
          temperature,
        },
        {
          onRouteHeaders: (headers) => {
            setLastRouteHeaders(headers);
          },
          onDelta: (delta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId ? { ...m, content: m.content + delta } : m
              )
            );
          },
          onError: (err) => {
            const errorMsg = err instanceof Error ? err.message : "Inference error";
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? { ...m, content: `⚠️ Error during inference: ${errorMsg}` }
                  : m
              )
            );
          },
          onComplete: () => {
            setIsStreaming(false);
          },
        },
        { signal: controller.signal }
      );
    } catch (err) {
      if (isAbortError(err) || controller.signal.aborted) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId
              ? {
                  ...m,
                  content:
                    m.content +
                    (m.content ? "\n\n*(Generation stopped by user)*" : "*(Generation stopped)*"),
                }
              : m
          )
        );
      } else {
        const errorMsg = err instanceof Error ? err.message : "Failed to execute inference";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId ? { ...m, content: `⚠️ Error: ${errorMsg}` } : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };

  // Local loopback Ollama inference execution (100% private)
  const executeLocalInference = async (promptText: string) => {
    if (!promptText.trim() || isStreaming) return;
    if (!selectedModel || connectionState !== "connected") return;

    // Verify chat contribution requirement is satisfied before local execution
    if (entitlement && !entitlement.eligible) {
      setMessages((prev) => [
        ...prev,
        {
          id: `asst-${Date.now()}`,
          role: "assistant",
          content:
            "⚠️ Chat Contribution Required: Under RagBait platform policy, every approved user must contribute at least one valid free-tier cloud API key to the shared pool before accessing AI Chat (including private local models). Please visit Key Management to contribute a key.",
        },
      ]);
      return;
    }

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: promptText.trim(),
    };

    const assistantMessageId = `asst-${Date.now()}`;
    const initialAssistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
    };

    const updatedMessages = [...messages, userMessage];
    setMessages([...updatedMessages, initialAssistantMessage]);
    setPromptInput("");
    setIsStreaming(true);
    setUsageStats(null);

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
                  content:
                    m.content +
                    (m.content ? "\n\n*(Generation stopped by user)*" : "*(Generation stopped)*"),
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

  // Unified submission handler
  const handleSend = async () => {
    const trimmed = promptInput.trim();
    if (!trimmed || isStreaming) return;

    if (executionMode === "auto") {
      await executeCloudInference(trimmed, "eligible_pool");
    } else if (executionMode === "owner_only") {
      await executeCloudInference(trimmed, "owner_only");
    } else {
      await executeLocalInference(trimmed);
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
    setLastRouteHeaders(null);
  };

  const currentModelObj = models.find((m) => m.name === selectedModel);
  const currentModelIsHeavy = isHeavyModel(currentModelObj);

  if (authLoading || checkingEntitlement) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  // Mandatory Chat Contribution Gate Onboarding Screen
  // When an approved user has not contributed at least one qualifying free-tier API key,
  // show the contribution-required experience directly on the workspace.
  if (entitlement && !entitlement.eligible) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6 lg:px-8">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 sm:p-10 shadow-sm text-center">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-blue-50 text-blue-600">
            <Key className="h-8 w-8" />
          </div>

          <h1 className="mt-5 text-2xl font-bold text-gray-900 sm:text-3xl">
            AI Chat Contribution Required
          </h1>
          <p className="mt-2 text-base text-gray-600 max-w-xl mx-auto">
            Contribute one eligible free-tier cloud API key to unlock AI Chat.
          </p>

          <div className="mt-8 rounded-xl border border-blue-200 bg-blue-50/70 p-6 text-left text-sm text-blue-900 leading-relaxed max-w-2xl mx-auto">
            <div className="flex items-center gap-2 font-semibold text-blue-950 text-base">
              <Sparkles className="h-5 w-5 text-blue-600 shrink-0" />
              <span>Mandatory Free-Tier Shared Pool Policy</span>
            </div>
            <p className="mt-3 text-blue-800 leading-relaxed">
              RagBait uses a shared pool of eligible free-tier cloud API credentials. To use AI Chat,
              every participating student must contribute at least one approved free-tier API key for
              other students to use. Your local Ollama models and paid API keys remain private and do
              not count as contributions.
            </p>

            <div className="mt-4 rounded-lg bg-blue-100/80 p-3.5 text-xs text-blue-950 border border-blue-200">
              <div className="font-semibold text-blue-900 mb-1">Current Contribution Status:</div>
              <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-blue-700 shrink-0" />
                <span>
                  {entitlement.reason_code === "NO_CREDENTIAL_CONTRIBUTED" &&
                    "No qualifying key contributed yet. Please add a supported free-tier API key (e.g. Gemini or Groq)."}
                  {entitlement.reason_code === "VALIDATION_REQUIRED" &&
                    "Key validation required. Please run the validation probe in Key Management."}
                  {entitlement.reason_code === "SHARING_CONSENT_REQUIRED" &&
                    "Sharing consent required. Please enable community sharing on your key."}
                  {entitlement.reason_code === "CREDENTIAL_INACTIVE" &&
                    "Credential inactive. Please enable your key in Key Management."}
                  {entitlement.reason_code === "PROVIDER_NOT_ELIGIBLE_FOR_SHARING" &&
                    "Provider not approved for free-tier community sharing."}
                  {entitlement.reason_code !== "NO_CREDENTIAL_CONTRIBUTED" &&
                    entitlement.reason_code !== "VALIDATION_REQUIRED" &&
                    entitlement.reason_code !== "SHARING_CONSENT_REQUIRED" &&
                    entitlement.reason_code !== "CREDENTIAL_INACTIVE" &&
                    entitlement.reason_code !== "PROVIDER_NOT_ELIGIBLE_FOR_SHARING" &&
                    "A qualifying free-tier cloud credential is required before accessing AI Chat."}
                </span>
              </div>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Link
                href="/credentials"
                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 transition-colors"
              >
                <Key className="h-4 w-4" />
                Manage API Credentials
              </Link>
              <button
                type="button"
                onClick={async () => {
                  const supabase = createClient();
                  const {
                    data: { session },
                  } = await supabase.auth.getSession();
                  if (session) {
                    await checkEntitlement(session.access_token);
                  }
                }}
                disabled={checkingEntitlement}
                className="inline-flex items-center gap-1.5 rounded-lg border border-blue-300 bg-white px-4 py-2.5 text-sm font-medium text-blue-700 shadow-sm hover:bg-blue-50 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${checkingEntitlement ? "animate-spin" : ""}`} />
                Check Contribution Status
              </button>
            </div>
          </div>

          <div className="mt-8 text-xs text-gray-500">
            Private local Ollama models and paid API keys remain 100% private to you and never satisfy the contribution gate.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Page Header */}
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">AI Inference Workspace</h1>
            {executionMode === "auto" && (
              <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 border border-indigo-200">
                <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
                Automatic (Recommended)
              </span>
            )}
            {executionMode === "owner_only" && (
              <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 px-2.5 py-0.5 text-xs font-semibold text-purple-700 border border-purple-200">
                <Key className="h-3.5 w-3.5 text-purple-600" />
                My API Keys Only
              </span>
            )}
            {executionMode === "private" && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200">
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                100% Private Local
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-500">
            {executionMode === "auto" &&
              "Automatically selects an eligible cloud API credential using your own keys and the approved shared free-tier pool."}
            {executionMode === "owner_only" &&
              "Use only your own cloud API credentials. Requests will never use another student's credential."}
            {executionMode === "private" &&
              "Use Ollama models installed on this computer. Your local models and computing resources are never shared."}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {executionMode === "private" ? (
            <>
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
                <RefreshCw
                  className={`h-4 w-4 text-gray-500 ${isRefreshing ? "animate-spin" : ""}`}
                />
                Refresh
              </button>
            </>
          ) : (
            <Link
              href="/credentials"
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
            >
              <Key className="h-3.5 w-3.5 text-gray-500" />
              Manage API Keys
              <ArrowRight className="h-3.5 w-3.5 text-gray-400" />
            </Link>
          )}
        </div>
      </div>

      {/* Unified Compact Execution Mode Selector */}
      <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50/80 p-1.5">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-1.5" role="radiogroup" aria-label="Execution Mode">
          <button
            type="button"
            role="radio"
            aria-checked={executionMode === "auto"}
            onClick={() => setExecutionMode("auto")}
            className={`flex flex-col items-start rounded-lg p-3 text-left transition-all ${
              executionMode === "auto"
                ? "bg-white shadow-xs border border-indigo-200 text-indigo-950 font-semibold"
                : "text-gray-600 hover:bg-white/60 hover:text-gray-900 border border-transparent"
            }`}
          >
            <div className="flex items-center gap-2">
              <Sparkles className={`h-4 w-4 ${executionMode === "auto" ? "text-indigo-600" : "text-gray-400"}`} />
              <span className="text-sm font-medium">Automatic (Recommended)</span>
            </div>
            <p className="mt-1 text-xs text-gray-500 font-normal leading-relaxed">
              Your keys + approved shared free-tier pool. Zero setup required.
            </p>
          </button>

          <button
            type="button"
            role="radio"
            aria-checked={executionMode === "owner_only"}
            onClick={() => setExecutionMode("owner_only")}
            className={`flex flex-col items-start rounded-lg p-3 text-left transition-all ${
              executionMode === "owner_only"
                ? "bg-white shadow-xs border border-purple-200 text-purple-950 font-semibold"
                : "text-gray-600 hover:bg-white/60 hover:text-gray-900 border border-transparent"
            }`}
          >
            <div className="flex items-center gap-2">
              <Key className={`h-4 w-4 ${executionMode === "owner_only" ? "text-purple-600" : "text-gray-400"}`} />
              <span className="text-sm font-medium">My API Keys Only</span>
            </div>
            <p className="mt-1 text-xs text-gray-500 font-normal leading-relaxed">
              Use only your own cloud API credentials. Excludes shared keys.
            </p>
          </button>

          <button
            type="button"
            role="radio"
            aria-checked={executionMode === "private"}
            onClick={() => setExecutionMode("private")}
            className={`flex flex-col items-start rounded-lg p-3 text-left transition-all ${
              executionMode === "private"
                ? "bg-white shadow-xs border border-emerald-200 text-emerald-950 font-semibold"
                : "text-gray-600 hover:bg-white/60 hover:text-gray-900 border border-transparent"
            }`}
          >
            <div className="flex items-center gap-2">
              <Cpu className={`h-4 w-4 ${executionMode === "private" ? "text-emerald-600" : "text-gray-400"}`} />
              <span className="text-sm font-medium">Private Local (This Computer)</span>
            </div>
            <p className="mt-1 text-xs text-gray-500 font-normal leading-relaxed">
              Direct loopback to your local Ollama. Compute is never shared.
            </p>
          </button>
        </div>
      </div>

      {/* Private Local Connection Status Banners & Guide */}
      {executionMode === "private" && (
        <>
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
                Direct browser-to-local loopback active • Never shared
              </span>
            </div>
          ) : connectionState === "loopback_permission_required" ? (
            <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
              <div className="flex items-start gap-3">
                <Lock className="h-5 w-5 text-blue-600 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <p className="font-semibold text-blue-900">
                    Browser Local Network Permission Required
                  </p>
                  <p className="mt-1 text-xs text-blue-800 leading-relaxed">
                    Modern Chrome requires an explicit user gesture to initiate connection to your
                    local loopback address (
                    <code className="bg-blue-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>
                    ).
                  </p>
                  <div className="mt-3 flex items-center gap-3">
                    <button
                      onClick={refreshOllama}
                      disabled={isRefreshing}
                      className="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-blue-700 disabled:opacity-50"
                    >
                      {isRefreshing ? "Connecting..." : "Connect to Local Ollama"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : connectionState === "loopback_permission_denied" ? (
            <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
              <div className="flex items-start gap-3">
                <ShieldAlert className="h-5 w-5 text-rose-600 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <p className="font-semibold text-rose-900">
                    Loopback Access Blocked by Browser
                  </p>
                  <p className="mt-1 text-xs text-rose-800 leading-relaxed">
                    Your browser denied permission to access{" "}
                    <code className="bg-rose-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>.
                    To resolve this: Click the Site Settings icon (tune or lock symbol) in your
                    browser address bar, set <strong>Local Network</strong> or{" "}
                    <strong>Insecure content</strong> to <strong>Allow</strong>, then reload this page.
                  </p>
                  <div className="mt-2.5 flex items-center gap-3">
                    <button
                      onClick={refreshOllama}
                      disabled={isRefreshing}
                      className="rounded-md bg-rose-700 px-3 py-1 text-xs font-medium text-white hover:bg-rose-800"
                    >
                      Check Permission Again
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : connectionState === "connection_failed" ? (
            <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <p className="font-semibold text-amber-900">Cannot Connect to Ollama</p>
                  <p className="mt-1 text-xs text-amber-800 leading-relaxed">
                    Browser could not reach Ollama at{" "}
                    <code className="bg-amber-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>.
                    This usually occurs when{" "}
                    <code className="font-mono bg-amber-100 px-1 py-0.5 rounded">OLLAMA_ORIGINS</code>{" "}
                    is not configured or loopback traffic is blocked.
                  </p>
                  {connectionError && (
                    <p className="mt-1 text-xs text-amber-700 font-mono">{connectionError}</p>
                  )}
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      onClick={() => setShowSetupGuide(true)}
                      className="rounded-md bg-amber-600 px-3 py-1 text-xs font-semibold text-white hover:bg-amber-700"
                    >
                      View Setup Guide
                    </button>
                    <button
                      onClick={refreshOllama}
                      disabled={isRefreshing}
                      className="rounded-md border border-amber-300 bg-white px-3 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-50 disabled:opacity-50"
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
                    The Ollama service returned an error or is offline at{" "}
                    <code className="bg-rose-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>.
                    Please ensure Ollama is installed and running.
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
          ) : null}

          {/* Setup & CORS Guide Modal/Drawer */}
          {showSetupGuide && (
            <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50/80 p-5 text-xs text-blue-900">
              <div className="flex items-center justify-between pb-3 border-b border-blue-200">
                <div className="flex items-center gap-2 font-semibold text-sm text-blue-950">
                  <Terminal className="h-4 w-4 text-blue-600" />
                  <span>Ollama Setup & CORS Configuration Guide</span>
                </div>
                <button
                  onClick={() => setShowSetupGuide(false)}
                  className="text-blue-600 hover:text-blue-800 font-medium"
                >
                  Close
                </button>
              </div>

              <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Windows */}
                <div className="rounded-lg bg-white p-4 border border-blue-100 shadow-xs">
                  <p className="font-semibold text-gray-900 mb-1.5">Windows</p>
                  <p className="text-gray-600 mb-2 leading-relaxed">
                    Set user environment variable and restart Ollama from tray:
                  </p>
                  <pre className="overflow-x-auto rounded bg-gray-900 p-2 text-emerald-400 font-mono text-[11px] select-all">
                    {`[System.Environment]::SetEnvironmentVariable(
  "OLLAMA_ORIGINS",
  "http://localhost:3000,http://127.0.0.1:3000",
  "User"
)`}
                  </pre>
                  <p className="mt-2 text-gray-500">Quit Ollama from taskbar tray, then relaunch.</p>
                </div>

                {/* macOS */}
                <div className="rounded-lg bg-white p-4 border border-blue-100 shadow-xs">
                  <p className="font-semibold text-gray-900 mb-1.5">macOS</p>
                  <p className="text-gray-600 mb-2 leading-relaxed">
                    Configure environment via launchctl and restart Ollama:
                  </p>
                  <pre className="overflow-x-auto rounded bg-gray-900 p-2 text-emerald-400 font-mono text-[11px] select-all">
                    {`launchctl setenv OLLAMA_ORIGINS "http://localhost:3000,http://127.0.0.1:3000"`}
                  </pre>
                  <p className="mt-2 text-gray-500">Restart Ollama via menu bar.</p>
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
            </div>
          )}
        </>
      )}

      {/* Main Workspace Grid: Single Conversation Playground across all 3 modes */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Column: Unified Playground (3 cols) */}
        <div className="lg:col-span-3 flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          {/* Controls Bar */}
          <div className="flex flex-wrap items-center justify-between border-b border-gray-200 bg-gray-50/80 px-4 py-3 gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <label
                htmlFor={executionMode === "private" ? "model-select" : "routed-model-select"}
                className="text-xs font-semibold uppercase tracking-wider text-gray-500"
              >
                {executionMode === "private" ? "Local Model:" : "Target Model:"}
              </label>

              {executionMode === "private" ? (
                models.length > 0 ? (
                  <select
                    id="model-select"
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                    disabled={isStreaming}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 shadow-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    {models.map((m) => {
                      const sizeStr = formatModelSize(m.size);
                      const paramStr = m.details?.parameter_size || "";
                      const heavy = isHeavyModel(m);
                      const parts: string[] = [];
                      if (sizeStr) parts.push(sizeStr);
                      if (paramStr) parts.push(paramStr);
                      if (heavy) parts.push("⚠️ Heavy");
                      const metaSuffix = parts.length > 0 ? ` (${parts.join(" · ")})` : "";

                      return (
                        <option key={m.name} value={m.name}>
                          {m.name}
                          {metaSuffix}
                        </option>
                      );
                    })}
                  </select>
                ) : (
                  <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded border border-amber-200">
                    {connectionState === "connected"
                      ? "No local models found (run `ollama pull llama3.2`)"
                      : "Connect to Ollama to view models"}
                  </span>
                )
              ) : (
                <select
                  id="routed-model-select"
                  value={selectedCloudModel}
                  onChange={(e) => setSelectedCloudModel(e.target.value)}
                  disabled={isStreaming}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 shadow-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                >
                  <option value="">Auto (Server Selected Default)</option>
                  <option value="gemini-3.8-flash">Gemini 3.8 Flash (Free-Tier Eligible)</option>
                  <option value="gemini-3.5-flash-lite">Gemini 3.5 Flash-Lite (Free-Tier Eligible)</option>
                  <option value="openai/gpt-oss-120b">OpenAI GPT-OSS 120B (Groq Free Tier)</option>
                  <option value="openrouter/free">OpenRouter Free Tier</option>
                  <option value="qwen/qwen3.8-27b">Qwen 3.8 27B (Groq Preview)</option>
                </select>
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

          {/* Large Model Notice for heavy local models */}
          {executionMode === "private" && currentModelIsHeavy && (
            <div className="border-b border-amber-200 bg-amber-50/80 px-4 py-2 text-xs text-amber-900 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600 shrink-0" />
                <span>
                  <strong>
                    Large Model Notice (
                    {currentModelObj?.size ? formatModelSize(currentModelObj.size) : "High RAM"}
                    ):
                  </strong>{" "}
                  This model requires substantial VRAM or free system RAM. If generation fails or
                  crashes Ollama, switch to a lighter model (e.g. 3B or 0.6B).
                </span>
              </div>
            </div>
          )}

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
                  placeholder="Instructions for the AI assistant..."
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

          {/* Conversation Messages Display (Preserved across mode switches) */}
          <div className="flex-1 p-4 min-h-[380px] max-h-[550px] overflow-y-auto space-y-4">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center text-gray-400">
                <Sparkles className="h-12 w-12 stroke-[1.25] text-gray-300 mb-3" />
                <p className="text-sm font-medium text-gray-600">AI Inference Playground</p>
                <p className="mt-1 text-xs text-gray-400 max-w-sm">
                  {executionMode === "auto" &&
                    "Type a prompt to run inference using intelligent multi-provider routing and the shared free-tier pool."}
                  {executionMode === "owner_only" &&
                    "Type a prompt to run inference using strictly your own cloud API credentials."}
                  {executionMode === "private" &&
                    "Type a prompt to interact with local Ollama models on this computer. Zero prompt data leaves your device."}
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
                    {msg.role === "user"
                      ? "You"
                      : executionMode === "private"
                      ? selectedModel || "Local Assistant"
                      : lastRouteHeaders?.selectedModel || selectedCloudModel || "AI Assistant"}
                  </div>

                  <div
                    className={`rounded-2xl px-4 py-3 text-sm max-w-[85%] leading-relaxed ${
                      msg.role === "user"
                        ? "bg-blue-600 text-white rounded-tr-xs"
                        : "bg-gray-100 text-gray-800 rounded-tl-xs border border-gray-200/80"
                    }`}
                  >
                    <div className="whitespace-pre-wrap font-sans">
                      {msg.content || (isStreaming && msg.role === "assistant" ? "..." : "")}
                    </div>
                  </div>
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Composer */}
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
                disabled={
                  isStreaming ||
                  (executionMode === "private" &&
                    (connectionState !== "connected" || models.length === 0))
                }
                placeholder={
                  executionMode === "auto"
                    ? "Ask AI assistant (Automatic)... (Enter to send, Shift+Enter for newline)"
                    : executionMode === "owner_only"
                    ? "Ask AI assistant (My API Keys)... (Enter to send, Shift+Enter for newline)"
                    : connectionState === "connected" && models.length > 0
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
                      (executionMode === "private" &&
                        (connectionState !== "connected" || models.length === 0))
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

        {/* Right Column: Execution Mode Info & Telemetry (1 col) */}
        <div className="flex flex-col gap-5">
          {/* Mode Card */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2 pb-3 border-b border-gray-100">
              <Zap className="h-4 w-4 text-amber-500" />
              <h3 className="text-sm font-semibold text-gray-900">Active Execution Mode</h3>
            </div>

            <div className="mt-3 space-y-2 text-xs">
              <div className="flex justify-between py-1 border-b border-gray-50">
                <span className="text-gray-500">Preference</span>
                <span className="font-semibold text-gray-800">
                  {executionMode === "auto" && "Automatic"}
                  {executionMode === "owner_only" && "My API Keys"}
                  {executionMode === "private" && "Private Local"}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-gray-50">
                <span className="text-gray-500">Resource Privacy</span>
                <span className="font-medium text-emerald-600">
                  {executionMode === "private"
                    ? "100% On-Device"
                    : executionMode === "owner_only"
                    ? "Owner-Only Cloud"
                    : "Shared Pool / Private"}
                </span>
              </div>
              {lastRouteHeaders?.selectedSource && (
                <div className="flex justify-between py-1 border-b border-gray-50">
                  <span className="text-gray-500">Selected Source</span>
                  <span className="font-mono text-gray-700">
                    {lastRouteHeaders.selectedSource}
                  </span>
                </div>
              )}
              {lastRouteHeaders?.selectedModel && (
                <div className="flex justify-between py-1 border-b border-gray-50">
                  <span className="text-gray-500">Routed Model</span>
                  <span className="font-mono text-gray-700">
                    {lastRouteHeaders.selectedModel}
                  </span>
                </div>
              )}
              {lastRouteHeaders?.decisionReason && (
                <div className="flex justify-between py-1 border-b border-gray-50">
                  <span className="text-gray-500">Decision Reason</span>
                  <span className="font-mono text-gray-700">
                    {lastRouteHeaders.decisionReason}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Telemetry Card (For Local Ollama) */}
          {executionMode === "private" && usageStats && (
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2 pb-3 border-b border-gray-100">
                <Cpu className="h-4 w-4 text-emerald-500" />
                <h3 className="text-sm font-semibold text-gray-900">Local Telemetry</h3>
              </div>

              <div className="mt-3 space-y-2 text-xs">
                {usageStats.tokensPerSecond !== undefined && (
                  <div className="flex justify-between py-1 border-b border-gray-50">
                    <span className="text-gray-500">Generation Speed</span>
                    <span className="font-mono font-medium text-gray-800">
                      {usageStats.tokensPerSecond.toFixed(1)} tokens/s
                    </span>
                  </div>
                )}
                {usageStats.evalCount !== undefined && (
                  <div className="flex justify-between py-1 border-b border-gray-50">
                    <span className="text-gray-500">Tokens Generated</span>
                    <span className="font-mono text-gray-800">{usageStats.evalCount}</span>
                  </div>
                )}
                {usageStats.totalDurationMs !== undefined && (
                  <div className="flex justify-between py-1 border-b border-gray-50">
                    <span className="text-gray-500">Total Duration</span>
                    <span className="font-mono text-gray-800">
                      {(usageStats.totalDurationMs / 1000).toFixed(2)}s
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Privacy Guarantee Card */}
          <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-4 text-xs text-emerald-900">
            <div className="flex items-center gap-2 font-semibold text-emerald-800 mb-1.5">
              <ShieldCheck className="h-4 w-4 text-emerald-600" />
              <span>Strict Privacy Guarantee</span>
            </div>
            <p className="text-emerald-700 leading-relaxed">
              Local Ollama models and student computing resources are strictly private and never
              shared across the community. Only explicitly contributed free-tier cloud credentials
              power the shared pool.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
