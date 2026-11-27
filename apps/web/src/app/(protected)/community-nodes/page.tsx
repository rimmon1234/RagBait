"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Cpu } from "lucide-react";

export default function CommunityNodesPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/chat");
  }, [router]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center p-6 text-center">
      <Cpu className="mb-4 h-12 w-12 animate-pulse text-indigo-600" />
      <h1 className="text-xl font-bold text-slate-900">Redirecting to AI Chat...</h1>
      <p className="mt-2 max-w-md text-sm text-slate-600">
        Community peer node sharing will not be implemented. Use the unified AI Chat workspace for Cloud AI or Private Local Ollama inference.
      </p>
      <Link
        href="/chat"
        className="btn-primary-gradient mt-4 px-4 py-2"
      >
        Go to AI Chat <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}
