"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BookOpen, Key, LogOut, MessageSquare, Shield, Sparkles, User } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import type { UserProfile } from "@/types";

export function Navbar() {
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentPath, setCurrentPath] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined") {
      setCurrentPath(window.location.pathname);
    }
  }, []);

  useEffect(() => {
    const supabase = createClient();

    const loadUser = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) {
        setProfile(null);
        setLoading(false);
        return;
      }

      const email = session.user.email || "";
      const metaRole = session.user.app_metadata?.role || session.user.user_metadata?.role;
      const initialRole = metaRole === "admin" || metaRole === "super_admin" ? metaRole : "user";
      const fallbackProfile: UserProfile = {
        id: session.user.id,
        email,
        display_name: session.user.user_metadata?.display_name || email.split("@")[0] || "User",
        role: initialRole as UserProfile["role"],
        approval_status: "approved",
        created_at: session.user.created_at,
        updated_at: session.user.updated_at || session.user.created_at,
      };

      setProfile((prev) => prev || fallbackProfile);
      setLoading(false);

      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        });
        if (res.ok) {
          const data = await res.json();
          setProfile(data);
        }
      } catch {
        setProfile((prev) => prev || fallbackProfile);
      }
    };

    loadUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, newSession) => {
      if (newSession) {
        loadUser();
      } else {
        setProfile(null);
        setLoading(false);
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  const handleSignOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    setProfile(null);
    router.push("/login");
  };

  const navLinkClass = (prefix: string) => {
    const isActive = currentPath.startsWith(prefix);
    return `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-all ${isActive
        ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200/80 shadow-sm"
        : "text-slate-600 hover:bg-slate-100/80 hover:text-slate-900"
      }`;
  };

  return (
      <nav className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
              <div className="flex flex-wrap items-center gap-3 sm:gap-5">
                <Link
                  href="/"
                  onClick={() => setCurrentPath("/")}
                  className="group flex items-center gap-2.5 text-lg font-extrabold tracking-tight text-slate-900"
                >
                  <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 via-indigo-600 to-cyan-500 text-xs font-black text-white shadow-sm shadow-indigo-600/25 transition-transform group-hover:scale-105">
                    RB
                  </span>
                  <span className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-800 bg-clip-text text-transparent">
                    RagBait
                  </span>
                </Link>

                {profile &&
                  (profile.approval_status === "approved" ||
                    profile.role === "admin" ||
                    profile.role === "super_admin") && (
                    <div className="flex flex-wrap items-center gap-1 sm:gap-1.5">
                      <Link
                        href="/knowledge-base"
                        onClick={() => setCurrentPath("/knowledge-base")}
                        className={navLinkClass("/knowledge-base")}
                      >
                        <BookOpen className="h-4 w-4 text-blue-600" />
                        Knowledge Base
                      </Link>
                      <Link
                        href="/chat"
                        onClick={() => setCurrentPath("/chat")}
                        className={navLinkClass("/chat")}
                      >
                        <MessageSquare className="h-4 w-4 text-indigo-600" />
                        AI Chat
                      </Link>
                      <Link
                        href="/credentials"
                        onClick={() => setCurrentPath("/credentials")}
                        className={navLinkClass("/credentials")}
                      >
                        <Key className="h-4 w-4 text-emerald-600" />
                        API Credentials
                      </Link>
                    </div>
                  )}

                {profile && (profile.role === "admin" || profile.role === "super_admin") && (
                  <Link
                    href="/admin"
                    onClick={() => setCurrentPath("/admin")}
                    className={navLinkClass("/admin")}
                  >
                    <Shield className="h-4 w-4 text-amber-600" />
                    Admin Dashboard
                  </Link>
                )}
              </div>

                <div className="flex items-center gap-3">
                  {loading ? (
            <div className="h-8 w-28 animate-pulse rounded-xl bg-slate-100" />
                  ) : profile ? (
                          <div className="flex items-center gap-2.5">
                            <div className="flex items-center gap-2 rounded-full border border-slate-200/80 bg-slate-50/90 px-3 py-1 text-sm text-slate-700">
                              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-100 text-indigo-700">
                                <User className="h-3 w-3" />
                              </span>
                              <span className="font-semibold text-slate-800 max-w-[140px] truncate">
                                {profile.display_name}
                              </span>
                              <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700 ring-1 ring-indigo-200/70">
                                {profile.role.replace("_", " ")}
                              </span>
                            </div>
                            <button
                              onClick={handleSignOut}
                              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 shadow-sm transition-colors hover:bg-slate-50 hover:text-slate-900"
                            >
                              <LogOut className="h-4 w-4" />
                              <span className="hidden sm:inline">Sign out</span>
                            </button>
                          </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <Link
                                href="/login"
                                className="rounded-xl px-3.5 py-1.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 hover:text-indigo-700"
                              >
                                Sign in
                              </Link>
                              <Link
                                href="/register"
                                className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 px-4 py-1.5 text-sm font-semibold text-white shadow-sm shadow-indigo-600/20 transition-all hover:shadow-md hover:brightness-105"
                              >
                                <Sparkles className="h-3.5 w-3.5" />
                                Register
                              </Link>
                            </div>
          )}
                          </div>
                      </div>
                    </nav>
                  );
}
