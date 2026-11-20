/**
 * TypeScript types for Ollama local inference integration.
 * Matches Ollama REST and NDJSON streaming APIs.
 */

export interface OllamaVersionResponse {
  version: string;
}

export interface OllamaModelDetails {
  parent_model?: string;
  format?: string;
  family?: string;
  families?: string[];
  parameter_size?: string;
  quantization_level?: string;
}

export interface OllamaTagModel {
  name: string;
  model: string;
  modified_at: string;
  size: number;
  digest: string;
  details?: OllamaModelDetails;
  capabilities?: string[];
}

export interface OllamaTagsResponse {
  models: OllamaTagModel[];
}

export interface OllamaShowResponse {
  license?: string;
  modelfile?: string;
  parameters?: string;
  template?: string;
  details?: OllamaModelDetails;
  model_info?: Record<string, unknown>;
  capabilities?: string[];
  modified_at?: string;
}

export interface OllamaChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
  images?: string[];
}

export interface OllamaChatOptions {
  temperature?: number;
  top_p?: number;
  top_k?: number;
  num_predict?: number;
  repeat_penalty?: number;
  stop?: string[];
}

export interface OllamaChatRequest {
  model: string;
  messages: OllamaChatMessage[];
  stream?: boolean;
  options?: OllamaChatOptions;
}

export interface OllamaChatChunk {
  model: string;
  created_at: string;
  message?: {
    role: string;
    content: string;
    thinking?: string;
  };
  done: boolean;
  done_reason?: string;
  total_duration?: number;       // nanoseconds
  load_duration?: number;        // nanoseconds
  prompt_eval_count?: number;
  prompt_eval_duration?: number; // nanoseconds
  eval_count?: number;
  eval_duration?: number;        // nanoseconds
}

export interface OllamaUsageStats {
  totalDurationMs: number;
  loadDurationMs: number;
  promptEvalCount: number;
  promptEvalDurationMs: number;
  evalCount: number;
  evalDurationMs: number;
  tokensPerSecond: number;
}

export type IPAddressSpace = "loopback" | "private" | "public";

export interface LoopbackRequestInit extends RequestInit {
  targetAddressSpace?: IPAddressSpace;
}

export type LoopbackPermissionState = "granted" | "prompt" | "denied" | "unsupported";

export type OllamaConnectionState =
  | "checking"
  | "connected"
  | "disconnected"
  | "connection_failed"
  | "cors_blocked"
  | "loopback_permission_required"
  | "loopback_permission_denied"
  | "insecure_context";

export interface OllamaConnectionInfo {
  state: OllamaConnectionState;
  version?: string;
  error?: string;
}
