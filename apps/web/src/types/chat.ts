export type ChatConversationStatus = "active" | "archived" | "deleted";

export type ChatMessageRole = "user" | "assistant";

export type ChatMessageStatus = "pending" | "streaming" | "completed" | "failed" | "cancelled";

export type ChatRoutingMode = "auto" | "cloud_only" | "provider_specific";

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

export interface CloudStreamMessageRequest {
  content: string;
  idempotency_key?: string;
  routing_mode?: ChatRoutingMode;
  provider_id?: string | null;
  model_id?: string | null;
  credential_id?: string | null;
  temperature?: number;
  max_output_tokens?: number | null;
}

export interface StreamSourceSummary {
  citation_key: string;
  document_id: string | null;
  document_title: string;
  page_number_start: number;
  page_number_end: number;
  relevance_score: number;
}

export interface AdmissionWaitingPayload {
  message_id: string;
  conversation_id: string;
  queue_position: number;
  estimated_wait_seconds: number;
  status: "queued";
}

export interface GenerationStartedPayload {
  message_id: string;
  user_message_id: string;
  conversation_id: string;
  generation_request_id: string;
  model_id: string | null;
  provider_id: string | null;
  resolved_model_id: string | null;
  streaming: boolean;
  replayed?: boolean;
  started_at: string;
}

export interface SourcesIdentifiedPayload {
  message_id: string;
  conversation_id: string;
  sources: StreamSourceSummary[];
  omitted_count: number;
  retrieval_candidate_count: number;
}

export interface TextDeltaPayload {
  message_id: string;
  delta: string;
}

export interface ContentReplacedPayload {
  message_id: string;
  content: string;
  reason: string;
}

export interface GenerationCompletedPayload {
  message_id: string;
  user_message_id: string;
  conversation_id: string;
  content: string;
  finish_reason: string;
  usage: Record<string, unknown>;
  citation_validation_status: string;
  valid_citations: string[];
  repair_attempted: boolean;
  sources: Record<string, unknown>[];
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  telemetry: Record<string, unknown>;
  replayed?: boolean;
}

export interface GenerationFailedPayload {
  message_id: string;
  code: string;
  message: string;
  retryable: boolean;
}

export interface GenerationCancelledPayload {
  message_id: string;
  conversation_id: string;
  partial_content: string;
  retained_length: number;
  reason: string;
}

export type ChatStreamEvent =
  | { id?: string; event: "admission_waiting"; data: AdmissionWaitingPayload }
  | { id?: string; event: "generation_started"; data: GenerationStartedPayload }
  | { id?: string; event: "sources_identified"; data: SourcesIdentifiedPayload }
  | { id?: string; event: "text_delta"; data: TextDeltaPayload }
  | { id?: string; event: "content_replaced"; data: ContentReplacedPayload }
  | { id?: string; event: "generation_completed"; data: GenerationCompletedPayload }
  | { id?: string; event: "generation_failed"; data: GenerationFailedPayload }
  | { id?: string; event: "generation_cancelled"; data: GenerationCancelledPayload };

export interface CancelGenerationResponse {
  message_id: string;
  conversation_id: string;
  status: "cancelled";
  already_cancelled: boolean;
  retained_length: number;
  reason: string;
}
