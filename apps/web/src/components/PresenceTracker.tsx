"use client";

import { useEffect, useRef } from "react";
import { fetchWithAuth } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

export function PresenceTracker() {
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const supabase = createClient();

    const sendPulse = async () => {
      if (document.hidden) return;

      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) return;

      try {
        await fetchWithAuth("/api/v1/me/heartbeat", {
          method: "POST",
          body: JSON.stringify({
            client_info: {
              userAgent: navigator.userAgent,
              platform: navigator.platform,
            },
          }),
        });
      } catch {
        // Silently catch heartbeat errors to avoid disrupting UI
      }
    };

    // Initial pulse
    sendPulse();

    // Pulse every 60 seconds
    timerRef.current = setInterval(sendPulse, 60000);

    const handleVisibilityChange = () => {
      if (!document.hidden) {
        sendPulse();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return null;
}
