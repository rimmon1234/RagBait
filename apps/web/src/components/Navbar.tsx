"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Cpu, Key, LogOut, Network, Shield, User } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import type { UserProfile } from "@/types";

export function Navbar() {
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

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

      try {
        const res = await fetch("/api/v1/me", {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        });
        if (res.ok) {
          const data = await res.json();
          setProfile(data);
        }
      } catch {
        // Ignored
      } finally {
        setLoading(false);
      }
    };

    loadUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(() => {
      loadUser();
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

  return (
    <nav className="border-b border-gray-200 bg-white shadow-sm">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2 text-xl font-bold text-blue-600">
            <span className="rounded bg-blue-600 px-2 py-0.5 text-white">RB</span>
            <span>RagBait</span>
          </Link>
          {profile && (profile.approval_status === "approved" || profile.role === "admin" || profile.role === "super_admin") && (
            <>
              <Link
                href="/credentials"
                className="flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-blue-600"
              >
                <Key className="h-4 w-4" />
                API Credentials
              </Link>
              <Link
                href="/local-inference"
                className="flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-blue-600"
              >
                <Cpu className="h-4 w-4" />
                Local Inference
              </Link>
              <Link
                href="/community-nodes"
                className="flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-blue-600"
              >
                <Network className="h-4 w-4" />
                Community Nodes
              </Link>
            </>
          )}
          {profile && (profile.role === "admin" || profile.role === "super_admin") && (
            <Link
              href="/admin"
              className="flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-blue-600"
            >
              <Shield className="h-4 w-4" />
              Admin Dashboard
            </Link>
          )}
        </div>

        <div className="flex items-center gap-4">
          {loading ? (
            <div className="h-8 w-24 animate-pulse rounded bg-gray-100" />
          ) : profile ? (
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-gray-700">
                <User className="h-4 w-4 text-gray-400" />
                <span className="font-medium">{profile.display_name}</span>
                <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600 uppercase">
                  {profile.role.replace("_", " ")}
                </span>
              </div>
              <button
                onClick={handleSignOut}
                className="flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <Link
                href="/login"
                className="rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 hover:text-blue-600"
              >
                Sign in
              </Link>
              <Link
                href="/register"
                className="rounded-md bg-blue-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
              >
                Register
              </Link>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
