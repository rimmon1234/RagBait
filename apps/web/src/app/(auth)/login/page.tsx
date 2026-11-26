"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowRight, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { fetchWithAuth } from "@/lib/api";
import type { AccessStateResponse } from "@/types";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Auto-redirect if already authenticated
  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session) {
        try {
          const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
          const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me/access`, {
            headers: {
              Authorization: `Bearer ${session.access_token}`,
            },
          });
          if (res.ok) {
            const stateData = await res.json();
            if (stateData.effective_state === "admin") {
              router.push("/admin");
            } else if (stateData.effective_state === "pending") {
              router.push("/access/pending");
            } else if (stateData.effective_state === "rejected") {
              router.push("/access/rejected");
            } else if (stateData.effective_state === "unverified") {
              router.push(`/auth/verify?email=${encodeURIComponent(session.user.email || "")}`);
            } else {
              router.push("/knowledge-base");
            }
          }
        } catch {
          // ignore
        }
      }
    });
  }, [router]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const supabase = createClient();
    const { data, error: authError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (authError) {
      setError(authError.message);
      setLoading(false);
      return;
    }

    if (data.session) {
      // Check roles from token metadata immediately for instant admin entry
      const userMeta = data.session.user.user_metadata || {};
      const appMeta = data.session.user.app_metadata || {};
      const userRole = appMeta.role || userMeta.role;

      if (userRole === "admin" || userRole === "super_admin") {
        router.push("/admin");
        return;
      }

      // Check user effective access state authoritatively using fresh token
      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me/access`, {
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${data.session.access_token}`,
          },
        });

        if (res.ok) {
          const stateData: AccessStateResponse = await res.json();
          if (stateData.effective_state === "admin") {
            router.push("/admin");
            return;
          } else if (stateData.effective_state === "pending") {
            router.push("/access/pending");
            return;
          } else if (stateData.effective_state === "rejected") {
            router.push("/access/rejected");
            return;
          } else if (stateData.effective_state === "unverified") {
            router.push(`/auth/verify?email=${encodeURIComponent(email)}`);
            return;
          } else {
            // Approved students have full access to Knowledge Base in Phase 3
            router.push("/knowledge-base");
            return;
          }
        }
      } catch (err) {
        console.warn("Failed to fetch authoritative access state, checking token claims:", err);
      }

      // Safe fallback if /me/access was unreachable:
      router.push("/knowledge-base");
      return;
    }
  };

  return (
        <div className="flex min-h-[82vh] items-center justify-center px-4 py-12">
          <div className="card-elevated w-full max-w-md p-8">
            <div className="text-center">
                <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-600 text-sm font-black text-white shadow-md shadow-indigo-600/20">
                  RB
                </div>
                <h1 className="text-2xl font-bold tracking-tight text-slate-900">Sign in to RagBait</h1>
                <p className="mt-1.5 text-sm text-slate-600">
                  Enter your college email and password to access your study workspace.
                </p>
            </div>

            {error && (
                <div className="mt-5 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
        )}

                <form onSubmit={handleLogin} className="mt-6 space-y-4">
                  <div>
                    <label className="block text-sm font-semibold text-slate-700">Email Address</label>
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="student@university.edu"
                      className="mt-1.5 block w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm shadow-sm transition-colors focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-slate-700">Password</label>
                    <input
                      type="password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="mt-1.5 block w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm shadow-sm transition-colors focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    className="btn-primary-gradient w-full py-2.5"
                  >
                    {loading ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Signing in...
                      </>
                    ) : (
                      <>
                        Sign in
                        <ArrowRight className="h-4 w-4" />
                      </>
                    )}
                  </button>
                </form>

                  <p className="mt-6 text-center text-sm text-slate-600">
                    Don&apos;t have an account?{" "}
                      <Link href="/register" className="font-semibold text-indigo-600 hover:text-indigo-500">
                        Register here
                      </Link>
                  </p>
              </div>
    </div>
          );
}
