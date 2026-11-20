"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Calendar,
  CheckCircle2,
  Clock,
  Coins,
  DollarSign,
  Key,
  Layers,
  Loader2,
  RefreshCw,
  Zap,
} from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { UsageSummaryResponse } from "@/types";

export default function UsageLedgerPage() {
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadUsage = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWithAuth<UsageSummaryResponse>("/api/v1/me/usage");
      setSummary(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load usage ledger");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsage();
  }, []);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "settled":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-semibold text-green-700 border border-green-200">
            <CheckCircle2 className="h-3 w-3" /> Settled
          </span>
        );
      case "dispatched":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700 border border-amber-200">
            <Zap className="h-3 w-3" /> Dispatched
          </span>
        );
      case "reserved":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700 border border-blue-200">
            <Clock className="h-3 w-3" /> Reserved
          </span>
        );
      case "outcome_unknown":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 px-2 py-0.5 text-xs font-semibold text-purple-700 border border-purple-200">
            <AlertTriangle className="h-3 w-3" /> Unknown Outcome
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Link
              href="/credentials"
              className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded hover:bg-gray-100"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <h1 className="text-2xl font-bold tracking-tight text-gray-900 flex items-center gap-2">
              <Activity className="h-6 w-6 text-blue-600" />
              Usage Ledger & Quota Consumption
            </h1>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            Durable accounting ledger tracking committed request counts, token consumption, and budget capacity.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadUsage}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : summary ? (
        <div className="mt-6 space-y-6">
          {/* Unknown Outcome Warning */}
          {summary.unknown_outcome_count > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold">Ambiguous Operations Detected:</span>{" "}
                {summary.unknown_outcome_count} operation(s) had an unconfirmed upstream outcome.
                Their reserved spending budget is retained conservatively to protect budget caps.
              </div>
            </div>
          )}

          {/* Metric Cards Grid */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Daily Requests */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between text-xs font-medium text-gray-500">
                <span>Daily Requests</span>
                <Calendar className="h-4 w-4 text-blue-500" />
              </div>
              <div className="mt-2 text-2xl font-bold text-gray-900">
                {summary.daily_request_usage}{" "}
                <span className="text-sm font-normal text-gray-400">/ {summary.daily_request_limit}</span>
              </div>
              <div className="mt-2 text-xs text-gray-500">
                Reset boundary: Midnight UTC
              </div>
            </div>

            {/* Monthly Requests */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between text-xs font-medium text-gray-500">
                <span>Monthly Requests</span>
                <Layers className="h-4 w-4 text-purple-500" />
              </div>
              <div className="mt-2 text-2xl font-bold text-gray-900">
                {summary.monthly_request_usage}{" "}
                <span className="text-sm font-normal text-gray-400">/ {summary.monthly_request_limit}</span>
              </div>
              <div className="mt-2 text-xs text-gray-500">
                Reset boundary: 1st of month UTC
              </div>
            </div>

            {/* Daily Spending */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between text-xs font-medium text-gray-500">
                <span>Daily Spend</span>
                <DollarSign className="h-4 w-4 text-emerald-500" />
              </div>
              <div className="mt-2 text-2xl font-bold text-gray-900">
                ${(summary.daily_spending_cents / 100).toFixed(2)}{" "}
                {summary.daily_spending_budget_cents != null && (
                  <span className="text-sm font-normal text-gray-400">
                    / ${(summary.daily_spending_budget_cents / 100).toFixed(2)}
                  </span>
                )}
              </div>
              <div className="mt-2 text-xs text-gray-500">
                {summary.daily_spending_budget_cents != null ? "Capped by contributor" : "No daily cap"}
              </div>
            </div>

            {/* Monthly Spending */}
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between text-xs font-medium text-gray-500">
                <span>Monthly Spend</span>
                <Coins className="h-4 w-4 text-amber-500" />
              </div>
              <div className="mt-2 text-2xl font-bold text-gray-900">
                ${(summary.monthly_spending_cents / 100).toFixed(2)}{" "}
                {summary.monthly_spending_budget_cents != null && (
                  <span className="text-sm font-normal text-gray-400">
                    / ${(summary.monthly_spending_budget_cents / 100).toFixed(2)}
                  </span>
                )}
              </div>
              <div className="mt-2 text-xs text-gray-500">
                {summary.monthly_spending_budget_cents != null ? "Capped by contributor" : "No monthly cap"}
              </div>
            </div>
          </div>

          {/* Tokens and Reservations Summary */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm flex flex-wrap items-center justify-between gap-4 text-sm text-gray-700">
            <div>
              <span className="text-xs text-gray-400 block">Total Tokens Settled</span>
              <span className="font-semibold text-base text-gray-900">
                {(summary.total_input_tokens + summary.total_output_tokens).toLocaleString()}{" "}
                <span className="text-xs font-normal text-gray-500">
                  ({summary.total_input_tokens.toLocaleString()} in / {summary.total_output_tokens.toLocaleString()} out)
                </span>
              </span>
            </div>
            <div>
              <span className="text-xs text-gray-400 block">Total Confirmed Spend</span>
              <span className="font-semibold text-base text-emerald-600">
                ${(summary.total_actual_cost_cents / 100).toFixed(2)}
              </span>
            </div>
            <div>
              <span className="text-xs text-gray-400 block">Active Reserved Budget</span>
              <span className="font-semibold text-base text-blue-600">
                ${(summary.total_reserved_cost_cents / 100).toFixed(2)}
              </span>
            </div>
            <div>
              <span className="text-xs text-gray-400 block">Dispatched Attempts</span>
              <span className="font-semibold text-base text-gray-900">
                {summary.total_dispatched_requests}
              </span>
            </div>
          </div>

          {/* Recent Reservations Table */}
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-200 px-6 py-4">
              <h3 className="text-base font-bold text-gray-900">Recent Usage Reservations</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                Individual inference attempts and their durable accounting lifecycle.
              </p>
            </div>

            {summary.recent_reservations.length === 0 ? (
              <div className="px-6 py-12 text-center text-sm text-gray-500">
                No inference attempts recorded yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                  <thead className="bg-gray-50 text-xs font-medium text-gray-600">
                    <tr>
                      <th className="px-6 py-3">Correlation ID & Model</th>
                      <th className="px-6 py-3">Status</th>
                      <th className="px-6 py-3">Tokens (Est / Actual)</th>
                      <th className="px-6 py-3">Cost (Est / Actual)</th>
                      <th className="px-6 py-3">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 bg-white">
                    {summary.recent_reservations.map((r) => (
                      <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-6 py-3.5">
                          <div className="font-mono text-xs text-gray-800 truncate max-w-xs">{r.request_id}</div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            <span className="uppercase font-semibold text-gray-700">{r.provider_id}</span> • {r.model_id}
                          </div>
                        </td>
                        <td className="px-6 py-3.5">
                          {getStatusBadge(r.status)}
                        </td>
                        <td className="px-6 py-3.5 text-xs text-gray-600 font-mono">
                          {r.actual_input_tokens != null && r.actual_output_tokens != null ? (
                            <span>{(r.actual_input_tokens + r.actual_output_tokens).toLocaleString()} tokens</span>
                          ) : (
                            <span className="text-gray-400">Est: {r.estimated_input_tokens + r.estimated_output_tokens}</span>
                          )}
                        </td>
                        <td className="px-6 py-3.5 text-xs font-medium">
                          {r.actual_cost_cents != null ? (
                            <span className="text-emerald-600">${(r.actual_cost_cents / 100).toFixed(4)}</span>
                          ) : (
                            <span className="text-gray-400">Est: ${(r.estimated_cost_cents / 100).toFixed(2)}</span>
                          )}
                        </td>
                        <td className="px-6 py-3.5 text-xs text-gray-500">
                          {new Date(r.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
