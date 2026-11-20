"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Check,
  Clock,
  Copy,
  Cpu,
  Layers,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Server,
  Shield,
  Trash2,
  X,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  createPairingCode,
  listOwnedNodes,
  pauseNode,
  resumeNode,
  revokeNode,
  updateNodeConfig,
  updateNodeModels,
} from "@/lib/community-client";
import type { CommunityNode, PairingCodeResponse } from "@/types/community";

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1000 * 1000 * 1000) {
    return `${(bytes / (1000 * 1000)).toFixed(0)} MB`;
  }
  return `${(bytes / (1000 * 1000 * 1000)).toFixed(1)} GB`;
}

export default function CommunityNodesPage() {
  const router = useRouter();
  const [nodes, setNodes] = useState<CommunityNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pairing Modal State
  const [isPairingModalOpen, setIsPairingModalOpen] = useState(false);
  const [pairingLoading, setPairingLoading] = useState(false);
  const [pairingData, setPairingData] = useState<PairingCodeResponse | null>(null);
  const [pairingCopied, setPairingCopied] = useState(false);
  const [pairingSecondsLeft, setPairingSecondsLeft] = useState(0);

  // Authenticate user
  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        router.push("/login");
      }
    });
  }, [router]);

  const loadNodes = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await listOwnedNodes();
      setNodes(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load community nodes");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNodes();
  }, [loadNodes]);

  // Pairing countdown timer
  useEffect(() => {
    if (!pairingData || pairingSecondsLeft <= 0) return;
    const interval = setInterval(() => {
      setPairingSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(interval);
  }, [pairingData, pairingSecondsLeft]);

  const handleOpenPairing = async () => {
    setIsPairingModalOpen(true);
    setPairingLoading(true);
    try {
      const data = await createPairingCode();
      setPairingData(data);
      setPairingSecondsLeft(data.ttl_seconds);
      setPairingCopied(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate pairing code");
    } finally {
      setPairingLoading(false);
    }
  };

  const handleCopyCode = () => {
    if (!pairingData) return;
    navigator.clipboard.writeText(pairingData.pairing_code);
    setPairingCopied(true);
    setTimeout(() => setPairingCopied(false), 2000);
  };

  const handleToggleSharing = async (node: CommunityNode) => {
    try {
      await updateNodeConfig(node.id, { sharing_enabled: !node.sharing_enabled });
      await loadNodes();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to toggle sharing");
    }
  };

  const handleToggleModel = async (node: CommunityNode, modelName: string, currentEnabled: boolean) => {
    try {
      await updateNodeModels(node.id, [{ model_name: modelName, enabled: !currentEnabled }]);
      await loadNodes();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update model sharing");
    }
  };

  const handlePauseResume = async (node: CommunityNode) => {
    try {
      if (node.status === "paused") {
        await resumeNode(node.id);
      } else {
        await pauseNode(node.id);
      }
      await loadNodes();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to update node status");
    }
  };

  const handleRevoke = async (node: CommunityNode) => {
    const confirmed = confirm(
      `Are you sure you want to permanently revoke node "${node.display_name}"? This action cannot be undone.`
    );
    if (!confirmed) return;
    try {
      await revokeNode(node.id);
      await loadNodes();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to revoke node");
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-gray-200 pb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Community Inference Nodes
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Register your computer as a community inference node to donate local Ollama compute to
            peers.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadNodes}
            className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3.5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
          <button
            id="pair-node-btn"
            onClick={handleOpenPairing}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Pair New Node
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="mt-6 flex items-center gap-3 rounded-lg bg-red-50 p-4 border border-red-200 text-sm text-red-800">
          <AlertCircle className="h-5 w-5 text-red-500 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Node Cards List */}
      {loading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : nodes.length === 0 ? (
        <div className="mt-12 rounded-2xl border-2 border-dashed border-gray-200 p-12 text-center">
          <Server className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-4 text-base font-semibold text-gray-900">No community nodes paired</h3>
          <p className="mt-2 text-sm text-gray-500 max-w-md mx-auto">
            You haven&apos;t connected any local Ollama instances yet. Pair an agent running on your
            computer to share compute.
          </p>
          <button
            onClick={handleOpenPairing}
            className="mt-6 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Pair Your First Node
          </button>
        </div>
      ) : (
        <div className="mt-8 space-y-6">
          {nodes.map((node) => (
            <div
              key={node.id}
              className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"
              data-testid={`node-card-${node.id}`}
            >
              {/* Card Header */}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-gray-100 bg-gray-50/60 px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="rounded-lg bg-blue-100 p-2 text-blue-700">
                    <Server className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-semibold text-gray-900">{node.display_name}</h2>
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-xs font-medium uppercase tracking-wide ${
                          node.status === "active" || node.status === "online"
                            ? "bg-emerald-100 text-emerald-800"
                            : node.status === "paused"
                              ? "bg-amber-100 text-amber-800"
                              : node.status === "revoked"
                                ? "bg-red-100 text-red-800"
                                : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {node.status}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 font-mono mt-0.5">
                      Key: {node.public_key.slice(0, 16)}...
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {node.status !== "revoked" && (
                    <>
                      <button
                        onClick={() => handlePauseResume(node)}
                        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium border ${
                          node.status === "paused"
                            ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                            : "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100"
                        }`}
                      >
                        {node.status === "paused" ? (
                          <>
                            <Play className="h-3.5 w-3.5" /> Resume
                          </>
                        ) : (
                          <>
                            <Pause className="h-3.5 w-3.5" /> Pause
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => handleRevoke(node)}
                        className="flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Revoke
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Card Body */}
              <div className="p-6 space-y-6">
                {/* Node Info & Master Toggle */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="space-y-1">
                    <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Environment
                    </span>
                    <p className="text-sm font-medium text-gray-800">
                      {node.os || "Unknown OS"} • {node.architecture || "x86_64"}
                    </p>
                    <p className="text-xs text-gray-500">
                      Ollama: {node.ollama_version || "Not reported"} • Agent: v{node.agent_version}
                    </p>
                  </div>

                  <div className="space-y-1">
                    <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Job Limits
                    </span>
                    <p className="text-sm font-medium text-gray-800">
                      Max {node.max_concurrent_jobs} concurrent jobs
                    </p>
                    <p className="text-xs text-gray-500">
                      Max duration: {node.max_execution_duration}s per job
                    </p>
                  </div>

                  <div className="flex flex-col justify-center sm:items-end">
                    <label className="flex items-center gap-3 cursor-pointer select-none">
                      <span className="text-sm font-medium text-gray-700">
                        Community Sharing
                      </span>
                      <input
                        type="checkbox"
                        checked={node.sharing_enabled}
                        onChange={() => handleToggleSharing(node)}
                        disabled={node.status === "revoked"}
                        className="h-5 w-5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                      />
                    </label>
                    <span className="text-xs text-gray-400 mt-1">
                      {node.sharing_enabled ? "Offering shared models" : "Sharing disabled"}
                    </span>
                  </div>
                </div>

                {/* Model Allowlist Section */}
                <div className="border-t border-gray-100 pt-5">
                  <div className="flex items-center gap-2 mb-3">
                    <Layers className="h-4 w-4 text-blue-600" />
                    <h3 className="text-sm font-semibold text-gray-900">
                      Discovered Local Models ({node.models.length})
                    </h3>
                  </div>

                  {node.models.length === 0 ? (
                    <p className="text-xs text-gray-500 italic">
                      No models reported yet. Ensure Ollama is running and has downloaded models
                      (e.g., `ollama pull llama3.2:3b`).
                    </p>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                      {node.models.map((m) => (
                        <div
                          key={m.id}
                          className={`flex items-center justify-between p-3 rounded-lg border text-sm transition-colors ${
                            m.enabled
                              ? "border-blue-200 bg-blue-50/40 text-blue-950"
                              : "border-gray-200 bg-white text-gray-600"
                          }`}
                        >
                          <div className="min-w-0 pr-2">
                            <p className="font-mono font-medium truncate">{m.model_name}</p>
                            <p className="text-xs text-gray-500">
                              {m.parameter_size || "Unknown size"}
                              {m.size_bytes ? ` • ${formatBytes(m.size_bytes)}` : ""}
                            </p>
                          </div>
                          <label className="flex items-center gap-2 cursor-pointer shrink-0">
                            <span className="text-xs text-gray-500">Share</span>
                            <input
                              type="checkbox"
                              checked={m.enabled}
                              disabled={node.status === "revoked" || !node.sharing_enabled}
                              onChange={() => handleToggleModel(node, m.model_name, m.enabled)}
                              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                            />
                          </label>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pairing Code Modal */}
      {isPairingModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl overflow-hidden border border-gray-200">
            <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-6 py-4">
              <div className="flex items-center gap-2 text-gray-900 font-semibold text-base">
                <Cpu className="h-5 w-5 text-blue-600" />
                Pair Ollama Node Agent
              </div>
              <button
                onClick={() => {
                  setIsPairingModalOpen(false);
                  loadNodes();
                }}
                className="rounded-lg p-1 text-gray-400 hover:bg-gray-100"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-6 space-y-5">
              {pairingLoading ? (
                <div className="flex flex-col items-center justify-center py-10 gap-3">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                  <p className="text-sm text-gray-500">Generating single-use pairing code...</p>
                </div>
              ) : pairingData ? (
                <>
                  <div className="text-center space-y-2">
                    <p className="text-xs uppercase tracking-wider text-gray-500 font-medium">
                      One-Time Device Pairing Code
                    </p>
                    <div className="flex items-center justify-center gap-3">
                      <span className="font-mono text-3xl font-bold tracking-widest text-blue-700 bg-blue-50 px-4 py-2 rounded-lg border border-blue-200">
                        {pairingData.pairing_code}
                      </span>
                      <button
                        onClick={handleCopyCode}
                        className="rounded-lg border border-gray-200 p-2 text-gray-600 hover:bg-gray-50"
                        title="Copy code"
                      >
                        {pairingCopied ? (
                          <Check className="h-5 w-5 text-emerald-600" />
                        ) : (
                          <Copy className="h-5 w-5" />
                        )}
                      </button>
                    </div>
                    <div className="flex items-center justify-center gap-1.5 text-xs text-amber-600">
                      <Clock className="h-3.5 w-3.5" />
                      <span>
                        Expires in {Math.floor(pairingSecondsLeft / 60)}:
                        {(pairingSecondsLeft % 60).toString().padStart(2, "0")}
                      </span>
                    </div>
                  </div>

                  <div className="space-y-3 rounded-lg bg-gray-900 p-4 text-xs font-mono text-gray-200">
                    <p className="text-gray-400 text-[11px]">Run on your contributor machine:</p>
                    <div className="space-y-1.5 select-all">
                      <p className="text-emerald-400"># 1. Pair your local device</p>
                      <p>
                        ragbait-node pair --server http://127.0.0.1:8000 --code{" "}
                        {pairingData.pairing_code}
                      </p>
                      <p className="text-emerald-400 mt-2"># 2. Run the agent daemon</p>
                      <p>ragbait-node run</p>
                    </div>
                  </div>

                  <div className="rounded-lg bg-blue-50/70 p-3 border border-blue-100 text-xs text-blue-900 space-y-1">
                    <p className="font-medium flex items-center gap-1.5">
                      <Shield className="h-4 w-4 text-blue-600 shrink-0" /> Security Guarantee
                    </p>
                    <p className="text-blue-700">
                      The agent strictly connects outward via WSS and accesses Ollama on loopback (
                      <code className="bg-blue-100 px-1 py-0.5 rounded">127.0.0.1:11434</code>). It
                      never opens inbound ports on your computer.
                    </p>
                  </div>
                </>
              ) : null}
            </div>

            <div className="border-t border-gray-100 bg-gray-50 px-6 py-3.5 flex justify-end">
              <button
                type="button"
                onClick={() => {
                  setIsPairingModalOpen(false);
                  loadNodes();
                }}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
