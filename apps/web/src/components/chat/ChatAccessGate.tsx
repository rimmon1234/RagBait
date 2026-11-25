"use client";

import Link from "next/link";
import { AlertCircle, KeyRound, Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import type { ChatAccessStateResult } from "@/lib/chat/useChatAccessState";

interface ChatAccessGateProps {
  access: ChatAccessStateResult;
  children: React.ReactNode;
}

/**
 * Guards protected chat routes against unauthenticated sessions and pending/rejected/suspended
 * accounts. Approved non-contributors pass through so they can browse & manage history.
 */
export function ChatAccessGate({ access, children }: ChatAccessGateProps) {
  if (access.loading) {
    return (
      <div
        className="flex h-[calc(100vh-4rem)] items-center justify-center bg-white"
        role="status"
        aria-label="Verifying chat access"
      >
        <div className="flex flex-col items-center gap-3 text-gray-600">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm font-medium">Checking study chat access...</p>
        </div>
      </div>
    );
  }

  if (access.authRequired) {
    return (
      <div className="flex h-[calc(100vh-4rem)] items-center justify-center bg-gray-50 p-6">
        <div className="max-w-md rounded-2xl border border-gray-200 bg-white p-6 text-center shadow-sm">
          <AlertCircle className="mx-auto h-10 w-10 text-amber-500" />
          <h1 className="mt-3 text-lg font-bold text-gray-900">Session Expired</h1>
          <p className="mt-1.5 text-sm text-gray-600">
            Please sign in again to access your RagBait study conversations.
          </p>
          <Link
            href="/login"
            className="mt-4 inline-flex items-center justify-center rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            Sign In
          </Link>
        </div>
      </div>
    );
  }

  if (access.isRestrictedAccount) {
    const status = access.accessState?.approval_status || access.accessState?.effective_state || "restricted";
    const statusTitle =
      status === "pending"
        ? "Account Pending Approval"
        : status === "rejected"
        ? "Account Access Declined"
        : status === "suspended"
        ? "Account Suspended"
        : "Chat Access Unavailable";

    return (
      <div className="flex h-[calc(100vh-4rem)] items-center justify-center bg-gray-50 p-6">
        <div
          className="max-w-md rounded-2xl border border-amber-200 bg-white p-6 text-center shadow-sm"
          role="alert"
        >
          <ShieldAlert className="mx-auto h-10 w-10 text-amber-600" />
          <h1 className="mt-3 text-lg font-bold text-gray-900">{statusTitle}</h1>
          <p className="mt-2 text-sm text-gray-600 leading-relaxed">
            {status === "rejected"
              ? "Your account access request was declined. Contact an administrator if you believe this is an error."
              : status === "suspended"
              ? "Your account has been suspended. Please contact an administrator for assistance."
              : "Study chat is restricted until your account is approved by an administrator."}
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-3">
            <Link
              href="/access/pending"
              className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
            >
              View Account Status
            </Link>
            <Link
              href="/"
              className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Return Home
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (access.error && !access.isApproved) {
    return (
      <div className="flex h-[calc(100vh-4rem)] items-center justify-center bg-gray-50 p-6">
        <div className="max-w-md rounded-2xl border border-red-200 bg-white p-6 text-center shadow-sm">
          <AlertCircle className="mx-auto h-10 w-10 text-red-500" />
          <h1 className="mt-3 text-lg font-bold text-gray-900">Unable to Load Access State</h1>
          <p className="mt-1.5 text-sm text-gray-600">{access.error}</p>
          <button
            type="button"
            onClick={() => void access.refresh()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            <RefreshCw className="h-4 w-4" />
            <span>Retry</span>
          </button>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

interface ContributionGateBannerProps {
  reason?: string;
}

/**
 * Non-blocking banner for approved students who have not yet satisfied M2 contribution requirements.
 * Allows browsing & managing existing conversation history while explaining why new turns are locked.
 */
export function ContributionGateBanner({ reason }: ContributionGateBannerProps) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-950"
    >
      <div className="flex items-start gap-2.5">
        <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
        <div>
          <p className="font-semibold">
            Chat contribution required to start new conversations or send messages
          </p>
          <p className="text-amber-800">
            {reason ||
              "You can continue reading, renaming, archiving, or deleting your existing conversations."}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Link
          href="/credentials"
          className="rounded-lg bg-amber-700 px-3 py-1.5 font-semibold text-white hover:bg-amber-800"
        >
          Connect OpenRouter Key
        </Link>
        <Link
          href="/access/contribution-required"
          className="rounded-lg border border-amber-300 bg-white px-2.5 py-1.5 font-medium text-amber-900 hover:bg-amber-100"
        >
          Learn More
        </Link>
      </div>
    </div>
  );
}
