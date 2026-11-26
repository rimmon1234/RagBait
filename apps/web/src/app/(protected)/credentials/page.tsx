"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Edit3,
  Eye,
  EyeOff,
  Info,
  Key,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Users,
  XCircle,
} from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type {
  ApiKeyCredential,
  CredentialCreateInput,
  CredentialListResponse,
  CredentialSharingScope,
  CredentialUpdateInput,
  LLMProvider,
  ValidationResult,
} from "@/types";

export default function CredentialsPage() {
  const [credentials, setCredentials] = useState<ApiKeyCredential[]>([]);
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add Modal State
  const [showAddModal, setShowAddModal] = useState(false);
  const [addProviderId, setAddProviderId] = useState("openrouter");
  const [addNickname, setAddNickname] = useState("");
  const [addApiKey, setAddApiKey] = useState("");
  const [showAddApiKey, setShowAddApiKey] = useState(false);
  const [addSharedConsent, setAddSharedConsent] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Edit Modal State
  const [editingCred, setEditingCred] = useState<ApiKeyCredential | null>(null);
  const [editNickname, setEditNickname] = useState("");
  const [editSharingScope, setEditSharingScope] = useState<CredentialSharingScope>("owner_only");
  const [editSharedConsent, setEditSharedConsent] = useState(false);

  // Delete Modal State
  const [deletingCred, setDeletingCred] = useState<ApiKeyCredential | null>(null);

  // Validation State (per credential ID)
  const [validatingIds, setValidatingIds] = useState<Record<string, boolean>>({});
  const [validationFeedback, setValidationFeedback] = useState<Record<string, ValidationResult>>({});

  // Status Toggle State (per credential ID)
  const [togglingIds, setTogglingIds] = useState<Record<string, boolean>>({});

  // Quick Scope Toggle State (per credential ID)
  const [scopeTogglingIds, setScopeTogglingIds] = useState<Record<string, boolean>>({});

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [credsRes, provRes] = await Promise.allSettled([
        fetchWithAuth<CredentialListResponse>("/api/v1/me/api-keys"),
        fetchWithAuth<LLMProvider[]>("/api/v1/llm/providers?only_enabled=true"),
      ]);

      if (credsRes.status === "fulfilled") {
        const raw = credsRes.value;
        const list = raw?.items || raw?.credentials || [];
        setCredentials(Array.isArray(list) ? list : []);
      } else {
        throw credsRes.reason;
      }

      if (provRes.status === "fulfilled" && Array.isArray(provRes.value)) {
        setProviders(provRes.value);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load credentials");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Filter out local runtimes (e.g. Ollama) so they never appear in API credentials
  const cloudProviders = providers.filter((p) => p.runtime_type !== "local_runtime");
  const sharingEligibleProviders = cloudProviders.filter((p) => p.allows_community_sharing);
  const ownerOnlyProviders = cloudProviders.filter((p) => !p.allows_community_sharing);
  const selectedProviderMeta = providers.find((p) => p.id === addProviderId);
  const providerAllowsSharing = selectedProviderMeta?.allows_community_sharing ?? false;

  const handleOpenAddModal = () => {
    const defaultProvider = sharingEligibleProviders[0]?.id || cloudProviders[0]?.id || "openrouter";
    setAddProviderId(defaultProvider);
    setAddNickname("");
    setAddApiKey("");
    setShowAddApiKey(false);
    setAddSharedConsent(false);
    setFormError(null);
    setShowAddModal(true);
  };

  const handleCreateCredential = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addNickname.trim() || !addApiKey.trim()) {
      setFormError("Nickname and API key are required.");
      return;
    }

    const finalScope: CredentialSharingScope = providerAllowsSharing && addSharedConsent ? "community_shared" : "owner_only";
    const finalConsent: boolean = providerAllowsSharing && addSharedConsent;

    setIsSubmitting(true);
    setFormError(null);

    const payload: CredentialCreateInput = {
      provider_id: addProviderId,
      nickname: addNickname.trim(),
      api_key: addApiKey.trim(),
      sharing_scope: finalScope,
      shared_consent: finalConsent,
    };

    try {
      await fetchWithAuth<ApiKeyCredential>("/api/v1/me/api-keys", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      // Clear sensitive secret from memory immediately
      setAddApiKey("");
      setShowAddModal(false);
      await loadData();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Failed to store credential");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleValidate = async (cred: ApiKeyCredential) => {
    setValidatingIds((prev) => ({ ...prev, [cred.id]: true }));
    try {
      const res = await fetchWithAuth<ValidationResult>(`/api/v1/me/api-keys/${cred.id}/validate`, {
        method: "POST",
      });

      setValidationFeedback((prev) => ({ ...prev, [cred.id]: res }));
      const isValid = res.is_valid ?? res.outcome === "valid";
      const probedAt = res.validated_at ?? res.probed_at ?? new Date().toISOString();
      const code = res.safe_code ?? res.error_code ?? null;

      // Update local item validation status
      setCredentials((prev) =>
        prev.map((c) =>
          c.id === cred.id
            ? {
              ...c,
              validation_status: isValid ? "valid" : "invalid",
              last_validated_at: probedAt,
              validation_error_code: code,
              billing_tier: res.billing_tier || c.billing_tier,
            }
            : c
        )
      );
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Validation request failed");
    } finally {
      setValidatingIds((prev) => ({ ...prev, [cred.id]: false }));
    }
  };

  const handleToggleStatus = async (cred: ApiKeyCredential) => {
    if (cred.status === "disabled" && cred.validation_status !== "valid") {
      alert("A credential must pass an active validation probe before it can be enabled.");
      return;
    }

    const endpoint = cred.status === "active" ? "disable" : "enable";
    setTogglingIds((prev) => ({ ...prev, [cred.id]: true }));

    try {
      const updated = await fetchWithAuth<ApiKeyCredential>(
        `/api/v1/me/api-keys/${cred.id}/${endpoint}`,
        { method: "POST" }
      );
      setCredentials((prev) => prev.map((c) => (c.id === cred.id ? updated : c)));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : `Failed to update status`);
    } finally {
      setTogglingIds((prev) => ({ ...prev, [cred.id]: false }));
    }
  };

  const handleToggleSharingScope = async (cred: ApiKeyCredential, targetScope: CredentialSharingScope) => {
    setScopeTogglingIds((prev) => ({ ...prev, [cred.id]: true }));
    try {
      const payload: CredentialUpdateInput = {
        sharing_scope: targetScope,
        shared_consent: targetScope === "community_shared",
      };
      const updated = await fetchWithAuth<ApiKeyCredential>(`/api/v1/me/api-keys/${cred.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setCredentials((prev) => prev.map((c) => (c.id === cred.id ? updated : c)));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to update sharing scope");
    } finally {
      setScopeTogglingIds((prev) => ({ ...prev, [cred.id]: false }));
    }
  };

  const handleOpenEdit = (cred: ApiKeyCredential) => {
    setEditingCred(cred);
    setEditNickname(cred.nickname);
    setEditSharingScope(cred.sharing_scope);
    setEditSharedConsent(cred.shared_consent);
    setFormError(null);
  };

  const handleUpdateCredential = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingCred) return;

    if (editSharingScope === "community_shared" && !editSharedConsent) {
      setFormError("You must explicitly grant consent to share your credential.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);

    const payload: CredentialUpdateInput = {
      nickname: editNickname.trim(),
      sharing_scope: editSharingScope,
      shared_consent: editSharingScope === "community_shared" ? editSharedConsent : false,
    };

    try {
      const updated = await fetchWithAuth<ApiKeyCredential>(`/api/v1/me/api-keys/${editingCred.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });

      setCredentials((prev) => prev.map((c) => (c.id === editingCred.id ? updated : c)));
      setEditingCred(null);
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Failed to update credential");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingCred) return;
    setIsSubmitting(true);
    try {
      await fetchWithAuth(`/api/v1/me/api-keys/${deletingCred.id}`, {
        method: "DELETE",
      });
      setCredentials((prev) => prev.filter((c) => c.id !== deletingCred.id));
      setDeletingCred(null);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete credential");
    } finally {
      setIsSubmitting(false);
    }
  };

  const getValidationBadge = (status: string) => {
    switch (status) {
      case "valid":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-semibold text-green-700 border border-green-200">
            <CheckCircle2 className="h-3 w-3" /> Valid
          </span>
        );
      case "unvalidated":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-yellow-50 px-2.5 py-0.5 text-xs font-semibold text-yellow-700 border border-yellow-200">
            <Info className="h-3 w-3" /> Unvalidated
          </span>
        );
      case "rate_limited":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-orange-50 px-2.5 py-0.5 text-xs font-semibold text-orange-700 border border-orange-200">
            <AlertTriangle className="h-3 w-3" /> Rate Limited
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-semibold text-red-700 border border-red-200">
            <XCircle className="h-3 w-3" /> Invalid
          </span>
        );
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "active":
        return (
          <span className="inline-flex items-center gap-1 rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
            Active
          </span>
        );
      case "disabled":
        return (
          <span className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
            Disabled
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
            Revoked
          </span>
        );
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
            <h1 className="flex items-center gap-2.5 text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-sm shadow-indigo-600/20">
                <Key className="h-5 w-5" />
              </span>
              API Credentials Vault
            </h1>
              <p className="mt-1.5 text-sm text-slate-500">
                Manage your personal AI inference API keys. Credentials are envelope-encrypted with AES-256-GCM
                and protected against cross-account duplication.
              </p>
            </div>
              <div className="flex flex-wrap items-center gap-2.5">
                <Link
                  href="/credentials/usage"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                >
                  <Activity className="h-4 w-4 text-indigo-600" />
                  Usage Ledger
                </Link>
                <button
                  onClick={loadData}
                  disabled={loading}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:opacity-50"
                >
                  <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                  Refresh
                </button>
                <button
                  onClick={handleOpenAddModal}
                  className="btn-primary-gradient px-4 py-2"
                >
                  <Plus className="h-4 w-4" />
                  Add Credential
                </button>
              </div>
            </div>

            {/* Security Info Banner */}
                <div className="mt-6 rounded-2xl bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 p-5 text-sm text-slate-100 shadow-md shadow-slate-900/10">
                  <div className="flex items-start gap-3.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/10 text-cyan-300 ring-1 ring-white/15">
                      <Shield className="h-5 w-5" />
                    </div>
                    <div className="space-y-1">
                        <div className="font-semibold text-white">Cryptographic Isolation &amp; Zero-Knowledge Storage</div>
                        <p className="text-xs text-slate-300 leading-relaxed">
                          Your API keys are never stored in plaintext and cannot be read by platform administrators or other users.
                          Validation probes use lightweight non-generative endpoints (0 inference tokens consumed).
                          HMAC fingerprint claims permanently prevent registering the same API key across different accounts, even after deletion.
                        </p>
                    </div>
                  </div>
                </div>

                {/* Error State */}
                {error && (
                  <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 flex items-center gap-2">
                    <AlertCircle className="h-5 w-5 text-red-500 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                {/* Credentials Table / Cards */}
                <div className="mt-6 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                  {loading ? (
                    <div className="flex h-48 items-center justify-center">
                      <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                    </div>
                  ) : (credentials?.length ?? 0) === 0 ? (
                    <div className="px-6 py-12 text-center">
                      <Key className="mx-auto h-12 w-12 text-gray-300" />
                      <h3 className="mt-2 text-base font-medium text-gray-900">No credentials configured</h3>
                      <p className="mt-1 text-sm text-gray-500">
                        Add your first AI provider API credential (e.g. OpenRouter, Groq, Gemini) to enable study features.
                      </p>
                      <div className="mt-6">
                        <button
                          onClick={handleOpenAddModal}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
                        >
                          <Plus className="h-4 w-4" />
                          Add Credential
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                        <thead className="bg-gray-50 font-medium text-gray-600">
                          <tr>
                            <th scope="col" className="px-6 py-3.5">Nickname & Provider</th>
                            <th scope="col" className="px-6 py-3.5">Masked Key</th>
                            <th scope="col" className="px-6 py-3.5">Status</th>
                            <th scope="col" className="px-6 py-3.5">Validation</th>
                            <th scope="col" className="px-6 py-3.5">Sharing Scope</th>
                            <th scope="col" className="px-6 py-3.5">Platform Quotas & Protection</th>
                            <th scope="col" className="px-6 py-3.5 text-right">Actions</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200 bg-white">
                          {(credentials || []).map((cred) => {
                            const isValidating = validatingIds[cred.id] || false;
                            const isToggling = togglingIds[cred.id] || false;
                            const isTogglingScope = scopeTogglingIds[cred.id] || false;
                            const feedback = validationFeedback[cred.id];
                            const prov = providers.find((p) => p.id === cred.provider_id);
                            const allowsSharing = prov?.allows_community_sharing ?? false;
                            const isVerifiedFree = cred.billing_tier === "verified_free_only";

                            return (
                              <tr key={cred.id} className="hover:bg-gray-50 transition-colors">
                                <td className="px-6 py-4">
                                  <div className="font-medium text-gray-900">{cred.nickname}</div>
                                  <div className="flex items-center gap-1.5 mt-0.5">
                                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-700 uppercase">
                                      {cred.provider_id}
                                    </span>
                                    {cred.account_scope_id && cred.account_scope_verified && (
                                      <span className="text-xs text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded font-mono">
                                        Verified: {cred.account_scope_id}
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="px-6 py-4 font-mono text-xs text-gray-700">
                                  {cred.masked_key}
                                </td>
                                <td className="px-6 py-4">
                                  {getStatusBadge(cred.status)}
                                </td>
                                <td className="px-6 py-4">
                                  <div className="flex flex-col gap-1 items-start">
                                    {getValidationBadge(cred.validation_status)}
                                    {feedback && (
                                      <span className="text-[11px] text-gray-500 max-w-xs truncate" title={feedback.diagnostic_message || feedback.message}>
                                        {feedback.diagnostic_message || feedback.message}
                                      </span>
                                    )}
                                    {!feedback && cred.last_validated_at && (
                                      <span className="text-[11px] text-gray-400">
                                        {new Date(cred.last_validated_at).toLocaleDateString()}
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="px-6 py-4">
                                  <div className="flex flex-col gap-1.5 items-start">
                                    {cred.sharing_scope === "community_shared" ? (
                                      <>
                                        <span className="inline-flex items-center gap-1 text-xs font-medium text-purple-700 bg-purple-50 px-2 py-0.5 rounded border border-purple-200">
                                          <Users className="h-3 w-3" /> Community Shared
                                        </span>
                                        <button
                                          type="button"
                                          onClick={() => handleToggleSharingScope(cred, "owner_only")}
                                          disabled={isTogglingScope}
                                          className="text-[11px] font-medium text-gray-600 hover:text-gray-900 underline flex items-center gap-1 disabled:opacity-50"
                                        >
                                          {isTogglingScope ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Lock className="h-2.5 w-2.5" />}
                                          Make Private
                                        </button>
                                      </>
                                    ) : (
                                      <>
                                        <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                                          <Lock className="h-3 w-3" /> Owner-Only (Private)
                                        </span>
                                        {allowsSharing && isVerifiedFree ? (
                                          <button
                                            type="button"
                                            onClick={() => handleToggleSharingScope(cred, "community_shared")}
                                            disabled={isTogglingScope}
                                            className="text-[11px] font-medium text-purple-600 hover:text-purple-800 underline flex items-center gap-1 disabled:opacity-50"
                                          >
                                            {isTogglingScope ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Users className="h-2.5 w-2.5" />}
                                            Share with Community
                                          </button>
                                        ) : (
                                          <span className="text-[10px] text-gray-400">
                                            {allowsSharing ? "Validate key to enable sharing" : "Private only (not shareable)"}
                                          </span>
                                        )}
                                      </>
                                    )}
                                  </div>
                                </td>
                                <td className="px-6 py-4 text-xs text-gray-600">
                                  <div className="font-medium text-gray-800">500 RPD Baseline</div>
                                  <div className="text-gray-400">15,000 req/mo</div>
                                  <div className="mt-0.5 text-[11px] text-blue-600 font-medium">
                                    Redis Admission Engine
                                  </div>
                                </td>
                                <td className="px-6 py-4 text-right">
                                  <div className="flex items-center justify-end gap-2">
                                    {/* Validation Probe Button */}
                                    <button
                                      onClick={() => handleValidate(cred)}
                                      disabled={isValidating}
                                      title="Run safe non-generative validation probe"
                                      className="inline-flex items-center gap-1 rounded bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-50"
                                    >
                                      {isValidating ? (
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                      ) : (
                                        <CheckCircle2 className="h-3 w-3 text-green-600" />
                                      )}
                                      Validate
                                    </button>

                                    {/* Status Toggle (Enable/Disable) */}
                                    <button
                                      onClick={() => handleToggleStatus(cred)}
                                      disabled={isToggling || (cred.status === "disabled" && cred.validation_status !== "valid")}
                                      title={
                                        cred.status === "disabled" && cred.validation_status !== "valid"
                                          ? "Must pass validation probe before enabling"
                                          : cred.status === "active"
                                            ? "Disable credential"
                                            : "Enable credential"
                                      }
                                      className={`rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40 ${cred.status === "active"
                                        ? "bg-amber-100 text-amber-800 hover:bg-amber-200"
                                        : "bg-blue-100 text-blue-800 hover:bg-blue-200"
                                        }`}
                                    >
                                      {isToggling ? (
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                      ) : cred.status === "active" ? (
                                        "Disable"
                                      ) : (
                                        "Enable"
                                      )}
                                    </button>

                                    {/* Edit Button */}
                                    <button
                                      onClick={() => handleOpenEdit(cred)}
                                      title="Edit metadata"
                                      className="rounded p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                                    >
                                      <Edit3 className="h-4 w-4" />
                                    </button>

                                    {/* Delete Button */}
                                    <button
                                      onClick={() => setDeletingCred(cred)}
                                      title="Delete credential"
                                      className="rounded p-1.5 text-red-500 hover:bg-red-50 hover:text-red-700"
                                    >
                                      <Trash2 className="h-4 w-4" />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* ===================================================================== */}
                {/* ADD CREDENTIAL MODAL */}
                {/* ===================================================================== */}
                {showAddModal && (
                          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm">
                            <div className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white shadow-2xl max-h-[90vh] overflow-y-auto">
                              <div className="border-b border-slate-200 px-6 py-4">
                                <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                                  <Plus className="h-5 w-5 text-indigo-600" /> Add AI Provider Credential
                                </h3>
                                  <p className="mt-1 text-xs text-slate-500">
                                    Envelope-encrypted before storage. The plaintext key is never retained in server memory or frontend state.
                                  </p>
                              </div>

                              <form onSubmit={handleCreateCredential} className="px-6 py-4 space-y-4">
                                  <div className="rounded-xl bg-indigo-50/80 border border-indigo-200/80 p-3 text-xs text-indigo-950 leading-relaxed">
                                    <span className="font-semibold text-indigo-950">AI Chat Requirement:</span> To unlock AI Chat, contribute an eligible free-tier key (such as <strong>OpenRouter</strong>) and check <em>&quot;Share spare capacity with RagBait Community Pool&quot;</em> below. Private keys (Owner-Only) remain strictly confidential to your account and <strong>will not count as a community contribution</strong>. Each API key may only be claimed by one account.
                                  </div>

                                  {formError && (
                                      <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-700 flex items-start gap-2">
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-500 mt-0.5" />
                                        <span>{formError}</span>
                                      </div>
              )}

                                      {/* Provider Selection */}
                                      <div>
                                        <label className="block text-xs font-semibold text-gray-700">AI Inference Provider</label>
                                        <select
                                          value={addProviderId}
                                          onChange={(e) => {
                                            const nextId = e.target.value;
                                            setAddProviderId(nextId);
                                            const p = providers.find((pr) => pr.id === nextId);
                                            if (p && !p.allows_community_sharing) {
                                              setAddSharedConsent(false);
                                            }
                                          }}
                                          className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                        >
                                          {cloudProviders.length > 0 ? (
                                            <>
                                              {sharingEligibleProviders.length > 0 && (
                                                <optgroup label="Community Shared Pool Eligible (Unlocks AI Chat)">
                                                  {sharingEligibleProviders.map((p) => (
                                                    <option key={p.id} value={p.id}>
                                                      {p.name} ({p.id}) — Free-Tier Shared Pool Eligible
                                                    </option>
                                                  ))}
                                                </optgroup>
                                              )}
                                              {ownerOnlyProviders.length > 0 && (
                                                <optgroup label="Owner-Only / Private (Does NOT Unlock AI Chat)">
                                                  {ownerOnlyProviders.map((p) => (
                                                    <option key={p.id} value={p.id}>
                                                      {p.name} ({p.id}) — Owner-Only (Private)
                                                    </option>
                                                  ))}
                                                </optgroup>
                                              )}
                                            </>
                                          ) : (
                                            <>
                                              <optgroup label="Community Shared Pool Eligible (Unlocks AI Chat)">
                                                <option value="openrouter">OpenRouter — Community Sharing Eligible</option>
                                              </optgroup>
                                              <optgroup label="Owner-Only / Private (Does NOT Unlock AI Chat)">
                                                <option value="groq">Groq (Llama, Mixtral) — Owner-Only (Private)</option>
                                                <option value="gemini">Google Gemini — Owner-Only (Private)</option>
                                                <option value="cerebras">Cerebras — Owner-Only (Private)</option>
                                                <option value="openai">OpenAI — Owner-Only (Private)</option>
                                                <option value="xai">xAI — Owner-Only (Private)</option>
                                              </optgroup>
                                            </>
                                          )}
                                        </select>
                                      </div>

                                      {/* Nickname */}
                                      <div>
                                        <label className="block text-xs font-semibold text-gray-700">Credential Nickname</label>
                                        <input
                                          type="text"
                                          required
                                          placeholder="e.g. My OpenRouter Free Key, Research Cerebras Key"
                                          value={addNickname}
                                          onChange={(e) => setAddNickname(e.target.value)}
                                          className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                        />
                                      </div>

                                      {/* API Key Input */}
                                      <div>
                                        <div className="flex items-center justify-between">
                                          <label className="block text-xs font-semibold text-gray-700">API Key Secret</label>
                                          <button
                                            type="button"
                                            onClick={() => setShowAddApiKey(!showAddApiKey)}
                                            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
                                          >
                                            {showAddApiKey ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                                            {showAddApiKey ? "Hide" : "Show"}
                                          </button>
                                        </div>
                                        <input
                                          type={showAddApiKey ? "text" : "password"}
                                          required
                                          autoComplete="off"
                                          placeholder="sk-or-..., gsk_..., AIzaSy..., csk-..."
                                          value={addApiKey}
                                          onChange={(e) => setAddApiKey(e.target.value)}
                                          className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
                                        />
                                        <p className="mt-1 text-[11px] text-gray-400">
                                          Transferred via HTTPS, encrypted using 256-bit AES-GCM DEK, immediately cleared from memory.
                                        </p>
                                      </div>

                                      {/* Community Sharing Option (Platform-Controlled Defaults) */}
                                      <div>
                                        {providerAllowsSharing ? (
                                          <div className="rounded-lg border border-purple-200 bg-purple-50 p-3 space-y-2">
                                            <label className="flex items-start gap-2.5 cursor-pointer">
                                              <input
                                                type="checkbox"
                                                checked={addSharedConsent}
                                                onChange={(e) => setAddSharedConsent(e.target.checked)}
                                                className="mt-0.5 rounded text-purple-600 focus:ring-purple-500"
                                              />
                                              <div>
                                                <div className="text-xs font-semibold text-purple-900 flex items-center gap-1.5">
                                                  <Users className="h-3.5 w-3.5 text-purple-600" /> Share spare capacity with RagBait Community Pool (Free-tier only)
                                                </div>
                                                <div className="text-[11px] text-purple-700 leading-relaxed mt-1">
                                                  <strong>Check this box to unlock AI Chat:</strong> When enabled, and once verified by the platform as a non-billable free-tier key, anonymous study requests from approved peers may route through this key. Quota is protected by platform limits (500 RPD baseline). You can revoke consent at any time.
                                                </div>
                                                {!addSharedConsent && (
                                                  <div className="mt-2 text-[11px] font-medium text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                                                    ⚠️ <strong>Key will be Private:</strong> Leaving this unchecked saves the key as <em>Owner-Only (Private)</em>. Private keys do <u>not</u> satisfy the mandatory community contribution and will <u>not</u> unlock AI Chat.
                                                  </div>
                                                )}
                                              </div>
                                            </label>
                                          </div>
                                        ) : (
                                          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 flex items-start gap-2.5">
                                            <Lock className="h-4 w-4 text-gray-500 shrink-0 mt-0.5" />
                                            <div>
                                              <div className="text-xs font-semibold text-gray-800">Owner-Only Provider (Private)</div>
                                              <div className="text-[11px] text-gray-500 leading-relaxed mt-0.5">
                                                This provider operates in Owner-Only mode because automated non-billable tier verification is not supported. Your key will be saved privately and will <strong>not count toward the AI Chat community contribution requirement</strong>.
                                              </div>
                                            </div>
                                          </div>
                                        )}
                                      </div>

                                      {/* Action Buttons */}
                                      <div className="flex items-center justify-end gap-3 pt-3 border-t border-gray-200">
                                        <button
                                          type="button"
                                          onClick={() => setShowAddModal(false)}
                                          className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                                        >
                                          Cancel
                                        </button>
                                        <button
                                          type="submit"
                                          disabled={isSubmitting}
                                          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
                                        >
                                          {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Key className="h-4 w-4" />}
                                          Encrypt & Save Key
                                        </button>
                                      </div>
                                    </form>
          </div>
                            </div>
      )}

                            {/* ===================================================================== */}
                            {/* EDIT CREDENTIAL MODAL */}
                            {/* ===================================================================== */}
                            {editingCred && (
                              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
                                <div className="w-full max-w-lg rounded-xl bg-white shadow-xl max-h-[90vh] overflow-y-auto">
                                  <div className="border-b border-gray-200 px-6 py-4">
                                    <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                                      <Edit3 className="h-5 w-5 text-blue-600" /> Edit Credential Metadata
                                    </h3>
                                    <p className="mt-1 text-xs text-gray-500">
                                      Update parameters for {editingCred.nickname} ({editingCred.masked_key}). Plaintext secrets cannot be modified in place.
                                    </p>
                                  </div>

                                  <form onSubmit={handleUpdateCredential} className="px-6 py-4 space-y-4">
                                    {formError && (
                                      <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700 flex items-center gap-2">
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />
                                        <span>{formError}</span>
                                      </div>
                                    )}

                                    <div>
                                      <label className="block text-xs font-semibold text-gray-700">Nickname</label>
                                      <input
                                        type="text"
                                        required
                                        value={editNickname}
                                        onChange={(e) => setEditNickname(e.target.value)}
                                        className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                                      />
                                    </div>

                                    {/* Privacy / Sharing Scope Controls */}
                                    {(() => {
                                      const prov = providers.find((p) => p.id === editingCred.provider_id);
                                      const canShare = (prov?.allows_community_sharing ?? false) && editingCred.billing_tier === "verified_free_only";

                                      return (
                                        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-3">
                                          <label className="block text-xs font-semibold text-gray-700">Sharing Scope</label>
                                          {canShare ? (
                                            <div className="space-y-2">
                                              <label className="flex items-start gap-2.5 cursor-pointer">
                                                <input
                                                  type="radio"
                                                  name="edit_sharing_scope"
                                                  value="owner_only"
                                                  checked={editSharingScope === "owner_only"}
                                                  onChange={() => {
                                                    setEditSharingScope("owner_only");
                                                    setEditSharedConsent(false);
                                                  }}
                                                  className="mt-0.5 text-blue-600 focus:ring-blue-500"
                                                />
                                                <div>
                                                  <div className="text-xs font-semibold text-gray-800 flex items-center gap-1.5">
                                                    <Lock className="h-3.5 w-3.5 text-gray-600" /> Owner Only (Private)
                                                  </div>
                                                  <div className="text-[11px] text-gray-500">
                                                    Credential remains strictly private to your account.
                                                  </div>
                                                </div>
                                              </label>
                                              <label className="flex items-start gap-2.5 cursor-pointer">
                                                <input
                                                  type="radio"
                                                  name="edit_sharing_scope"
                                                  value="community_shared"
                                                  checked={editSharingScope === "community_shared"}
                                                  onChange={() => {
                                                    setEditSharingScope("community_shared");
                                                    setEditSharedConsent(true);
                                                  }}
                                                  className="mt-0.5 text-blue-600 focus:ring-blue-500"
                                                />
                                                <div>
                                                  <div className="text-xs font-semibold text-gray-800 flex items-center gap-1.5">
                                                    <Users className="h-3.5 w-3.5 text-purple-600" /> Community Shared Pool
                                                  </div>
                                                  <div className="text-[11px] text-gray-500">
                                                    Share spare capacity with other authenticated students.
                                                  </div>
                                                </div>
                                              </label>
                                            </div>
                                          ) : (
                                            <div className="text-xs text-gray-500 flex items-center gap-1.5">
                                              <Lock className="h-4 w-4 text-gray-400" />
                                              <span>Owner Only (This provider or credential tier cannot be shared)</span>
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })()}

                                    <div className="flex items-center justify-end gap-3 pt-3 border-t border-gray-200">
                                      <button
                                        type="button"
                                        onClick={() => setEditingCred(null)}
                                        className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                                      >
                                        Cancel
                                      </button>
                                      <button
                                        type="submit"
                                        disabled={isSubmitting}
                                        className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
                                      >
                                        {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save Changes"}
                                      </button>
                                    </div>
                                  </form>
                                </div>
                              </div>
                            )}

                            {/* ===================================================================== */}
                            {/* DELETE CONFIRMATION DIALOG */}
                            {/* ===================================================================== */}
                            {deletingCred && (
                              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
                                <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl space-y-4">
                                  <div className="flex items-start gap-3">
                                    <div className="rounded-full bg-red-100 p-2 text-red-600 shrink-0">
                                      <Trash2 className="h-6 w-6" />
                                    </div>
                                    <div>
                                      <h3 className="text-base font-bold text-gray-900">Delete API Credential?</h3>
                                      <p className="mt-1 text-xs text-gray-500 leading-relaxed">
                                        Are you sure you want to permanently delete <strong>{deletingCred.nickname}</strong> ({deletingCred.masked_key})?
                                      </p>
                                    </div>
                                  </div>

                                  <div className="rounded-lg border border-red-100 bg-red-50 p-3 text-xs text-red-800">
                                    <p className="font-semibold text-red-900">Cryptographic Purge Notice:</p>
                                    <p className="mt-1">
                                      The encrypted ciphertext and wrapped DEK in the private vault will be permanently deleted immediately.
                                      Any active routing will stop. Non-secret audit records and usage statistics are retained for security provenance.
                                    </p>
                                  </div>

                                  <div className="flex items-center justify-end gap-3 pt-2">
                                    <button
                                      type="button"
                                      onClick={() => setDeletingCred(null)}
                                      className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                                    >
                                      Cancel
                                    </button>
                                    <button
                                      type="button"
                                      onClick={handleDelete}
                                      disabled={isSubmitting}
                                      className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-700 disabled:opacity-50"
                                    >
                                      {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                                      Confirm Deletion
                                    </button>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                          );
}
