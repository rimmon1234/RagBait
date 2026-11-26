"use client";

import Link from "next/link";
import { Cpu, Key, ShieldCheck, ArrowRight } from "lucide-react";

export default function CommunityNodesPage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
        <div className="flex items-center gap-3 text-blue-600">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-50">
            <ShieldCheck className="h-6 w-6 text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              Community Compute Discontinued
            </h1>
            <p className="text-sm text-gray-500">
              RagBait Product Architecture & Privacy Boundary
            </p>
          </div>
        </div>

  useEffect(() => {
    router.replace("/chat");
  }, [router]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center p-6 text-center">
      <Cpu className="h-12 w-12 text-blue-600 mb-4 animate-pulse" />
      <h1 className="text-xl font-bold text-gray-900">Redirecting to AI Chat...</h1>
      <p className="mt-2 text-sm text-gray-600 max-w-md">
        Community peer node sharing has been retired. Use the unified AI Chat workspace for Cloud AI or Private Local Ollama inference.
      </p>
      <Link
        href="/chat"
        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
      >
        Go to AI Chat <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}
