import { fetchWithAuth } from "@/lib/api";
import type {
  CancelGenerationResponse,
  ChatConversation,
  ConversationListParams,
  ConversationListResponse,
  CreateConversationRequest,
  FinalizeLocalRequest,
  FinalizeLocalResponse,
  MessageListParams,
  MessageListResponse,
  MessageSourcesResponse,
  PrepareLocalRequest,
  PrepareLocalResponse,
  RenameConversationRequest,
} from "@/types/chat";
import type { AccessStateResponse, ChatEntitlementResponse } from "@/types";

export async function createConversation(
  request: CreateConversationRequest
): Promise<ChatConversation> {
  return fetchWithAuth<ChatConversation>("/api/v1/chat/conversations", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function listConversations(
  params: ConversationListParams = {}
): Promise<ConversationListResponse> {
  const query = new URLSearchParams();
  if (params.subject_id) query.set("subject_id", params.subject_id);
  if (params.status) query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", params.limit.toString());
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.search) query.set("search", params.search);

  const queryString = query.toString();
  const endpoint = `/api/v1/chat/conversations${queryString ? `?${queryString}` : ""}`;
  return fetchWithAuth<ConversationListResponse>(endpoint, {
    method: "GET",
  });
}

export async function getConversation(id: string): Promise<ChatConversation> {
  return fetchWithAuth<ChatConversation>(`/api/v1/chat/conversations/${encodeURIComponent(id)}`, {
    method: "GET",
  });
}

export async function renameConversation(
  id: string,
  request: RenameConversationRequest
): Promise<ChatConversation> {
  return fetchWithAuth<ChatConversation>(`/api/v1/chat/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(request),
  });
}

export async function archiveConversation(id: string): Promise<ChatConversation> {
  return fetchWithAuth<ChatConversation>(
    `/api/v1/chat/conversations/${encodeURIComponent(id)}/archive`,
    {
      method: "POST",
    }
  );
}

export async function restoreConversation(id: string): Promise<ChatConversation> {
  return fetchWithAuth<ChatConversation>(
    `/api/v1/chat/conversations/${encodeURIComponent(id)}/restore`,
    {
      method: "POST",
    }
  );
}

export async function deleteConversation(id: string): Promise<void> {
  return fetchWithAuth<void>(`/api/v1/chat/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getConversationMessages(
  conversationId: string,
  params: MessageListParams = {}
): Promise<MessageListResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", params.limit.toString());
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.direction) query.set("direction", params.direction);

  const queryString = query.toString();
  const endpoint = `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}/messages${
    queryString ? `?${queryString}` : ""
  }`;
  return fetchWithAuth<MessageListResponse>(endpoint, {
    method: "GET",
  });
}

export async function cancelGeneration(
  messageId: string,
  conversationId?: string
): Promise<CancelGenerationResponse> {
  const endpoint = conversationId
    ? `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/cancel`
    : `/api/v1/chat/messages/${encodeURIComponent(messageId)}/cancel`;

  return fetchWithAuth<CancelGenerationResponse>(endpoint, {
    method: "POST",
  });
}

export async function getMessageSources(messageId: string): Promise<MessageSourcesResponse> {
  return fetchWithAuth<MessageSourcesResponse>(
    `/api/v1/chat/messages/${encodeURIComponent(messageId)}/sources`,
    {
      method: "GET",
    }
  );
}

export async function prepareLocalTurn(
  conversationId: string,
  request: PrepareLocalRequest
): Promise<PrepareLocalResponse> {
  return fetchWithAuth<PrepareLocalResponse>(
    `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}/messages/prepare-local`,
    {
      method: "POST",
      body: JSON.stringify(request),
    }
  );
}

export async function finalizeLocalTurn(
  conversationId: string,
  request: FinalizeLocalRequest
): Promise<FinalizeLocalResponse> {
  return fetchWithAuth<FinalizeLocalResponse>(
    `/api/v1/chat/conversations/${encodeURIComponent(conversationId)}/messages/finalize-local`,
    {
      method: "POST",
      body: JSON.stringify(request),
    }
  );
}

export async function getAccessState(): Promise<AccessStateResponse> {
  return fetchWithAuth<AccessStateResponse>("/api/v1/me/access", {
    method: "GET",
  });
}

export async function getChatEntitlementState(): Promise<ChatEntitlementResponse> {
  return fetchWithAuth<ChatEntitlementResponse>("/api/v1/me/chat-entitlement", {
    method: "GET",
  });
}
