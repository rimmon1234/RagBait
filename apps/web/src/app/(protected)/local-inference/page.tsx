"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Compass,
  Cpu,
  HelpCircle,
  Loader2,
  Lock,
  Network,
  RefreshCw,
  Send,
  Settings2,
  Share2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Users,
  Zap,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { isSecureLoopbackContext, queryLoopbackPermission } from "@/lib/local-inference/lna-permissions";
import { ollamaClient } from "@/lib/local-inference/ollama-client";
import { isAbortError } from "@/lib/local-inference/ollama-errors";
import { CommunityConsentModal } from "@/components/CommunityConsentModal";
import {
  cancelCommunityJob,
  listAvailableCommunityModels,
  streamCommunityJob,
  submitCommunityJob,
} from "@/lib/community-client";
import {
  previewRoute,
  streamChatCompletion,
  type RouteHeaders,
} from "@/lib/routing-client";
import type { CommunityAvailableModel } from "@/types/community";
import type {
  LoopbackPermissionState,
  OllamaChatMessage,
  OllamaConnectionState,
  OllamaTagModel,
  OllamaUsageStats,
} from "@/lib/local-inference/ollama-types";

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
  if (pSize.includes("14b") || pSize.includes("13b") || pSize.includes("32b") || pSize.includes("70b")) {
    return true;
  }
  return false;
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
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a helpful, precise academic and coding assistant running locally on the student's machine."
  );
  const [temperature, setTemperature] = useState(0.7);

  // Mode switcher: "private" | "community" | "auto" | "cloud_only"
  const [inferenceMode, setInferenceMode] = useState<"private" | "community" | "auto" | "cloud_only">("private");
  const [communityOptIn, setCommunityOptIn] = useState(false);
  const [selectedCloudModel, setSelectedCloudModel] = useState<string>("");
  const [lastRouteHeaders, setLastRouteHeaders] = useState<RouteHeaders | null>(null);

  // Community inference state
  const [communityModels, setCommunityModels] = useState<CommunityAvailableModel[]>([]);
  const [selectedCommunityModel, setSelectedCommunityModel] = useState<string>("");
  const [loadingCommunityModels, setLoadingCommunityModels] = useState(false);
  const [communityConsentAccepted, setCommunityConsentAccepted] = useState(false);
  const [showConsentModal, setShowConsentModal] = useState(false);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const refreshCommunityModels = useCallback(async () => {
    try {
      setLoadingCommunityModels(true);
      const list = await listAvailableCommunityModels();
      setCommunityModels(list);
      if (list.length > 0) {
        setSelectedCommunityModel((prev) => prev || list[0].model_name);
      }
    } catch (err) {
      console.error("Failed to fetch community models", err);
    } finally {
      setLoadingCommunityModels(false);
    }
  }, []);

  useEffect(() => {
    if (inferenceMode === "community") {
      refreshCommunityModels();
    }
  }, [inferenceMode, refreshCommunityModels]);

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

  // Initialize permission state and determine whether auto-connect is permitted
  useEffect(() => {
    if (authLoading) return;

    let permissionStatusRef: PermissionStatus | null = null;
    let isMounted = true;

    const initConnection = async () => {
      // 1. Check secure context requirement
      if (!isSecureLoopbackContext()) {
        if (isMounted) setConnectionState("insecure_context");
        return;
      }

      // 2. Query Chrome Local Network Access (LNA) permission
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
        // Only granted permission allows automatic probing on page load
        refreshOllama();
      } else if (permResult.state === "denied") {
        // Explicitly denied: show remediation instructions
        setConnectionState("loopback_permission_denied");
      } else {
        // 'prompt' OR 'unsupported' (or query threw):
        // According to Chrome Local Network Access guidance, when granular permission
        // descriptor is unsupported or throws, treat it as prompt: require explicit user gesture!
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

  const executeCommunityInference = async (promptText: string) => {
    if (!promptText.trim() || isStreaming || !selectedCommunityModel) {
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
      const submitRes = await submitCommunityJob({
        model_name: selectedCommunityModel,
        messages: chatMessages,
        params: { temperature },
        consent_accepted: true,
      });

      setActiveJobId(submitRes.job_id);

      await streamCommunityJob(
        submitRes.job_id,
        {
          onChunk: (chunk) => {
            if (chunk.delta) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMessageId ? { ...m, content: m.content + chunk.delta } : m
                )
              );
            }
            if (chunk.done && (chunk.eval_count || chunk.total_duration_ms)) {
              const durMs = chunk.total_duration_ms || 1;
              const tokSec = chunk.eval_count ? ((chunk.eval_count / durMs) * 1000).toFixed(1) : "0";
              setUsageStats({
                totalDurationMs: durMs,
                loadDurationMs: 0,
                promptEvalCount: chunk.prompt_eval_count || 0,
                promptEvalDurationMs: 0,
                evalCount: chunk.eval_count || 0,
                evalDurationMs: durMs,
                tokensPerSecond: parseFloat(tokSec),
              });
            }
          },
          onError: (err) => {
            const errorMsg = err instanceof Error ? err.message : "Community stream failure";
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? { ...m, content: `⚠️ Error during community inference: ${errorMsg}` }
                  : m
              )
            );
          },
          onComplete: () => {
            setIsStreaming(false);
            setActiveJobId(null);
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
                  content: m.content + (m.content ? "\n\n*(Generation stopped by user)*" : "*(Generation stopped)*"),
                }
              : m
          )
        );
      } else {
        const errorMsg = err instanceof Error ? err.message : "Failed to dispatch community inference job";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId
              ? { ...m, content: `⚠️ Error: ${errorMsg}` }
              : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
      setActiveJobId(null);
    }
  };

  const executeRoutedInference = async (promptText: string, mode: "auto" | "cloud_only") => {
    if (!promptText.trim() || isStreaming) {
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
      await streamChatCompletion(
        {
          messages: chatMessages,
          routing_mode: mode,
          model: selectedCloudModel || undefined,
          temperature,
          community_opt_in: mode === "auto" ? communityOptIn : false,
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
            const errorMsg = err instanceof Error ? err.message : "Routing error";
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessageId
                  ? { ...m, content: `⚠️ Error during routed inference: ${errorMsg}` }
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
                  content: m.content + (m.content ? "\n\n*(Generation stopped by user)*" : "*(Generation stopped)*"),
                }
              : m
          )
        );
      } else {
        const errorMsg = err instanceof Error ? err.message : "Failed to execute routed inference";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMessageId
              ? { ...m, content: `⚠️ Error: ${errorMsg}` }
              : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };

  const handleCommunityOptInChange = (checked: boolean) => {
    if (checked && !communityConsentAccepted) {
      setShowConsentModal(true);
      return;
    }
    setCommunityOptIn(checked);
  };

  const handleConsentConfirm = () => {
    setCommunityConsentAccepted(true);
    setShowConsentModal(false);
    if (inferenceMode === "auto") {
      setCommunityOptIn(true);
    }
    if (pendingPrompt) {
      const p = pendingPrompt;
      setPendingPrompt(null);
      if (inferenceMode === "community") {
        executeCommunityInference(p);
      } else if (inferenceMode === "auto") {
        executeRoutedInference(p, "auto");
      }
    }
  };

  // Handle generation submission
  const handleSend = async () => {
    const trimmed = promptInput.trim();
    if (!trimmed || isStreaming) return;

    if (inferenceMode === "auto" || inferenceMode === "cloud_only") {
      await executeRoutedInference(trimmed, inferenceMode);
      return;
    }

    if (inferenceMode === "community") {
      if (!selectedCommunityModel) return;
      if (!communityConsentAccepted) {
        setPendingPrompt(trimmed);
        setShowConsentModal(true);
        return;
      }
      await executeCommunityInference(trimmed);
      return;
    }

    if (!selectedModel || connectionState !== "connected") {
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

  const handleStop = async () => {
    if (activeJobId) {
      try {
        await cancelCommunityJob(activeJobId);
      } catch {
        // Ignored
      }
      setActiveJobId(null);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  const handleClear = () => {
    handleStop();
    setMessages([]);
    setUsageStats(null);
  };

  const currentModelObj = models.find((m) => m.name === selectedModel);
  const currentModelIsHeavy = isHeavyModel(currentModelObj);

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
      <div className="mb-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">
              {inferenceMode === "private"
                ? "Private Local Inference"
                : inferenceMode === "community"
                ? "Community Local Inference"
                : inferenceMode === "auto"
                ? "Intelligent Inference Router"
                : "Cloud LLM Gateway"}
            </h1>
            {inferenceMode === "private" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200">
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                100% Private Loopback
              </span>
            ) : inferenceMode === "community" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-semibold text-blue-700 border border-blue-200">
                <Network className="h-3.5 w-3.5 text-blue-600" />
                Shared Community Node
              </span>
            ) : inferenceMode === "auto" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 border border-indigo-200">
                <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
                Intelligent Router (Auto)
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 px-2.5 py-0.5 text-xs font-semibold text-purple-700 border border-purple-200">
                <Compass className="h-3.5 w-3.5 text-purple-600" />
                Cloud Only
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-500">
            {inferenceMode === "private"
              ? "Execute open-source models on your local hardware via Ollama. Zero server routing, zero latency cost, and zero prompt data leaves your device."
              : inferenceMode === "community"
              ? "Submit inference requests to approved peer machines running Ollama. Prompts are ephemeral and never stored in server databases."
              : inferenceMode === "auto"
              ? "Unified intelligent routing layer dynamically selecting optimal free, cloud, or community compute with deterministic privacy and quota enforcement."
              : "Direct inference through authenticated cloud providers with quota enforcement and budget limits."}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {inferenceMode === "private" ? (
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
                <RefreshCw className={`h-4 w-4 text-gray-500 ${isRefreshing ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </>
          ) : inferenceMode === "community" ? (
            <>
              <Link
                href="/community-nodes"
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
              >
                <Users className="h-4 w-4 text-gray-500" />
                Manage Nodes
                <ArrowRight className="h-3.5 w-3.5 text-gray-400" />
              </Link>
              <button
                onClick={refreshCommunityModels}
                disabled={loadingCommunityModels}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 text-gray-500 ${loadingCommunityModels ? "animate-spin" : ""}`} />
                Refresh Models
              </button>
            </>
          ) : (
            <Link
              href="/credentials"
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
            >
              Manage Cloud Keys
              <ArrowRight className="h-3.5 w-3.5 text-gray-400" />
            </Link>
          )}
        </div>
      </div>

      {/* Mode Switcher Tabs */}
      <div className="mb-6 flex items-center gap-2 border-b border-gray-200 overflow-x-auto">
        <button
          onClick={() => {
            setInferenceMode("private");
            handleClear();
          }}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap ${
            inferenceMode === "private"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          <Cpu className="h-4 w-4" />
          Private (This Computer)
        </button>
        <button
          onClick={() => {
            setInferenceMode("community");
            handleClear();
          }}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap ${
            inferenceMode === "community"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          <Network className="h-4 w-4" />
          Community Network (Peer Nodes)
        </button>
        <button
          onClick={() => {
            setInferenceMode("auto");
            handleClear();
          }}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap ${
            inferenceMode === "auto"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          <Sparkles className="h-4 w-4" />
          Auto (Intelligent Routing)
        </button>
        <button
          onClick={() => {
            setInferenceMode("cloud_only");
            handleClear();
          }}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap ${
            inferenceMode === "cloud_only"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          <Compass className="h-4 w-4" />
          Cloud Only
        </button>
      </div>

      {/* Community Status Banner */}
      {inferenceMode === "community" && (
        <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50/70 p-4 text-sm text-blue-900">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Network className="h-5 w-5 text-blue-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-blue-900">
                  Shared Community Inference Network Active
                </p>
                <p className="mt-1 text-xs text-blue-800 leading-relaxed">
                  Prompts submitted here are processed ephemerally on approved student machines running the RagBait Node Agent.
                  Zero prompt or completion text is ever stored in databases or permanent server logs.
                </p>
              </div>
            </div>
            <Link
              href="/community-nodes"
              className="shrink-0 text-xs font-semibold text-blue-700 hover:text-blue-900 underline flex items-center gap-1"
            >
              Share Your GPU <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
        </div>
      )}

      {/* Auto Routing Status Banner */}
      {inferenceMode === "auto" && (
        <div className="mb-6 rounded-lg border border-indigo-200 bg-indigo-50/70 p-4 text-sm text-indigo-900">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Sparkles className="h-5 w-5 text-indigo-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-indigo-900">
                  Intelligent Multi-Provider Routing Active
                </p>
                <p className="mt-1 text-xs text-indigo-800 leading-relaxed">
                  Requests automatically evaluate eligible cloud and free-tier models with fallback protection. Community peer compute is only used when the opt-in checkbox below is enabled.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Cloud Only Status Banner */}
      {inferenceMode === "cloud_only" && (
        <div className="mb-6 rounded-lg border border-purple-200 bg-purple-50/70 p-4 text-sm text-purple-900">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Compass className="h-5 w-5 text-purple-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-purple-900">
                  Cloud Dedicated Inference Mode
                </p>
                <p className="mt-1 text-xs text-purple-800 leading-relaxed">
                  Requests strictly target approved cloud providers (Gemini, Claude, OpenRouter). Requests will never be routed to community peer nodes under any circumstances.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Private Connection Status Banners & Guide */}
      {inferenceMode === "private" && (
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
            Direct browser-to-local loopback active
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
                Modern Chrome requires an explicit user click to initiate connection to your local loopback address (<code className="bg-blue-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>).
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
                Your browser denied permission to access <code className="bg-rose-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>. To resolve this: Click the Site Settings icon (tune or lock symbol) in your browser address bar, set <strong>Local Network</strong> or <strong>Insecure content</strong> to <strong>Allow</strong>, then reload this page.
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
      ) : connectionState === "insecure_context" ? (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-semibold text-amber-900">Secure Context (HTTPS) Required</p>
              <p className="mt-1 text-xs text-amber-800 leading-relaxed">
                Modern browsers strictly require a secure HTTPS origin to communicate with local network services. For local development, access RagBait via <code className="bg-amber-100 px-1 py-0.5 rounded">http://localhost:3000</code> or <code className="bg-amber-100 px-1 py-0.5 rounded">http://127.0.0.1:3000</code>.
              </p>
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
                Unable to establish a connection with <code className="bg-amber-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>. The browser reported an opaque network failure. Please verify each of the following:
              </p>
              <ul className="mt-2 list-disc list-inside text-xs text-amber-900 space-y-1">
                <li>Ollama is running on your machine (<code className="bg-amber-100 px-1 py-0.5 rounded">ollama serve</code>).</li>
                <li><code className="bg-amber-100 px-1 py-0.5 rounded">OLLAMA_ORIGINS</code> is configured to allow this web origin.</li>
                <li>Browser Local Network Access is not blocked in Site Settings.</li>
              </ul>
              <div className="mt-3 flex items-center gap-3">
                <button
                  onClick={refreshOllama}
                  disabled={isRefreshing}
                  className="rounded-md bg-amber-700 px-3 py-1 text-xs font-medium text-white hover:bg-amber-800"
                >
                  Retry Connection
                </button>
                <button
                  onClick={() => setShowSetupGuide(true)}
                  className="text-xs font-medium text-amber-900 underline hover:no-underline"
                >
                  View Setup Guide
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
                The Ollama service returned an error or is offline at <code className="bg-rose-100 px-1 py-0.5 rounded">http://127.0.0.1:11434</code>. Please ensure Ollama is installed and running.
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
      </>
      )}

      {/* Main Grid: Playground & Local Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Column: Playground (3 cols) */}
        <div className="lg:col-span-3 flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
          {/* Controls Bar */}
          <div className="flex flex-wrap items-center justify-between border-b border-gray-200 bg-gray-50/80 px-4 py-3 gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <label
                htmlFor={
                  inferenceMode === "auto" || inferenceMode === "cloud_only"
                    ? "routed-model-select"
                    : inferenceMode === "community"
                    ? "community-model-select"
                    : "model-select"
                }
                className="text-xs font-semibold uppercase tracking-wider text-gray-500"
              >
                {inferenceMode === "auto" || inferenceMode === "cloud_only"
                  ? "Target Model:"
                  : inferenceMode === "community"
                  ? "Community Model:"
                  : "Local Model:"}
              </label>

              {inferenceMode === "auto" || inferenceMode === "cloud_only" ? (
                <>
                  <select
                    id="routed-model-select"
                    value={selectedCloudModel}
                    onChange={(e) => setSelectedCloudModel(e.target.value)}
                    disabled={isStreaming}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 shadow-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="">Auto (Server Selected Default)</option>
                    <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                    <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                    <option value="claude-3-5-sonnet">Claude 3.5 Sonnet</option>
                    <option value="openrouter/free">OpenRouter Free Tier</option>
                  </select>

                  {inferenceMode === "auto" && (
                    <label className="flex items-center gap-2 text-xs font-medium text-gray-700 cursor-pointer ml-2">
                      <input
                        type="checkbox"
                        id="community-opt-in-checkbox"
                        checked={communityOptIn}
                        onChange={(e) => handleCommunityOptInChange(e.target.checked)}
                        disabled={isStreaming}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                      <span>Consider Community Nodes (Peer Compute)</span>
                    </label>
                  )}
                </>
              ) : inferenceMode === "community" ? (
                communityModels.length > 0 ? (
                  <select
                    id="community-model-select"
                    value={selectedCommunityModel}
                    onChange={(e) => setSelectedCommunityModel(e.target.value)}
                    disabled={isStreaming}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 shadow-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    {communityModels.map((m) => (
                      <option key={m.model_name} value={m.model_name}>
                        {m.model_name} ({m.online_nodes_count} node{m.online_nodes_count > 1 ? "s" : ""} online)
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="text-xs text-amber-700 bg-amber-50 px-2.5 py-1 rounded border border-amber-200">
                    {loadingCommunityModels ? "Discovering community models..." : "No community nodes online"}
                  </span>
                )
              ) : models.length > 0 ? (
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
                        {m.name}{metaSuffix}
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

          {/* Large Model Notice Callout */}
          {currentModelIsHeavy && (
            <div className="border-b border-amber-200 bg-amber-50/80 px-4 py-2 text-xs text-amber-900 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600 shrink-0" />
                <span>
                  <strong>Large Model Notice ({currentModelObj?.size ? formatModelSize(currentModelObj.size) : "High RAM"}):</strong>{" "}
                  This model requires substantial VRAM or free system RAM. If generation fails or crashes Ollama, switch to a lighter model (e.g. 3B or 0.6B).
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
                    {msg.role === "user"
                      ? "You"
                      : inferenceMode === "auto" || inferenceMode === "cloud_only"
                        ? lastRouteHeaders?.selectedModel || selectedCloudModel || "Router Assistant"
                        : inferenceMode === "community"
                          ? selectedCommunityModel || "Community Assistant"
                          : selectedModel || "Assistant"}
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
                disabled={
                  isStreaming ||
                  (inferenceMode === "auto" || inferenceMode === "cloud_only"
                    ? false
                    : inferenceMode === "community"
                    ? communityModels.length === 0
                    : connectionState !== "connected" || models.length === 0)
                }
                placeholder={
                  inferenceMode === "auto"
                    ? `Ask intelligent router (auto source)... (Enter to send, Shift+Enter for newline)`
                    : inferenceMode === "cloud_only"
                    ? `Ask cloud LLM gateway... (Enter to send, Shift+Enter for newline)`
                    : inferenceMode === "community"
                    ? communityModels.length > 0
                      ? `Ask community node running ${selectedCommunityModel}... (Enter to send, Shift+Enter for newline)`
                      : "No community models currently available..."
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
                      (inferenceMode === "auto" || inferenceMode === "cloud_only"
                        ? false
                        : inferenceMode === "community"
                        ? communityModels.length === 0 || !selectedCommunityModel
                        : connectionState !== "connected" || models.length === 0)
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
              <h3 className="text-sm font-semibold text-gray-900">
                {inferenceMode === "auto" || inferenceMode === "cloud_only"
                  ? "Intelligent Routing Telemetry"
                  : inferenceMode === "community"
                  ? "Community Node Telemetry"
                  : "Device Telemetry"}
              </h3>
            </div>

            <div className="mt-3">
              <span className="inline-block rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 border border-emerald-200 mb-3">
                {inferenceMode === "auto"
                  ? "Autonomous Multi-Provider Dispatch"
                  : inferenceMode === "cloud_only"
                  ? "Direct Cloud LLM Dispatch"
                  : inferenceMode === "community"
                  ? "Device-Reported / Community Node"
                  : "Non-Billable / Device Native"}
              </span>

              {lastRouteHeaders && (
                <div className="mb-3 rounded-lg bg-gray-50 p-2.5 text-xs space-y-1.5 border border-gray-100">
                  <div className="flex justify-between">
                    <span className="text-gray-500 font-medium">Source:</span>
                    <span className="font-semibold text-gray-900 capitalize">{lastRouteHeaders.selectedSource || "cloud"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500 font-medium">Model:</span>
                    <span className="font-mono text-blue-700">{lastRouteHeaders.selectedModel || "default"}</span>
                  </div>
                  {lastRouteHeaders.decisionReason && (
                    <div className="pt-1 border-t border-gray-200 text-[11px] text-gray-600">
                      <span className="font-medium text-gray-700">Reason:</span> {lastRouteHeaders.decisionReason}
                    </div>
                  )}
                </div>
              )}

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
                  Run a prompt to view token metrics and generation throughput.
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

            {inferenceMode === "auto" ? (
              <ul className="mt-3 space-y-2 text-gray-600 leading-relaxed">
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Cloud-first routing with strict privacy boundary enforcement.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Community peer nodes evaluated ONLY if peer compute opt-in is enabled.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>All provider API keys encrypted in Credential Vault.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Idempotency-safe dispatch prevents duplicate billing or reservations.</span>
                </li>
              </ul>
            ) : inferenceMode === "cloud_only" ? (
              <ul className="mt-3 space-y-2 text-gray-600 leading-relaxed">
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Strictly zero routing to community peer nodes under any circumstances.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Full admission control and user spending budget enforcement.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Direct streaming via authenticated cloud provider endpoints.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Transparent cost settlement and usage accounting.</span>
                </li>
              </ul>
            ) : inferenceMode === "community" ? (
              <ul className="mt-3 space-y-2 text-gray-600 leading-relaxed">
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Ephemeral in-flight streaming via Redis Streams.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Zero prompt or output stored in PostgreSQL databases or logs.</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Cryptographically verified node authentication (Ed25519).</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0 mt-0.5" />
                  <span>Dual explicit consent required from contributor and requester.</span>
                </li>
              </ul>
            ) : (
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
            )}

            <div className="mt-3.5 pt-3 border-t border-gray-100 text-[11px] text-gray-500 leading-normal">
              <strong>Local Execution Notice:</strong> For guaranteed offline execution without cloud routing or external web search, run Ollama with <code className="bg-gray-100 px-1 py-0.5 rounded font-mono text-[10px]">OLLAMA_NO_CLOUD=1</code>.
            </div>
          </div>
        </div>
      </div>

      {/* Community Consent Modal */}
      <CommunityConsentModal
        isOpen={showConsentModal}
        modelName={selectedCommunityModel}
        onConfirm={handleConsentConfirm}
        onCancel={() => {
          setShowConsentModal(false);
          setPendingPrompt(null);
        }}
      />
    </div>
  );
}
