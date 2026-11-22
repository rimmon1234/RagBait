"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Cpu, ArrowRight } from "lucide-react";

export default function CommunityNodesPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/local-inference");
  }, [router]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center p-6 text-center">
      <Cpu className="h-12 w-12 text-blue-600 mb-4 animate-pulse" />
      <h1 className="text-xl font-bold text-gray-900">Redirecting to Local Inference...</h1>
      <p className="mt-2 text-sm text-gray-600 max-w-md">
        Community peer node sharing has been retired. Please use private local Ollama inference or cloud API keys.
      </p>
      <Link
        href="/local-inference"
        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
      >
        Go to Local Inference <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}
