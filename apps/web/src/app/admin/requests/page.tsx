"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Check, Loader2, RefreshCw, X } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { AccessRequestResponse, PaginatedAccessRequestsResponse } from "@/types";

export default function AdminAccessRequestsPage() {
  const [requests, setRequests] = useState<AccessRequestResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Dialog state
  const [activeRequest, setActiveRequest] = useState<AccessRequestResponse | null>(null);
  const [decisionType, setDecisionType] = useState<"approved" | "rejected" | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadRequests = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth<PaginatedAccessRequestsResponse>(
        "/api/v1/admin/access-requests?status=pending"
      );
      setRequests(res.items);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load access requests");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRequests();
  }, []);

  const handleDecisionSubmit = async () => {
    if (!activeRequest || !decisionType) return;
    setSubmitting(true);
    try {
      await fetchWithAuth(`/api/v1/admin/access-requests/${activeRequest.id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          decision: decisionType,
          reason: reason.trim() || undefined,
        }),
      });
      // Refresh list
      setActiveRequest(null);
      setDecisionType(null);
      setReason("");
      loadRequests();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to process decision");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Pending Access Requests</h2>
          <p className="text-xs text-gray-500">
            Review and approve registered student requests for platform access.
          </p>
        </div>
        <button
          onClick={loadRequests}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 p-4 text-sm text-red-700">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : requests.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-300 bg-white p-12 text-center">
          <p className="text-sm font-medium text-gray-500">No pending access requests.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  User / Name
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Email
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Requested At
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {requests.map((req) => (
                <tr key={req.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-6 py-4">
                    <div className="font-medium text-gray-900">{req.display_name || "Unknown"}</div>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">{req.email}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-gray-500">
                    {new Date(req.created_at).toLocaleDateString()} {new Date(req.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-right text-sm">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => {
                          setActiveRequest(req);
                          setDecisionType("approved");
                          setReason("");
                        }}
                        className="flex items-center gap-1 rounded bg-green-50 px-2.5 py-1.5 text-xs font-medium text-green-700 hover:bg-green-100"
                      >
                        <Check className="h-3.5 w-3.5" />
                        Approve
                      </button>
                      <button
                        onClick={() => {
                          setActiveRequest(req);
                          setDecisionType("rejected");
                          setReason("");
                        }}
                        className="flex items-center gap-1 rounded bg-red-50 px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100"
                      >
                        <X className="h-3.5 w-3.5" />
                        Reject
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Decision Modal */}
      {activeRequest && decisionType && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-bold text-gray-900">
              Confirm {decisionType === "approved" ? "Approval" : "Rejection"}
            </h3>
            <p className="mt-2 text-sm text-gray-600">
              You are about to {decisionType} access for{" "}
              <span className="font-semibold text-gray-900">{activeRequest.email}</span>.
            </p>

            <div className="mt-4">
              <label className="block text-xs font-medium text-gray-700">
                Decision Notes / Reason {decisionType === "rejected" ? "(Required for audit)" : "(Optional)"}
              </label>
              <textarea
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={decisionType === "approved" ? "Verified student..." : "Cohort full / invalid info..."}
                className="mt-1 block w-full rounded-md border border-gray-300 p-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setActiveRequest(null);
                  setDecisionType(null);
                }}
                disabled={submitting}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDecisionSubmit}
                disabled={submitting || (decisionType === "rejected" && !reason.trim())}
                className={`rounded-md px-4 py-2 text-sm font-medium text-white shadow-sm ${
                  decisionType === "approved"
                    ? "bg-green-600 hover:bg-green-700"
                    : "bg-red-600 hover:bg-red-700"
                } disabled:opacity-50`}
              >
                {submitting ? "Processing..." : `Confirm ${decisionType}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
