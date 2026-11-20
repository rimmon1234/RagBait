import Link from "next/link";
import { CheckCircle2, Key, Sparkles } from "lucide-react";

export default function ContributionRequiredPage() {
  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-8 text-center shadow-sm">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-green-100 text-green-600">
          <CheckCircle2 className="h-8 w-8" />
        </div>

        <h1 className="mt-4 text-2xl font-bold text-gray-900">Account Approved!</h1>
        <p className="mt-2 text-sm text-gray-600">
          Your student account has been approved by the platform administrators.
        </p>

        <div className="mt-6 rounded-lg border border-blue-100 bg-blue-50 p-5 text-left text-sm text-blue-900">
          <div className="flex items-center gap-2 font-semibold">
            <Sparkles className="h-5 w-5 text-blue-600" />
            <span>Milestone M2 Feature Gate</span>
          </div>
          <p className="mt-2 text-blue-800">
            Interactive AI study features and subject-scoped RAG queries require AI provider credentials.
            Contribute your own provider API keys to unlock platform inference.
          </p>
          <div className="mt-4">
            <Link
              href="/credentials"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
            >
              <Key className="h-4 w-4" />
              Manage API Credentials
            </Link>
          </div>
        </div>

        <div className="mt-8 rounded-lg bg-gray-50 p-4 text-xs text-gray-500">
          RagBait M0, M1 & M2 Foundation Active • Secure Owner-Only Credential Vault
        </div>
      </div>
    </div>
  );
}
