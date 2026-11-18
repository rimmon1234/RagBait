"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  DollarSign,
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
  const [addProviderId, setAddProviderId] = useState("groq");
  const [addNickname, setAddNickname] = useState("");
  const [addApiKey, setAddApiKey] = useState("");
  const [showAddApiKey, setShowAddApiKey] = useState(false);
  const [addAccountScopeId, setAddAccountScopeId] = useState("");
  const [addSharingScope, setAddSharingScope] = useState<CredentialSharingScope>("owner_only");
  const [addSharedConsent, setAddSharedConsent] = useState(false);
  const [addDailyRequestLimit, setAddDailyRequestLimit] = useState(500);
  const [addMonthlyRequestLimit, setAddMonthlyRequestLimit] = useState(15000);
  const [addDailySpendingCents, setAddDailySpendingCents] = useState<string>("");
  const [addMonthlySpendingCents, setAddMonthlySpendingCents] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Edit Modal State
  const [editingCred, setEditingCred] = useState<ApiKeyCredential | null>(null);
  const [editNickname, setEditNickname] = useState("");
  const [editAccountScopeId, setEditAccountScopeId] = useState("");
  const [editSharingScope, setEditSharingScope] = useState<CredentialSharingScope>("owner_only");
  const [editSharedConsent, setEditSharedConsent] = useState(false);
  const [editDailyRequestLimit, setEditDailyRequestLimit] = useState(500);
  const [editMonthlyRequestLimit, setEditMonthlyRequestLimit] = useState(15000);
  const [editDailySpendingCents, setEditDailySpendingCents] = useState<string>("");
  const [editMonthlySpendingCents, setEditMonthlySpendingCents] = useState<string>("");

  // Delete Modal State
  const [deletingCred, setDeletingCred] = useState<ApiKeyCredential | null>(null);

  // Validation State (per credential ID)
  const [validatingIds, setValidatingIds] = useState<Record<string, boolean>>({});
  const [validationFeedback, setValidationFeedback] = useState<Record<string, ValidationResult>>({});

  // Status Toggle State (per credential ID)
  const [togglingIds, setTogglingIds] = useState<Record<string, boolean>>({});

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [credsRes, provRes] = await Promise.allSettled([
        fetchWithAuth<CredentialListResponse>("/api/v1/me/api-keys"),
        fetchWithAuth<LLMProvider[]>("/api/v1/llm/providers?only_enabled=true"),
      ]);

      if (credsRes.status === "fulfilled") {
        setCredentials(credsRes.value.credentials);
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

  const selectedProviderMeta = providers.find((p) => p.id === addProviderId);
  const providerAllowsSharing = selectedProviderMeta?.allows_community_sharing ?? false;

  const handleOpenAddModal = () => {
    setAddProviderId(providers[0]?.id || "groq");
    setAddNickname("");
    setAddApiKey("");
    setShowAddApiKey(false);
    setAddAccountScopeId("");
    setAddSharingScope("owner_only");
    setAddSharedConsent(false);
    setAddDailyRequestLimit(500);
    setAddMonthlyRequestLimit(15000);
    setAddDailySpendingCents("");
    setAddMonthlySpendingCents("");
    setFormError(null);
    setShowAddModal(true);
  };

  const handleCreateCredential = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addNickname.trim() || !addApiKey.trim()) {
      setFormError("Nickname and API key are required.");
      return;
    }

    if (addSharingScope === "community_shared" && !addSharedConsent) {
      setFormError("You must explicitly provide consent to share your credential with the community.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);

    const payload: CredentialCreateInput = {
      provider_id: addProviderId,
      nickname: addNickname.trim(),
      api_key: addApiKey.trim(),
      account_scope_id: addAccountScopeId.trim() || undefined,
      sharing_scope: addSharingScope,
      shared_consent: addSharingScope === "community_shared" ? addSharedConsent : false,
      daily_request_limit: addDailyRequestLimit,
      monthly_request_limit: addMonthlyRequestLimit,
      daily_spending_limit_cents: addDailySpendingCents ? parseInt(addDailySpendingCents, 10) : null,
      monthly_spending_limit_cents: addMonthlySpendingCents ? parseInt(addMonthlySpendingCents, 10) : null,
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
      // Ensure API key is cleared on failure as well if desired
    }
  };

  const handleValidate = async (cred: ApiKeyCredential) => {
    setValidatingIds((prev) => ({ ...prev, [cred.id]: true }));
    try {
      const res = await fetchWithAuth<ValidationResult>(`/api/v1/me/api-keys/${cred.id}/validate`, {
        method: "POST",
      });

      setValidationFeedback((prev) => ({ ...prev, [cred.id]: res }));
      // Update local item validation status
      setCredentials((prev) =>
        prev.map((c) =>
          c.id === cred.id
            ? {
                ...c,
                validation_status: res.is_valid ? "valid" : "invalid",
                last_validated_at: res.probed_at,
                validation_error_code: res.error_code,
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

  const handleOpenEdit = (cred: ApiKeyCredential) => {
    setEditingCred(cred);
    setEditNickname(cred.nickname);
    setEditAccountScopeId(cred.account_scope_id || "");
    setEditSharingScope(cred.sharing_scope);
    setEditSharedConsent(cred.shared_consent);
    setEditDailyRequestLimit(cred.daily_request_limit);
    setEditMonthlyRequestLimit(cred.monthly_request_limit);
    setEditDailySpendingCents(
      cred.daily_spending_limit_cents !== null && cred.daily_spending_limit_cents !== undefined
        ? cred.daily_spending_limit_cents.toString()
        : ""
    );
    setEditMonthlySpendingCents(
      cred.monthly_spending_limit_cents !== null && cred.monthly_spending_limit_cents !== undefined
        ? cred.monthly_spending_limit_cents.toString()
        : ""
    );
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
      account_scope_id: editAccountScopeId.trim() || null,
      sharing_scope: editSharingScope,
      shared_consent: editSharingScope === "community_shared" ? editSharedConsent : false,
      daily_request_limit: editDailyRequestLimit,
      monthly_request_limit: editMonthlyRequestLimit,
      daily_spending_limit_cents: editDailySpendingCents ? parseInt(editDailySpendingCents, 10) : null,
      monthly_spending_limit_cents: editMonthlySpendingCents ? parseInt(editMonthlySpendingCents, 10) : null,
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
          <h1 className="text-2xl font-bold tracking-tight text-gray-900 flex items-center gap-2">
            <Key className="h-6 w-6 text-blue-600" />
            API Credentials Vault
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage your personal AI inference API keys. Credentials are envelope-encrypted with AES-256-GCM
            and stored under strict owner-only database access control.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/credentials/usage"
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
          >
            <Activity className="h-4 w-4 text-blue-600" />
            Usage Ledger
          </Link>
          <button
            onClick={loadData}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={handleOpenAddModal}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            Add Credential
          </button>
        </div>
      </div>

      {/* Security Info Banner */}
      <div className="mt-6 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
        <div className="flex items-start gap-3">
          <Shield className="h-5 w-5 text-blue-600 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold text-blue-900">Cryptographic Isolation & Zero-Knowledge Storage</div>
            <p className="text-xs text-blue-700 leading-relaxed">
              Your API keys are never stored in plaintext and cannot be read by platform administrators or other users.
              Validation probes use lightweight non-generative endpoints (0 inference tokens consumed).
              Daily and monthly request/spending limits configured below will be actively enforced by the shared LLM gateway in M2.4.
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
        ) : credentials.length === 0 ? (
          <div className="px-6 py-12 text-center">
            <Key className="mx-auto h-12 w-12 text-gray-300" />
            <h3 className="mt-2 text-base font-medium text-gray-900">No credentials configured</h3>
            <p className="mt-1 text-sm text-gray-500">
              Add your first AI provider API credential (e.g. Groq, Gemini, OpenRouter) to enable study features.
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
                  <th scope="col" className="px-6 py-3.5">Configured Limits</th>
                  <th scope="col" className="px-6 py-3.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {credentials.map((cred) => {
                  const isValidating = validatingIds[cred.id] || false;
                  const isToggling = togglingIds[cred.id] || false;
                  const feedback = validationFeedback[cred.id];

                  return (
                    <tr key={cred.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-medium text-gray-900">{cred.nickname}</div>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-700 uppercase">
                            {cred.provider_id}
                          </span>
                          {cred.account_scope_id && (
                            <span className="text-xs text-gray-400">({cred.account_scope_id})</span>
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
                            <span className="text-[11px] text-gray-500 max-w-xs truncate" title={feedback.diagnostic_message}>
                              {feedback.diagnostic_message}
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
                        {cred.sharing_scope === "community_shared" ? (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-purple-700 bg-purple-50 px-2 py-1 rounded border border-purple-200">
                            <Users className="h-3 w-3" /> Community Shared
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-700 bg-gray-100 px-2 py-1 rounded">
                            <Lock className="h-3 w-3" /> Owner Only
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-xs text-gray-600">
                        <div>{cred.daily_request_limit} req/day</div>
                        <div className="text-gray-400">{cred.monthly_request_limit} req/mo</div>
                        {(cred.daily_spending_limit_cents !== null || cred.monthly_spending_limit_cents !== null) && (
                          <div className="mt-1 text-[11px] text-emerald-600 font-medium">
                            ${((cred.daily_spending_limit_cents || 0) / 100).toFixed(2)} / ${((cred.monthly_spending_limit_cents || 0) / 100).toFixed(2)}
                          </div>
                        )}
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
                            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40 ${
                              cred.status === "active"
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
                            title="Edit metadata & limits"
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-xl rounded-xl bg-white shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="border-b border-gray-200 px-6 py-4">
              <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <Plus className="h-5 w-5 text-blue-600" /> Add AI Provider Credential
              </h3>
              <p className="mt-1 text-xs text-gray-500">
                Envelope-encrypted before storage. The plaintext key is never retained in server memory or frontend state.
              </p>
            </div>

            <form onSubmit={handleCreateCredential} className="px-6 py-4 space-y-4">
              {formError && (
                <div className="rounded-lg bg-red-50 p-3 text-xs text-red-700 flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />
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
                      setAddSharingScope("owner_only");
                      setAddSharedConsent(false);
                    }
                  }}
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                >
                  {providers.length > 0 ? (
                    providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.id}) {p.allows_community_sharing ? "— Community Sharing Eligible" : "— Owner-Only"}
                      </option>
                    ))
                  ) : (
                    <>
                      <option value="groq">Groq (Llama, Mixtral) — Community Sharing Eligible</option>
                      <option value="gemini">Google Gemini — Owner-Only</option>
                      <option value="openrouter">OpenRouter — Community Sharing Eligible</option>
                      <option value="cerebras">Cerebras — Community Sharing Eligible</option>
                      <option value="openai">OpenAI — Owner-Only</option>
                      <option value="xai">xAI — Owner-Only</option>
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
                  placeholder="e.g. My Groq Free Key, Research Cerebras Key"
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
                  placeholder="gsk_..., AIzaSy..., sk-or-..., csk-..."
                  value={addApiKey}
                  onChange={(e) => setAddApiKey(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
                />
                <p className="mt-1 text-[11px] text-gray-400">
                  Transferred via HTTPS, encrypted using 256-bit AES-GCM DEK, immediately cleared from memory.
                </p>
              </div>

              {/* Account Scope ID (Optional) */}
              <div>
                <label className="block text-xs font-semibold text-gray-700">
                  Account Scope / Organization ID <span className="font-normal text-gray-400">(Optional)</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. org-12345, project-name"
                  value={addAccountScopeId}
                  onChange={(e) => setAddAccountScopeId(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
              </div>

              {/* Sharing Scope & Explicit Consent */}
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-3">
                <label className="block text-xs font-semibold text-gray-700">Sharing Scope</label>
                <div className="space-y-2">
                  <label className="flex items-start gap-2.5 cursor-pointer">
                    <input
                      type="radio"
                      name="sharing_scope"
                      value="owner_only"
                      checked={addSharingScope === "owner_only"}
                      onChange={() => {
                        setAddSharingScope("owner_only");
                        setAddSharedConsent(false);
                      }}
                      className="mt-0.5 text-blue-600 focus:ring-blue-500"
                    />
                    <div>
                      <div className="text-xs font-semibold text-gray-800 flex items-center gap-1.5">
                        <Lock className="h-3.5 w-3.5 text-gray-600" /> Owner Only (Private)
                      </div>
                      <div className="text-[11px] text-gray-500">
                        This credential can only be used by your account for interactive study sessions and RAG queries.
                      </div>
                    </div>
                  </label>

                  <label className={`flex items-start gap-2.5 ${providerAllowsSharing ? "cursor-pointer" : "opacity-60 cursor-not-allowed"}`}>
                    <input
                      type="radio"
                      name="sharing_scope"
                      value="community_shared"
                      disabled={!providerAllowsSharing}
                      checked={addSharingScope === "community_shared"}
                      onChange={() => setAddSharingScope("community_shared")}
                      className="mt-0.5 text-blue-600 focus:ring-blue-500"
                    />
                    <div>
                      <div className="text-xs font-semibold text-gray-800 flex items-center gap-1.5">
                        <Users className="h-3.5 w-3.5 text-purple-600" /> Community Shared Pool
                      </div>
                      <div className="text-[11px] text-gray-500">
                        {providerAllowsSharing
                          ? "Share spare rate limit capacity with other authenticated students on RagBait."
                          : "This provider is not approved for community sharing under institutional policy. Owner-only use only."}
                      </div>
                    </div>
                  </label>
                </div>

                {/* Explicit Consent Notice for Community Sharing */}
                {addSharingScope === "community_shared" && (
                  <div className="rounded-md border border-purple-200 bg-purple-50 p-3 mt-3">
                    <label className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={addSharedConsent}
                        onChange={(e) => setAddSharedConsent(e.target.checked)}
                        className="mt-0.5 rounded text-purple-600 focus:ring-purple-500"
                      />
                      <span className="text-xs font-medium text-purple-900 leading-tight">
                        I explicitly authorize RagBait to route anonymous inference requests from approved
                        community peers through this credential, adhering to my configured rate and spending limits.
                      </span>
                    </label>
                  </div>
                )}
              </div>

              {/* Limits Configuration */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Daily Request Limit</label>
                  <input
                    type="number"
                    min="1"
                    max="100000"
                    value={addDailyRequestLimit}
                    onChange={(e) => setAddDailyRequestLimit(parseInt(e.target.value, 10) || 1)}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Monthly Request Limit</label>
                  <input
                    type="number"
                    min="1"
                    max="1000000"
                    value={addMonthlyRequestLimit}
                    onChange={(e) => setAddMonthlyRequestLimit(parseInt(e.target.value, 10) || 1)}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">
                    Daily Spending Cap ($) <span className="font-normal text-gray-400">(Opt)</span>
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="e.g. 1.00"
                    value={addDailySpendingCents ? (parseInt(addDailySpendingCents, 10) / 100).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value);
                      setAddDailySpendingCents(isNaN(val) ? "" : Math.round(val * 100).toString());
                    }}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">
                    Monthly Spending Cap ($) <span className="font-normal text-gray-400">(Opt)</span>
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="e.g. 20.00"
                    value={addMonthlySpendingCents ? (parseInt(addMonthlySpendingCents, 10) / 100).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value);
                      setAddMonthlySpendingCents(isNaN(val) ? "" : Math.round(val * 100).toString());
                    }}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
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
                <Edit3 className="h-5 w-5 text-blue-600" /> Edit Credential Metadata & Limits
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

              <div>
                <label className="block text-xs font-semibold text-gray-700">Account Scope ID</label>
                <input
                  type="text"
                  value={editAccountScopeId}
                  onChange={(e) => setEditAccountScopeId(e.target.value)}
                  className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
              </div>

              {/* Limits */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Daily Request Limit</label>
                  <input
                    type="number"
                    min="1"
                    value={editDailyRequestLimit}
                    onChange={(e) => setEditDailyRequestLimit(parseInt(e.target.value, 10) || 1)}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Monthly Request Limit</label>
                  <input
                    type="number"
                    min="1"
                    value={editMonthlyRequestLimit}
                    onChange={(e) => setEditMonthlyRequestLimit(parseInt(e.target.value, 10) || 1)}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Daily Spending ($)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="None"
                    value={editDailySpendingCents ? (parseInt(editDailySpendingCents, 10) / 100).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value);
                      setEditDailySpendingCents(isNaN(val) ? "" : Math.round(val * 100).toString());
                    }}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700">Monthly Spending ($)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    placeholder="None"
                    value={editMonthlySpendingCents ? (parseInt(editMonthlySpendingCents, 10) / 100).toString() : ""}
                    onChange={(e) => {
                      const val = parseFloat(e.target.value);
                      setEditMonthlySpendingCents(isNaN(val) ? "" : Math.round(val * 100).toString());
                    }}
                    className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                </div>
              </div>

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
