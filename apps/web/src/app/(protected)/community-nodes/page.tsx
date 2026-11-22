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

        <div className="mt-6 space-y-4 text-sm text-gray-700 leading-relaxed">
          <div className="rounded-xl bg-gray-50 p-5 border border-gray-100">
            <p className="font-semibold text-gray-900">
              Official Privacy Policy: Private Local Inference Only
            </p>
            <p className="mt-2 text-gray-600">
              A student&apos;s locally installed Ollama models and local computing resources
              remain exclusively available to that student. RagBait strictly prohibits donating,
              pooling, sharing, or exposing local models or Ollama instances for use by other students.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-5">
              <div className="flex items-center gap-2 text-emerald-800 font-semibold mb-2">
                <Cpu className="h-5 w-5 text-emerald-600" />
                Private Local Models
              </div>
              <p className="text-xs text-emerald-700 leading-relaxed">
                Your local Ollama installation runs 100% privately via loopback. Model weights,
                prompts, and completion data never leave your personal computer.
              </p>
              <div className="mt-4">
                <Link
                  href="/local-inference"
                  className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700 hover:text-emerald-900 underline"
                >
                  Go to Local Inference <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </div>

            <div className="rounded-xl border border-purple-100 bg-purple-50/50 p-5">
              <div className="flex items-center gap-2 text-purple-800 font-semibold mb-2">
                <Key className="h-5 w-5 text-purple-600" />
                Cloud API Credential Pool
              </div>
              <p className="text-xs text-purple-700 leading-relaxed">
                Inference resource sharing in RagBait applies exclusively to explicitly consented,
                institutional administrator-approved free-tier cloud provider credentials.
              </p>
              <div className="mt-4">
                <Link
                  href="/credentials"
                  className="inline-flex items-center gap-1.5 text-xs font-semibold text-purple-700 hover:text-purple-900 underline"
                >
                  Manage API Credentials <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
