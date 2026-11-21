import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import type { UserProfile } from "@/types";

export function useCurrentUser() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isCancelled = false;
    const load = async () => {
      try {
        const profile = await fetchWithAuth<UserProfile>("/api/v1/me");
        if (!isCancelled) setUser(profile);
      } catch {
        if (!isCancelled) setUser(null);
      } finally {
        if (!isCancelled) setLoading(false);
      }
    };
    load();
    return () => {
      isCancelled = true;
    };
  }, []);

  return { user, loading };
}
