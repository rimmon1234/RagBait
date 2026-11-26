"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Key,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import type { ChatEntitlementResponse } from "@/types";

export default function ContributionRequiredPage() {
  const [entitlement, setEntitlement] = useState<ChatEntitlementResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchEntitlement = async () => {
    setLoading(true);
    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (session) {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const res = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/me/chat-entitlement`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        });
        if (res.ok) {
          const data = await res.json();
          setEntitlement(data);
        }
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEntitlement();
  }, []);

  const getReasonExplanation = (reasonCode?: string) => {
    switch (reasonCode) {
      case "NO_CREDENTIAL_CONTRIBUTED":
        return "No qualifying key contributed yet. Please add a supported free-tier API key.";
      case "VALIDATION_REQUIRED":
        return "Key validation required. Please run the validation probe in Key Management.";
      case "SHARING_CONSENT_REQUIRED":
        return "Sharing consent required. Please enable community sharing on your key.";
      case "CREDENTIAL_INACTIVE":
        return "Credential inactive. Please enable your key in Key Management.";
      case "BILLING_VERIFICATION_REQUIRED":
        return "Free-tier verification required. Your key must be verified as free-tier only with no attached billing.";
      case "PAID_CREDENTIAL_INELIGIBLE":
        return "Paid API keys cannot be shared. Only verified free-tier credentials may be contributed.";
      case "BILLING_VERIFICATION_STALE":
        return "Billing verification has expired. Please re-validate your key in Key Management.";
      case "PROVIDER_NOT_ELIGIBLE_FOR_SHARING":
        return "The selected provider is not approved for free-tier community sharing.";
      default:
        return "A qualifying free-tier cloud credential is required to unlock AI Chat.";
    }
  };

  return (
          <div className="flex min-h-[82vh] items-center justify-center px-4 py-12">
            <div className="card-elevated w-full max-w-lg p-8 text-center">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-600 text-white shadow-md shadow-emerald-600/20">
                <CheckCircle2 className="h-8 w-8" />
              </div>

                <h1 className="mt-4 text-2xl font-extrabold tracking-tight text-slate-900">Account Approved!</h1>
                <p className="mt-2 text-sm text-slate-600 leading-relaxed">
                  Your student account has been approved by the platform administrators. You have full access to browse, upload, and read study materials.
                </p>

                <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
                  <Link
                    href="/knowledge-base"
                    className="btn-primary-gradient px-5 py-2.5"
                  >
                    <BookOpen className="h-4 w-4" />
                    Enter Knowledge Base
                  </Link>
                </div>

                {entitlement?.eligible ? (
                  <div className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 p-5 text-left text-sm text-emerald-900">
                    <div className="flex items-center gap-2 font-semibold">
                      <Sparkles className="h-5 w-5 text-emerald-600" />
                      <span>AI Chat Unlocked</span>
                    </div>
                    <p className="mt-2 text-emerald-800">
                      You have contributed {entitlement.eligible_credential_count} active qualifying free-tier API credential to the shared pool.
                      AI Chat and inference features are now unlocked for your account.
                    </p>
                    <div className="mt-4">
                      <Link
                        href="/local-inference"
                        className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-emerald-700"
                      >
                        Launch AI Inference
                        <ArrowRight className="h-4 w-4" />
                      </Link>
                    </div>
                  </div>
                ) : (
                  <div className="mt-6 rounded-lg border border-blue-100 bg-blue-50 p-5 text-left text-sm text-blue-900">
                    <div className="flex items-center gap-2 font-semibold">
                      <Sparkles className="h-5 w-5 text-blue-600" />
                      <span>Milestone M2 Feature Gate</span>
                    </div>
                    <p className="mt-2 text-blue-800 leading-relaxed">
                      <strong>Contribute one eligible free-tier cloud API key to unlock AI Chat.</strong>
                    </p>
                    <p className="mt-2 text-xs text-blue-700 leading-relaxed">
                      RagBait uses a shared pool of eligible free-tier cloud API credentials. To use AI Chat,
                      every participating student must contribute at least one approved free-tier API key for other students to use.
                      Your local Ollama models and paid API keys remain private and do not count as contributions.
                    </p>

                    {entitlement && (
                      <div className="mt-3 flex items-start gap-2 rounded bg-blue-100/70 p-2 text-xs text-blue-900">
                        <AlertCircle className="h-4 w-4 shrink-0 text-blue-600 mt-0.5" />
                        <span>{getReasonExplanation(entitlement.reason_code)}</span>
                      </div>
                    )}

                    <div className="mt-4 flex items-center gap-3">
                      <Link
                        href="/credentials"
                        className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
                      >
                        <Key className="h-4 w-4" />
                        Manage API Credentials
                      </Link>
                      <button
                        type="button"
                        onClick={fetchEntitlement}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm font-medium text-blue-700 shadow-sm hover:bg-blue-50 disabled:opacity-50"
                      >
                        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                        Check Status
                      </button>
                    </div>
                  </div>
                )}

                <div className="mt-8 rounded-lg bg-gray-50 p-4 text-xs text-gray-500">
                  RagBait M0, M1 & M2 Foundation Active • Mandatory Free-Tier Contribution Policy
                </div>
            </div>
          </div>
          );
}
