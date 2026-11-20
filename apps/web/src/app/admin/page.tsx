"use client";

import { useEffect, useState } from "react";
import { Activity, AlertCircle, Clock, RefreshCw, ShieldAlert, UserCheck, Users } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { OverviewResponse } from "@/types";

export default function AdminOverviewPage() {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth<OverviewResponse>("/api/v1/admin/overview");
      setData(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load overview data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Platform Metrics</h2>
        <button
          onClick={loadData}
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

      {loading && !data ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-xl border border-gray-200 bg-gray-100" />
          ))}
        </div>
      ) : data ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-500">Total Users</span>
              <Users className="h-5 w-5 text-gray-400" />
            </div>
            <div className="mt-2 text-3xl font-extrabold text-gray-900">{data.total_users}</div>
            <p className="mt-1 text-xs text-gray-500">{data.approved_users} approved members</p>
          </div>

          <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-amber-700">Pending Requests</span>
              <Clock className="h-5 w-5 text-amber-500" />
            </div>
            <div className="mt-2 text-3xl font-extrabold text-amber-900">{data.pending_requests}</div>
            <p className="mt-1 text-xs text-amber-600">Awaiting administrator review</p>
          </div>

          <div className="rounded-xl border border-green-200 bg-green-50/50 p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-green-700">Active Now</span>
              <Activity className="h-5 w-5 text-green-500" />
            </div>
            <div className="mt-2 text-3xl font-extrabold text-green-900">{data.active_users_now}</div>
            <p className="mt-1 text-xs text-green-600">{data.active_users_recent} active in last 15 min</p>
          </div>

          <div className="rounded-xl border border-red-200 bg-red-50/50 p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-red-700">Suspended / Rejected</span>
              <ShieldAlert className="h-5 w-5 text-red-500" />
            </div>
            <div className="mt-2 text-3xl font-extrabold text-red-900">
              {data.suspended_users + data.rejected_users}
            </div>
            <p className="mt-1 text-xs text-red-600">
              {data.suspended_users} suspended • {data.rejected_users} rejected
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
