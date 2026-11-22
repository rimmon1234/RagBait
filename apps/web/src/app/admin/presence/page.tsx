"use client";

import { useEffect, useState } from "react";
import { Activity, AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { PresenceListResponse, PresenceUserResponse } from "@/types";

export default function AdminPresencePage() {
  const [activeUsers, setActiveUsers] = useState<PresenceUserResponse[]>([]);
  const [totalActive, setTotalActive] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPresence = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth<PresenceListResponse>("/api/v1/admin/presence?threshold_seconds=900");
      setActiveUsers(res.active_users);
      setTotalActive(res.total_active);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load presence data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPresence();
    const interval = setInterval(loadPresence, 30000); // Poll every 30s in admin console
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-gray-900">Active Presence Monitor</h2>
            <span className="flex h-2.5 w-2.5 rounded-full bg-green-500 animate-pulse" />
          </div>
          <p className="text-xs text-gray-500">
            Real-time active sessions transmitting 60-second presence pulses.
          </p>
        </div>
        <button
          onClick={loadPresence}
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

      {loading && activeUsers.length === 0 ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : activeUsers.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-300 bg-white p-12 text-center">
          <p className="text-sm font-medium text-gray-500">No users currently active.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  User
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Role
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Status
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Last Pulse Seen
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {activeUsers.map((u) => (
                <tr key={u.user_id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-6 py-4">
                    <div className="font-medium text-gray-900">{u.display_name}</div>
                    <div className="text-xs text-gray-500">{u.email}</div>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm">
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800 uppercase">
                      {u.role.replace("_", " ")}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm">
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                        u.is_active
                          ? "bg-green-100 text-green-800"
                          : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          u.is_active ? "bg-green-500" : "bg-gray-400"
                        }`}
                      />
                      {u.is_active ? "Online (< 2m)" : "Recent (< 15m)"}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-right text-xs text-gray-500">
                    {new Date(u.last_seen_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
