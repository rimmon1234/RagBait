"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Cpu, ArrowRight } from "lucide-react";

export default function CommunityNodesPage() {
  const router = useRouter();

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
