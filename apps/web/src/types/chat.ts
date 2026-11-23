export type ChatConversationStatus = "active" | "archived" | "deleted";

export type ChatMessageRole = "user" | "assistant";

export type ChatMessageStatus = "pending" | "streaming" | "completed" | "failed" | "cancelled";

export interface ChatConversation {
  id: string;
  user_id: string;
  subject_id: string;
  subject_name: string;
  subject_slug: string;
  title: string;
  status: ChatConversationStatus;
  message_count: number;
  last_message_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  sequence_number: number;
  role: ChatMessageRole;
  content: string;
  status: ChatMessageStatus;
  client_message_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  items: ChatConversation[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface MessageListResponse {
  items: ChatMessage[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface ConversationListParams {
  subject_id?: string;
  status?: ChatConversationStatus;
  limit?: number;
  cursor?: string;
  search?: string;
}

export interface MessageListParams {
  limit?: number;
  cursor?: string;
  direction?: "before" | "after";
}

export interface CreateConversationRequest {
  subject_id: string;
  title?: string;
}

export interface RenameConversationRequest {
  title: string;
}
