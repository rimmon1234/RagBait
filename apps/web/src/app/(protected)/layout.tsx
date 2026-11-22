"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    const supabase = createClient();

    // 1. Initial active session check
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        setIsAuthenticated(false);
        const loginUrl = `/login?redirectedFrom=${encodeURIComponent(pathname || "/local-inference")}`;
        router.replace(loginUrl);
      } else {
        setIsAuthenticated(true);
      }
    });

    // 2. Real-time auth state listener: session expiration, token refresh failure, or logout in another tab
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_OUT" || !session) {
        setIsAuthenticated(false);
        const loginUrl = `/login?redirectedFrom=${encodeURIComponent(pathname || "/local-inference")}`;
        router.replace(loginUrl);
      } else if (session) {
        setIsAuthenticated(true);
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [router, pathname]);

  if (isAuthenticated === null) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        <p className="text-xs text-gray-500 font-medium">Verifying active session...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
        <p className="text-xs text-gray-500">Session expired. Redirecting to login...</p>
      </div>
    );
  }

  return <>{children}</>;
}
