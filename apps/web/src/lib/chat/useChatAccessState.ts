"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { getAccessState, getChatEntitlementState } from "@/lib/chat-client";
import type { AccessStateResponse, ChatEntitlementResponse } from "@/types";

export interface ChatAccessStateResult {
  loading: boolean;
  authRequired: boolean;
  accessState: AccessStateResponse | null;
  entitlement: ChatEntitlementResponse | null;
  error: string | null;
  isApproved: boolean;
  isRestrictedAccount: boolean;
  requiresContribution: boolean;
  canBrowseHistory: boolean;
  canGenerate: boolean;
  refresh: () => Promise<void>;
  markContributionRequired: (message?: string) => void;
}

/**
 * Authoritatively checks student approval status and M2 contribution entitlement.
 * Pending/rejected/suspended users are denied access; approved non-contributors are gated
 * from Chat until contributing a qualifying free-tier cloud credential (history stays safe in DB).
 */
export function useChatAccessState(): ChatAccessStateResult {
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [accessState, setAccessState] = useState<AccessStateResponse | null>(null);
  const [entitlement, setEntitlement] = useState<ChatEntitlementResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setAuthRequired(false);

    try {
      const access = await getAccessState();
      setAccessState(access);

      const approved =
        access.approval_status === "approved" ||
        access.effective_state === "admin" ||
        access.effective_state === "contribution_required";

      if (!approved) {
        setEntitlement(null);
        return;
      }

      try {
        const ent = await getChatEntitlementState();
        setEntitlement(ent);
      } catch (entErr) {
        if (entErr instanceof ApiError && entErr.status === 401) {
          setAuthRequired(true);
          return;
        }
        // Fail closed on generation entitlement if entitlement lookup fails unexpectedly
        setEntitlement({
          eligible: false,
          can_chat: false,
          reason: "Unable to verify chat contribution entitlement.",
        });
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setAuthRequired(true);
        setAccessState(null);
        setEntitlement(null);
      } else {
        setError(err instanceof Error ? err.message : "Failed to verify account access");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const markContributionRequired = useCallback((message?: string) => {
    setEntitlement((prev) => ({
      ...(prev || {}),
      eligible: false,
      can_chat: false,
      reason_code: "CHAT_CONTRIBUTION_REQUIRED",
      reason:
        message ||
        "A qualifying free-tier cloud credential contribution is required to start new chats or generate answers.",
    }));
  }, []);

  const isApproved = Boolean(
    accessState &&
      (accessState.approval_status === "approved" ||
        accessState.effective_state === "admin" ||
        accessState.effective_state === "contribution_required")
  );
  const isRestrictedAccount = Boolean(accessState && !isApproved);
  const canGenerate = isApproved && Boolean(entitlement?.eligible);
  const requiresContribution = isApproved && !canGenerate;
  const canBrowseHistory = canGenerate;

  return {
    loading,
    authRequired,
    accessState,
    entitlement,
    error,
    isApproved,
    isRestrictedAccount,
    requiresContribution,
    canBrowseHistory,
    canGenerate,
    refresh,
    markContributionRequired,
  };
}
