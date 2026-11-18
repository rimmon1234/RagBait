"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Clock, RefreshCw } from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import type { AccessStateResponse } from "@/types";

export default function PendingAccessPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);

  const checkStatus = async () => {
    setChecking(true);
    setStatusText(null);
    try {
      const res = await fetchWithAuth<AccessStateResponse>("/api/v1/me/access");
      if (res.effective_state === "contribution_required") {
        router.push("/access/contribution-required");
      } else if (res.effective_state === "admin") {
        router.push("/admin");
      } else if (res.effective_state === "rejected") {
        router.push("/access/rejected");
      } else {
        setStatusText("Your request is still pending administrator review.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to check status";
      setStatusText(msg);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-8 text-center shadow-sm">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-amber-100 text-amber-600">
          <Clock className="h-8 w-8" />
        </div>

        <h1 className="mt-4 text-2xl font-bold text-gray-900">Access Request Pending</h1>
        <p className="mt-2 text-sm text-gray-600">
          Your email has been confirmed and your access request has been submitted to the platform administrators.
        </p>

        <div className="mt-6 rounded-lg bg-gray-50 p-4 text-left text-sm text-gray-700">
          <h2 className="font-semibold text-gray-900">What happens next?</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-gray-600">
            <li>An administrator will review your college enrollment and request.</li>
            <li>You will receive full access to shared study materials once approved.</li>
            <li>You do not need to resubmit your application.</li>
          </ul>
        </div>

        {statusText && (
          <p className="mt-4 text-sm font-medium text-blue-600">{statusText}</p>
        )}

        <div className="mt-6">
          <button
            onClick={checkStatus}
            disabled={checking}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${checking ? "animate-spin" : ""}`} />
            {checking ? "Checking Status..." : "Refresh Approval Status"}
          </button>
        </div>
      </div>
    </div>
  );
}
