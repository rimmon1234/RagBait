"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Loader2, RefreshCw, Search, Shield, UserX } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { PaginatedUsersResponse, UserAdminResponse, UserRole } from "@/types";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserAdminResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modals
  const [selectedUser, setSelectedUser] = useState<UserAdminResponse | null>(null);
  const [newRole, setNewRole] = useState<UserRole>("user");
  const [statusAction, setStatusAction] = useState<"suspend" | "activate" | null>(null);
  const [suspensionReason, setSuspensionReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        page: page.toString(),
        page_size: "15",
      });
      if (search.trim()) params.set("search", search.trim());
      if (roleFilter) params.set("role", roleFilter);
      if (statusFilter) params.set("approval_status", statusFilter);

      const res = await fetchWithAuth<PaginatedUsersResponse>(`/api/v1/admin/users?${params.toString()}`);
      setUsers(res.items);
      setTotal(res.total);
      setTotalPages(res.total_pages);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, roleFilter, statusFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    loadUsers();
  };

  const handleRoleChange = async () => {
    if (!selectedUser) return;
    setSubmitting(true);
    try {
      await fetchWithAuth(`/api/v1/admin/users/${selectedUser.id}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      });
      setSelectedUser(null);
      loadUsers();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Role modification failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleStatusChange = async () => {
    if (!selectedUser || !statusAction) return;
    setSubmitting(true);
    try {
      const newStatus = statusAction === "suspend" ? "suspended" : "approved";
      await fetchWithAuth(`/api/v1/admin/users/${selectedUser.id}/status`, {
        method: "PATCH",
        body: JSON.stringify({
          approval_status: newStatus,
          reason: suspensionReason.trim() || undefined,
        }),
      });
      setSelectedUser(null);
      setStatusAction(null);
      setSuspensionReason("");
      loadUsers();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Status update failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">User Directory</h2>
          <p className="text-xs text-gray-500">{total} registered user accounts</p>
        </div>

        <form onSubmit={handleSearchSubmit} className="flex gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by name or email..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="rounded-md border border-gray-300 py-1.5 pl-9 pr-3 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <button
            type="submit"
            className="rounded-md bg-gray-100 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-200"
          >
            Search
          </button>
        </form>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={roleFilter}
          onChange={(e) => {
            setRoleFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All Roles</option>
          <option value="user">User</option>
          <option value="admin">Admin</option>
          <option value="super_admin">Super Admin</option>
        </select>

        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All Statuses</option>
          <option value="approved">Approved</option>
          <option value="pending">Pending</option>
          <option value="suspended">Suspended</option>
          <option value="rejected">Rejected</option>
        </select>

        <button
          onClick={loadUsers}
          disabled={loading}
          className="ml-auto flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50"
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
                  Approval Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Registered
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-6 py-4">
                    <div className="font-medium text-gray-900">{u.display_name}</div>
                    <div className="text-xs text-gray-500">{u.email}</div>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                        u.role === "super_admin"
                          ? "bg-purple-100 text-purple-800"
                          : u.role === "admin"
                          ? "bg-blue-100 text-blue-800"
                          : "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {u.role.replace("_", " ")}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                        u.approval_status === "approved"
                          ? "bg-green-100 text-green-800"
                          : u.approval_status === "pending"
                          ? "bg-amber-100 text-amber-800"
                          : u.approval_status === "suspended"
                          ? "bg-red-100 text-red-800"
                          : "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {u.approval_status}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-xs text-gray-500">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-right text-sm">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => {
                          setSelectedUser(u);
                          setNewRole(u.role);
                        }}
                        className="rounded bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
                      >
                        Role
                      </button>
                      {u.approval_status === "suspended" ? (
                        <button
                          onClick={() => {
                            setSelectedUser(u);
                            setStatusAction("activate");
                          }}
                          className="rounded bg-green-50 px-2 py-1 text-xs font-medium text-green-700 hover:bg-green-100"
                        >
                          Reactivate
                        </button>
                      ) : u.approval_status === "approved" ? (
                        <button
                          onClick={() => {
                            setSelectedUser(u);
                            setStatusAction("suspend");
                            setSuspensionReason("");
                          }}
                          className="rounded bg-red-50 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
                        >
                          Suspend
                        </button>
                      ) : u.approval_status === "pending" ? (
                        <Link
                          href="/admin/requests"
                          className="rounded bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
                        >
                          Review
                        </Link>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-6 py-3">
            <span className="text-xs text-gray-500">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 disabled:opacity-50"
              >
                <ArrowLeft className="h-3 w-3" />
                Previous
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 disabled:opacity-50"
              >
                Next
                <ArrowRight className="h-3 w-3" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Role Change Modal */}
      {selectedUser && !statusAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-bold text-gray-900">Change Role</h3>
            <p className="mt-2 text-sm text-gray-600">
              Update platform role for <span className="font-semibold text-gray-900">{selectedUser.email}</span>.
            </p>

            <div className="mt-4">
              <label className="block text-xs font-medium text-gray-700">Assign New Role</label>
              <select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value as UserRole)}
                className="mt-1 block w-full rounded-md border border-gray-300 p-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="user">User (Regular Student)</option>
                <option value="admin">Admin (Staff / Moderator)</option>
                <option value="super_admin">Super Admin (Platform Owner)</option>
              </select>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setSelectedUser(null)}
                disabled={submitting}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRoleChange}
                disabled={submitting || newRole === selectedUser.role}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? "Updating..." : "Save Role"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Status Modal (Suspend / Reactivate) */}
      {selectedUser && statusAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-bold text-gray-900">
              Confirm {statusAction === "suspend" ? "Suspension" : "Reactivation"}
            </h3>
            <p className="mt-2 text-sm text-gray-600">
              Are you sure you want to {statusAction}{" "}
              <span className="font-semibold text-gray-900">{selectedUser.email}</span>?
            </p>

            {statusAction === "suspend" && (
              <div className="mt-4">
                <label className="block text-xs font-medium text-gray-700">
                  Suspension Reason (Audited)
                </label>
                <textarea
                  rows={2}
                  value={suspensionReason}
                  onChange={(e) => setSuspensionReason(e.target.value)}
                  placeholder="Reason for account suspension..."
                  className="mt-1 block w-full rounded-md border border-gray-300 p-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
            )}

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setSelectedUser(null);
                  setStatusAction(null);
                }}
                disabled={submitting}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleStatusChange}
                disabled={submitting}
                className={`rounded-md px-4 py-2 text-sm font-medium text-white shadow-sm ${
                  statusAction === "suspend"
                    ? "bg-red-600 hover:bg-red-700"
                    : "bg-green-600 hover:bg-green-700"
                } disabled:opacity-50`}
              >
                {submitting ? "Processing..." : `Confirm ${statusAction}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
